from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "holycrab_cli.py"
SPEC = importlib.util.spec_from_file_location("holycrab_v041", SCRIPT)
assert SPEC and SPEC.loader
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


def jpeg(path: Path, marker: bytes = b"a") -> None:
    path.write_bytes(b"\xff\xd8\xff\xe0" + marker)


class V041Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {
            "HOLYCRAB_CONFIG_DIR": self.temp.name,
            "HOLYCRAB_API_KEY": "test-secret",
            "HOLYCRAB_NO_UPDATE_CHECK": "1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    @staticmethod
    def image_request() -> dict[str, object]:
        return {"model": "seedream-5-0-lite-260128", "prompt": "draw", "size": "2K"}

    def test_every_ambiguous_create_outcome_is_unknown_and_never_retried(self) -> None:
        outcomes = [
            (408, {"code": 408, "message": "timeout"}),
            (502, {"code": 502, "message": "gateway"}),
            (200, "not-json-object"),
            (200, {"code": 200, "data": {"step": 0}}),
            urllib.error.URLError("connection lost"),
        ]
        for index, outcome in enumerate(outcomes):
            with self.subTest(outcome=outcome):
                attempt = f"attempt{index}"
                replies = [(200, {"code": 200, "data": {"frozenCredit": 5}}), outcome]
                with patch.object(cli, "send", side_effect=replies) as send:
                    result = cli.create_generation("image", self.image_request(), confirmed=True,
                                                   attempt_id=attempt)
                self.assertEqual(result["state"], "unknown")
                self.assertEqual(send.call_count, 2)
                self.assertEqual(cli.attempt_record(attempt)["state"], "unknown")
                self.assertIn("cannot prove", result["nextAction"]["instruction"])
                with patch.object(cli, "send") as second:
                    with self.assertRaisesRegex(SystemExit, "already exists"):
                        cli.create_generation("image", self.image_request(), confirmed=True,
                                              attempt_id=attempt)
                second.assert_not_called()

    def test_explicit_business_rejection_is_failed(self) -> None:
        replies = [(200, {"code": 200, "data": {"frozenCredit": 5}}),
                   (400, {"code": 400, "message": "bad request"})]
        with patch.object(cli, "send", side_effect=replies):
            result = cli.create_generation("image", self.image_request(), confirmed=True,
                                           attempt_id="rejected1")
        self.assertEqual(result["state"], "failed")
        self.assertEqual(cli.attempt_record("rejected1")["state"], "failed")

    def test_attempt_commands_and_mcp_are_read_only(self) -> None:
        cli.reserve_attempt("one-safe_id", {"attemptId": "one-safe_id", "state": "prepared", "createdAt": cli.utc_now()})
        self.assertEqual(cli.attempt_record("one-safe_id")["state"], "prepared")
        self.assertEqual(cli.mcp_tool_call("generation_attempt_list", {})["attempts"][0]["attemptId"], "one-safe_id")
        parser = cli.build_parser()
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(parser.parse_args(["generate", "attempts", "get", "one-safe_id"]).func(
                parser.parse_args(["generate", "attempts", "get", "one-safe_id"])), 0)
        self.assertEqual(json.loads(output.getvalue())["attemptId"], "one-safe_id")

    def test_invalid_attempt_state_recovers_to_unknown_without_resubmission(self) -> None:
        cli.write_private_json(cli.attempts_path(), {
            "damaged": {"attemptId": "damaged", "state": "not-a-real-state"},
        })
        recovered = cli.load_attempts()["damaged"]
        self.assertEqual(recovered["state"], "unknown")
        self.assertIn("do not resubmit", recovered["note"])

    @unittest.skipIf(os.name == "nt", "POSIX permission enforcement")
    def test_attempts_and_installation_manifest_reject_unsafe_permissions(self) -> None:
        cli.attempts_path().write_text("{}", encoding="utf-8")
        cli.attempts_path().chmod(0o644)
        with self.assertRaisesRegex(SystemExit, "Unsafe permissions"):
            cli.load_attempts()
        cli.attempts_path().chmod(0o600)

        manifest = Path(self.temp.name) / "installation.json"
        manifest.write_text("{}", encoding="utf-8")
        manifest.chmod(0o644)
        with patch.object(cli, "installation_path", return_value=manifest), \
                self.assertRaisesRegex(SystemExit, "Unsafe permissions"):
            cli.load_installation()
        self.assertEqual(stat.S_IMODE(manifest.stat().st_mode), 0o644)

    def test_update_startup_runs_local_health_without_duplicate_online_check(self) -> None:
        with patch.object(cli, "local_health_report", return_value={"repairs": []}) as health, \
                patch.object(cli, "check_for_update") as update:
            cli.startup_maintenance(["update", "--check"])
        health.assert_called_once_with(online=False)
        update.assert_not_called()

    def test_current_contract_validation(self) -> None:
        lite = {"model": "seedream-5-0-lite-260128", "prompt": "x", "size": "2001x2000"}
        cli.normalize_generation_request("image", lite)
        with self.assertRaisesRegex(SystemExit, "multiples of 16"):
            cli.normalize_generation_request("image", {"model": "seedream-5-0-pro-260628",
                                                        "prompt": "x", "size": "1201x800"})
        h3 = {"model": "MiniMax-H3", "prompt": "x", "duration": 5, "resolution": "768P",
              "imageAssetIds": ["asset1"]}
        self.assertEqual(cli.normalize_generation_request("video", h3)["ratio"], "adaptive")
        with self.assertRaisesRegex(SystemExit, "concrete ratio"):
            cli.normalize_generation_request("video", {"model": "MiniMax-H3", "prompt": "x",
                                                        "duration": 5, "resolution": "768P", "ratio": "adaptive"})
        h3_frame = cli.normalize_generation_request("video", {
            "model": "MiniMax-H3", "prompt": "x", "duration": 5, "resolution": "768P",
            "ratio": "16:9", "firstFrameAssetId": "frame1",
        })
        self.assertEqual(h3_frame["ratio"], "adaptive")
        h3_last_frame = cli.normalize_generation_request("video", {
            "model": "MiniMax-H3", "prompt": "x", "duration": 5, "resolution": "768P",
            "ratio": "16:9", "lastFrameAssetId": "frame2",
        })
        self.assertEqual(h3_last_frame["ratio"], "adaptive")
        with self.assertRaisesRegex(SystemExit, "require firstFrameAssetId"):
            cli.normalize_generation_request("video", {
                "model": "seedance-2-0", "prompt": "x", "duration": 5,
                "resolution": "720p", "videoTaskType": "frames",
            })
        with self.assertRaisesRegex(SystemExit, "require videoTaskType"):
            cli.normalize_generation_request("video", {
                "model": "dreamina-seedance-2-5-260628", "prompt": "x", "duration": 5,
                "resolution": "720p", "videoAssetIds": ["video1"],
            })
        image = cli.normalize_generation_request("image", {
            "model": "seedream-5-0-lite-260128", "prompt": "x", "size": "2K",
            "aspectRatio": "16:9", "resolution": "2k", "outputFormat": "png",
        })
        self.assertEqual(image["resolution"], "2k")

    def test_audio_ids_are_exposed_and_downloadable_as_urls(self) -> None:
        task = cli.public_task_data({"uniqId": "task", "audioIds": '["asset", "https://cdn.example/a.mp3"]'})
        self.assertEqual(task["audioUrls"], ["https://cdn.example/a.mp3"])
        self.assertEqual(cli.parse_audio_urls("bad json"), [])
        self.assertEqual(cli.parse_audio_urls(None), [])
        self.assertEqual(cli.task_output_urls({"audioIds": '["https://cdn.example/a.mp3", "https://cdn.example/b.ogg"]'}),
                         ["https://cdn.example/a.mp3", "https://cdn.example/b.ogg"])

    def test_audio_reference_combinations_follow_current_capability(self) -> None:
        valid = cli.normalize_generation_request("audio", {
            "textPrompt": "@Audio1 says hello",
            "references": [{"audio_url": "https://cdn.example/voice.ogg"}],
            "audioConfig": {"format": "ogg_opus", "sample_rate": 48000},
        })
        self.assertEqual(valid["audioConfig"]["format"], "ogg_opus")
        for references in (
            [{"audio_url": "https://cdn.example/a.wav", "image_url": "https://cdn.example/a.png"}],
            [{"audio_url": "https://cdn.example/a.wav"}, {"image_url": "https://cdn.example/a.png"}],
            [{"image_url": "https://cdn.example/a.png"}, {"image_url": "https://cdn.example/b.png"}],
        ):
            with self.subTest(references=references), self.assertRaises(SystemExit):
                cli.normalize_generation_request("audio", {"textPrompt": "hello", "references": references})
        with self.assertRaisesRegex(SystemExit, "mention every audio reference"):
            cli.normalize_generation_request("audio", {
                "textPrompt": "@Audio1 only",
                "references": [{"audio_url": "https://cdn.example/a.wav"},
                               {"audio_url": "https://cdn.example/b.wav"}],
            })
        with self.assertRaisesRegex(SystemExit, "without audio references"):
            cli.normalize_generation_request("audio", {"textPrompt": "imitate @Audio1"})
        with self.assertRaisesRegex(SystemExit, "Image mentions"):
            cli.normalize_generation_request("audio", {
                "textPrompt": "describe @Image1",
                "references": [{"image_url": "https://cdn.example/a.png"}],
            })

    def test_task_filters_are_forwarded_and_dates_are_paired(self) -> None:
        args = argparse.Namespace(page=1, page_size=20, start_date="2026-09-01",
                                  end_date="2026-09-14", type="AUDIO")
        with patch.object(cli, "send", return_value=(200, {"code": 200, "data": {"records": []}})) as send, \
                redirect_stdout(io.StringIO()):
            cli.command_task_list(args)
        self.assertEqual(send.call_args.kwargs["query"][-3:], [
            ("startDate", "2026-09-01"), ("endDate", "2026-09-14"), ("taskType", "AUDIO")])
        args.end_date = None
        with self.assertRaisesRegex(ValueError, "supplied together"):
            cli.command_task_list(args)

    def test_download_rejects_local_network_and_existing_output(self) -> None:
        for value in ("https://127.0.0.1/a", "https://[::1]/a", "https://localhost/a",
                      "https://example.com/a#fragment", "https://user:pass@example.com/a"):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                cli.validate_download_url(value)
        output = Path(self.temp.name) / "exists.bin"
        output.write_bytes(b"keep")
        with patch.object(cli, "send", return_value=(200, {"code": 200, "data": {
            "uniqId": "task", "videoUrl": "https://cdn.example/a.mp4"}})), patch.object(cli, "open_download") as open_:
            with self.assertRaisesRegex(SystemExit, "already exists"):
                cli.command_download(argparse.Namespace(uniq_id="task", output=str(output), index=0, force=False))
        open_.assert_not_called()
        self.assertEqual(output.read_bytes(), b"keep")

    def test_download_revalidates_redirects_and_cleans_partial_files(self) -> None:
        request = cli.urllib.request.Request("https://cdn.example/result.mp4")
        with self.assertRaises(SystemExit):
            cli.SecureDownloadRedirectHandler().redirect_request(
                request, None, 302, "Found", {}, "https://127.0.0.1/private"
            )
        destination = Path(self.temp.name) / "result.mp4"
        response = MagicMock()
        response.__enter__.return_value.headers = {}
        response.__enter__.return_value.read.side_effect = [b"12345", b"67890"]
        backend = (200, {"code": 200, "data": {"uniqId": "task", "videoUrl": "https://cdn.example/result.mp4"}})
        with patch.object(cli, "send", return_value=backend), patch.object(cli, "open_download", return_value=response), \
                patch.object(cli, "MAX_DOWNLOAD_BYTES", 8):
            with self.assertRaisesRegex(SystemExit, "size limit"):
                cli.command_download(argparse.Namespace(uniq_id="task", output=str(destination), index=0, force=False))
        self.assertFalse(destination.exists())
        self.assertEqual(list(destination.parent.glob(f".{destination.name}.*.part")), [])

    def test_upload_prepare_is_local_one_shot_and_detects_replacement(self) -> None:
        first = Path(self.temp.name) / "first.jpg"
        jpeg(first)
        plan = cli.prepare_upload_plan([str(first)])
        self.assertTrue(plan["confirmationRequired"])
        self.assertNotIn("apiKey", json.dumps(plan))
        first.write_bytes(b"\xff\xd8\xff\xe0changed")
        with patch.object(cli, "send") as send:
            with self.assertRaisesRegex(SystemExit, "changed after confirmation"):
                cli.execute_upload_plan(plan["uploadPlanId"], confirmed=True)
        send.assert_not_called()

    def test_upload_cancel_and_removed_tool_never_send_bytes(self) -> None:
        first = Path(self.temp.name) / "first.jpg"
        jpeg(first)
        plan = cli.prepare_upload_plan([str(first)])
        with patch.object(cli, "send") as send, patch.object(cli, "open_presigned_upload") as put:
            with self.assertRaisesRegex(ValueError, "confirmed must be true"):
                cli.execute_upload_plan(plan["uploadPlanId"], confirmed=False)
            with self.assertRaisesRegex(SystemExit, "Unknown MCP tool: asset_upload"):
                cli.mcp_tool_call("asset_upload", {"file": str(first)})
        send.assert_not_called()
        put.assert_not_called()
        args = argparse.Namespace(file=[str(first)], real_human_group=None,
                                  duration_seconds=None, yes=False)
        with patch.object(cli, "send") as send, patch.object(cli, "open_presigned_upload") as put, \
                patch.object(cli.sys.stdin, "isatty", return_value=False), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.command_upload_asset(args), 2)
        send.assert_not_called()
        put.assert_not_called()

    def test_upload_rejects_special_forged_and_oversized_files_before_network(self) -> None:
        forged = Path(self.temp.name) / "forged.jpg"
        forged.write_bytes(b"not-a-jpeg")
        with self.assertRaisesRegex(SystemExit, "Unsupported or unrecognized"):
            cli.inspect_upload_file(str(forged), real_human=True)
        valid = Path(self.temp.name) / "valid.jpg"
        jpeg(valid)
        with self.assertRaisesRegex(SystemExit, "between 1 and 10"):
            cli.prepare_upload_plan([str(valid)] * 11)
        oversized = Path(self.temp.name) / "large.jpg"
        jpeg(oversized)
        with oversized.open("r+b") as stream:
            stream.truncate(cli.MAX_IMAGE_UPLOAD_BYTES)
        with self.assertRaisesRegex(SystemExit, "too large"):
            cli.inspect_upload_file(str(oversized), real_human=True)
        if hasattr(os, "mkfifo"):
            fifo = Path(self.temp.name) / "pipe.jpg"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(SystemExit, "regular file"):
                cli.inspect_upload_file(str(fifo), real_human=True)
        parser = cli.build_parser()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["assets", "upload", str(valid), "--content-type", "image/jpeg"])

    def test_upload_batch_stops_at_first_unknown(self) -> None:
        files = [Path(self.temp.name) / f"{name}.jpg" for name in ("a", "b", "c")]
        for path in files:
            jpeg(path, path.name.encode())
        plan = cli.prepare_upload_plan([str(path) for path in files])
        results = [
            {"assetUniqId": "asset1", "state": "uploaded", "ready": False},
            {"assetUniqId": "asset2", "state": "unknown"},
        ]
        with patch.object(cli, "upload_prepared_file", side_effect=results) as upload:
            result = cli.execute_upload_plan(plan["uploadPlanId"], confirmed=True)
        self.assertEqual(upload.call_count, 2)
        self.assertEqual(len(result["uploaded"]), 1)
        self.assertEqual(result["failedOrUnknown"]["state"], "unknown")
        self.assertEqual(len(result["notAttempted"]), 1)

    def test_upload_presign_classifies_rejection_and_ambiguity_without_put(self) -> None:
        source = Path(self.temp.name) / "clip.mp4"
        source.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"0" * 20)
        item = cli.inspect_upload_file(str(source), real_human=True, duration_seconds=8)
        for status, expected in ((400, "failed"), (408, "unknown"), (503, "unknown")):
            with self.subTest(status=status), patch.object(
                cli, "send", return_value=(status, {"code": status, "message": "no"})
            ) as send, patch.object(cli, "open_presigned_upload") as put:
                result = cli.upload_prepared_file(item, "group1")
            self.assertEqual(result["state"], expected)
            self.assertEqual(send.call_count, 1)
            put.assert_not_called()

    def test_unknown_group_upload_without_asset_id_keeps_group_recovery_command(self) -> None:
        source = Path(self.temp.name) / "first.jpg"
        jpeg(source)
        group = {"uniqId": "group1", "name": "Lin"}
        with patch.object(cli, "find_real_human_group", return_value=group):
            plan = cli.prepare_upload_plan([str(source)], group_uniq_id="group1")
        with patch.object(cli, "upload_prepared_file", return_value={"state": "unknown", "phase": "presign"}):
            result = cli.execute_upload_plan(plan["uploadPlanId"], confirmed=True)
        self.assertEqual(result["failedOrUnknown"]["groupUniqId"], "group1")
        self.assertEqual(result["nextAction"]["code"], "QUERY_GROUP_ASSETS")
        self.assertIn("group1", result["nextAction"]["command"])

    def test_asset_processing_states_wait_and_failed_state_shows_public_error(self) -> None:
        for step in ("UPLOADED", "UPLOADING_TO_ARK", "GETTING_UPLOADED_RESULT"):
            with self.subTest(step=step):
                self.assertEqual(cli.asset_next_action("asset1", step)["code"], "WAIT_FOR_ASSET")
        failed = cli.asset_next_action("asset1", "FAILED", "codec rejected")
        self.assertIn("codec rejected", failed["instruction"])

    def test_duration_is_positive_and_not_sent_for_images(self) -> None:
        source = Path(self.temp.name) / "first.jpg"
        jpeg(source)
        self.assertIsNone(cli.inspect_upload_file(str(source), real_human=True, duration_seconds=8)["durationSeconds"])
        with self.assertRaisesRegex(SystemExit, "positive"):
            cli.inspect_upload_file(str(source), real_human=True, duration_seconds=0)

    def test_real_human_statuses_always_include_next_action(self) -> None:
        authorization = "a" * 32
        group = "b" * 32
        fixtures = {
            "CREATED": {"authorizationId": authorization, "status": "CREATED"},
            "SUCCEEDED": {"authorizationId": authorization, "status": "SUCCEEDED",
                          "group": {"uniqId": group, "name": "Lin"}},
            "FAILED": {"authorizationId": authorization, "status": "FAILED"},
            "EXPIRED": {"authorizationId": authorization, "status": "EXPIRED"},
        }
        for status, data in fixtures.items():
            with self.subTest(status=status), patch.object(cli, "send", return_value=(200, {"code": 200, "data": data})):
                result = cli.get_authorization(authorization)
            self.assertIn("nextAction", result)
            self.assertIsInstance(result["nextAction"]["instruction"], str)

    def test_update_rejects_unstable_versions_and_missing_digest(self) -> None:
        with self.assertRaises(ValueError):
            cli.release_update_info({"tag_name": "v0.4.4-rc1", "draft": False, "prerelease": True,
                                     "html_url": cli.RELEASE_PAGE_PREFIX + "v0.4.4-rc1"})
        installer_name = "install.ps1" if os.name == "nt" else "install.sh"
        with self.assertRaisesRegex(SystemExit, "missing its GitHub SHA-256"):
            cli.release_installer({"tag_name": "v0.4.4", "assets": [{"name": installer_name, "browser_download_url":
                f"https://github.com/AstroxNetwork/skills/releases/download/v0.4.4/{installer_name}", "digest": None}]})
        older = cli.release_update_info({"tag_name": "v0.4.0", "draft": False, "prerelease": False,
                                         "html_url": cli.RELEASE_PAGE_PREFIX + "v0.4.0"})
        self.assertFalse(older["updateAvailable"])
        bad_asset_name = "install.ps1" if os.name == "nt" else "install.sh"
        with self.assertRaisesRegex(SystemExit, "invalid download URL"):
            cli.release_installer({"tag_name": "v0.4.4", "assets": [{"name": bad_asset_name,
                "browser_download_url": "https://evil.example/install", "digest": "sha256:" + "0" * 64}]})

    def test_update_cache_limits_automatic_checks_to_once_per_day(self) -> None:
        release = {"checkedAt": cli.utc_now(), "latestVersion": "0.4.4", "updateAvailable": True,
                   "releasePage": cli.RELEASE_PAGE_PREFIX + "v0.4.4", "release": {"tag_name": "v0.4.4"}}
        with patch.dict(os.environ, {"HOLYCRAB_NO_UPDATE_CHECK": "0"}), \
                patch.object(cli, "fetch_latest_release", return_value=release) as fetch:
            first = cli.check_for_update()
            second = cli.check_for_update()
        self.assertTrue(first["updateAvailable"])
        self.assertEqual(second["latestVersion"], "0.4.4")
        fetch.assert_called_once()

    def test_failed_automatic_update_check_is_also_cached_for_one_day(self) -> None:
        with patch.dict(os.environ, {"HOLYCRAB_NO_UPDATE_CHECK": "0"}), \
                patch.object(cli, "fetch_latest_release", side_effect=urllib.error.URLError("offline")) as fetch:
            first = cli.check_for_update()
            second = cli.check_for_update()
        self.assertFalse(first["updateAvailable"])
        self.assertEqual(second, first)
        fetch.assert_called_once()

    def test_update_requires_confirmation_and_verifies_digest_before_execution(self) -> None:
        name = "install.ps1" if os.name == "nt" else "install.sh"
        marker = '$Version = "v0.4.4"\n' if os.name == "nt" else "VERSION=v0.4.4\n"
        body = marker.encode()
        release = {"tag_name": "v0.4.4", "assets": [{"name": name,
            "browser_download_url": f"https://github.com/AstroxNetwork/skills/releases/download/v0.4.4/{name}",
            "digest": "sha256:" + cli.hashlib.sha256(body).hexdigest()}]}
        state = {"release": release}
        args = argparse.Namespace(check=False, yes=False)
        with patch.object(cli, "check_for_update", return_value={"latestVersion": "0.4.4", "updateAvailable": True}), \
                patch.object(cli, "read_update_state", return_value=state), \
                patch.object(cli.sys.stdin, "isatty", return_value=False), \
                patch.object(cli, "run_update") as run, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.command_update(args), 2)
        run.assert_not_called()

        bad_release = json.loads(json.dumps(release))
        bad_release["assets"][0]["digest"] = "sha256:" + "0" * 64
        remote = MagicMock()
        remote.__enter__.return_value.read.side_effect = [body, b""]
        with patch.object(cli, "open_download", return_value=remote), patch.object(cli.subprocess, "run") as run:
            with self.assertRaisesRegex(SystemExit, "digest mismatch"):
                cli.run_update(bad_release)
        run.assert_not_called()

        remote = MagicMock()
        remote.__enter__.return_value.read.side_effect = [body, b""]
        completed = MagicMock(returncode=0)
        with patch.dict(os.environ, {"HOLYCRAB_INSTALL_SOURCE_DIR": "/unsafe/source", "HOLYCRAB_INSTALL_REF": "a" * 40}), \
                patch.object(cli, "open_download", return_value=remote), \
                patch.object(cli, "load_installation", return_value={"prefix": "/chosen", "agents": ["codex"], "mcp": False}), \
                patch.object(cli.subprocess, "run", return_value=completed) as run:
            cli.run_update(release)
        self.assertNotIn("HOLYCRAB_INSTALL_SOURCE_DIR", run.call_args.kwargs["env"])
        self.assertNotIn("HOLYCRAB_INSTALL_REF", run.call_args.kwargs["env"])
        self.assertEqual(run.call_args.kwargs["env"]["HOLYCRAB_INSTALL_PREFIX"], "/chosen")

    def test_corrupt_update_cache_is_discarded_and_online_doctor_checks_selected_mcp(self) -> None:
        cli.update_state_path().write_text("{truncated", encoding="utf-8")
        self.assertEqual(cli.read_update_state(), {})
        repo = SCRIPT.parents[2]
        core_paths = [
            SCRIPT,
            SCRIPT.parent.parent / "references" / "capabilities.json",
            SCRIPT.parent / "vendor" / "segno-1.6.6-py3-none-any.whl",
            SCRIPT.parent / "vendor" / "LICENSE.segno",
            repo / "bin" / "holycrab",
        ]
        manifest = {"version": cli.VERSION, "prefix": "/opt/holycrab", "agents": ["codex"],
                    "mcp": True, "coreFiles": [
                        {"path": str(path), "sha256": cli.sha256_stream(path)} for path in core_paths
                    ]}
        with patch.object(cli, "load_installation", return_value=manifest), \
                patch.object(cli, "mcp_registration_check", return_value={"ok": True}) as mcp, \
                patch.object(cli, "send", return_value=(200, {"code": 200, "data": {"username": "Fixture", "credit": 10}})), \
                patch.object(cli, "check_for_update", return_value={"latestVersion": cli.VERSION,
                                                                      "updateAvailable": False}):
            report = cli.local_health_report(online=True)
        self.assertTrue(report["ok"])
        self.assertEqual(report["checks"]["mcpRegistrations"]["codex"], {"ok": True})
        expected = str(Path("/opt/holycrab") / (
            "lib/holycrab/holycrab_cli.py" if os.name == "nt" else "bin/holycrab"
        ))
        mcp.assert_called_once_with("codex", expected)


if __name__ == "__main__":
    unittest.main()
