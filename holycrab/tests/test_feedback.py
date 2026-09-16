from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "holycrab_cli.py"
SPEC = importlib.util.spec_from_file_location("holycrab_feedback", SCRIPT)
assert SPEC and SPEC.loader
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


class TerminalBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


def json_documents(raw: str) -> list[object]:
    documents = []
    decoder = json.JSONDecoder()
    remaining = raw.lstrip()
    while remaining:
        value, end = decoder.raw_decode(remaining)
        documents.append(value)
        remaining = remaining[end:].lstrip()
    return documents


def ok(data: object) -> tuple[int, dict[str, object]]:
    return 200, {"code": 200, "data": data}


class FeedbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.environment = patch.dict(os.environ, {
            "HOLYCRAB_CONFIG_DIR": self.temp.name,
            "HOLYCRAB_API_KEY": "test-feedback-key",
            "HOLYCRAB_NO_UPDATE_CHECK": "1",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    @staticmethod
    def request() -> dict[str, object]:
        return {"model": "seedream-5-0-lite-260128", "prompt": "fixture", "size": "2K"}

    def invoke(self, argv: list[str], *, terminal: bool = True) -> tuple[int, str, str]:
        output = io.StringIO()
        errors = TerminalBuffer() if terminal else io.StringIO()
        with patch.object(cli.sys, "argv", ["holycrab", *argv]), \
                patch.object(cli, "startup_maintenance"), \
                redirect_stdout(output), redirect_stderr(errors):
            code = cli.main()
        return code, output.getvalue(), errors.getvalue()

    def test_setup_acknowledges_input_before_verifying_and_guides_to_agent(self) -> None:
        output, errors = io.StringIO(), TerminalBuffer()

        def verify(*args: object, **kwargs: object) -> tuple[int, dict[str, object]]:
            self.assertIn("Verifying", errors.getvalue())
            self.assertNotIn("saved", output.getvalue())
            return ok({"username": "fixture", "credit": 10})

        with patch.object(cli.getpass, "getpass", return_value="hidden-test-key"), \
                patch.object(cli, "send", side_effect=verify), \
                redirect_stdout(output), redirect_stderr(errors):
            cli.command_set_key(argparse.Namespace(stdin=False, no_verify=False))
        self.assertIn("connected", errors.getvalue())
        self.assertIn("Codex", errors.getvalue())
        self.assertNotIn("hidden-test-key", output.getvalue() + errors.getvalue())

    def test_invalid_key_does_not_print_success(self) -> None:
        with patch.object(cli.getpass, "getpass", return_value="invalid-test-key"), \
                patch.object(cli, "send", return_value=(401, {"code": 401})), \
                redirect_stdout(io.StringIO()) as output, redirect_stderr(TerminalBuffer()) as errors:
            with self.assertRaises(SystemExit):
                cli.command_set_key(argparse.Namespace(stdin=False, no_verify=False))
        self.assertNotIn("saved", output.getvalue())
        self.assertNotIn("connected", errors.getvalue())

    def test_estimate_feedback_precedes_network_and_stdout_remains_json(self) -> None:
        errors = TerminalBuffer()

        def estimate(*args: object, **kwargs: object) -> tuple[int, dict[str, object]]:
            self.assertIn("Estimating", errors.getvalue())
            return ok({"frozenCredit": 5})

        output = io.StringIO()
        with patch.object(cli, "send", side_effect=estimate), redirect_stdout(output), redirect_stderr(errors):
            cli.command_generation_estimate(argparse.Namespace(kind="image", json=json.dumps(self.request())))
        self.assertIsNone(json.loads(output.getvalue())["nextAction"]["command"])

    def test_noninteractive_command_has_no_progress_noise(self) -> None:
        with patch.object(cli, "send", return_value=ok({"credit": 10})):
            code, output, errors = self.invoke(["credits", "balance"], terminal=False)
        self.assertEqual(code, 0)
        json.loads(output)
        self.assertEqual(errors, "")

    def test_help_version_and_invalid_arguments_do_not_run_maintenance(self) -> None:
        for argv, expected in ((["--help"], 0), (["--version"], 0), (["tasks", "get"], 2)):
            with self.subTest(argv=argv), patch.object(cli.sys, "argv", ["holycrab", *argv]), \
                    patch.object(cli, "startup_maintenance") as maintenance, \
                    patch.object(cli, "cleanup_authorization_qrs") as qrs, \
                    patch.object(cli, "cleanup_upload_plans") as plans, \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as stopped:
                cli.main()
            self.assertEqual(stopped.exception.code, expected)
            maintenance.assert_not_called()
            qrs.assert_not_called()
            plans.assert_not_called()

    def test_task_wait_only_emits_changes_and_final_timeout(self) -> None:
        replies = [ok({"uniqId": "task1", "step": 1})] * 3
        with patch.object(cli, "send", side_effect=replies), \
                patch.object(cli.time, "monotonic", side_effect=[0, 0, 1, 2]), \
                patch.object(cli.time, "sleep"):
            code, output, errors = self.invoke(["tasks", "wait", "task1", "--timeout", "2", "--interval", "1"])
        documents = json_documents(output)
        self.assertEqual(code, 2)
        self.assertEqual(len(documents), 2)
        self.assertTrue(documents[-1]["timedOut"])
        self.assertIn("task1", documents[-1]["nextAction"]["command"])
        self.assertIn("Ctrl+C", errors)

    def test_task_wait_rejects_bad_limits_before_query(self) -> None:
        for timeout, interval in ((-1, 1), (float("nan"), 1), (1, 0), (1, -1), (1, float("inf"))):
            with self.subTest(timeout=timeout, interval=interval), patch.object(cli, "send") as send:
                with self.assertRaises(ValueError):
                    cli.poll_task("task1", timeout, interval)
                send.assert_not_called()

    def test_task_list_validates_pages_dates_and_order_before_query(self) -> None:
        invalid = [
            ["--page", "0"], ["--page-size", "101"],
            ["--start-date", "2026-1-01", "--end-date", "2026-09-16"],
            ["--start-date", "2026-09-16", "--end-date", "2026-09-01"],
        ]
        for options in invalid:
            with self.subTest(options=options), patch.object(cli, "send") as send:
                code, _, _ = self.invoke(["tasks", "list", *options])
                self.assertEqual(code, 2)
                send.assert_not_called()

    def test_task_and_attempt_guidance_is_state_specific(self) -> None:
        for step, expected in ((0, "WAIT_FOR_TASK"), (1, "WAIT_FOR_TASK"), (2, "CHOOSE_DOWNLOAD_DESTINATION"), (3, "REVIEW_TASK_FAILURE")):
            with self.subTest(step=step):
                task = cli.public_task_data({"uniqId": "task1", "step": step,
                                             "imageUrls": ["https://cdn.example/fixture.png"]})
                self.assertEqual(task["nextAction"]["code"], expected)
        for state in ("prepared", "submitting", "unknown", "created", "failed"):
            cli.reserve_attempt(state, {"state": state, "taskId": "task1"})
            action = cli.attempt_record(state)["nextAction"]
            if state == "created":
                self.assertIn("tasks get task1", action["command"])
            elif state != "failed":
                self.assertIn("Do not submit", action["instruction"])

    def test_authorization_and_empty_asset_list_guide_to_upload_without_fake_command(self) -> None:
        data = {"authorizationId": "auth1", "status": "SUCCEEDED", "group": {"uniqId": "group1", "name": "Fixture"}}
        with patch.object(cli, "send", return_value=ok(data)):
            action = cli.get_authorization("auth1")["nextAction"]
        self.assertIn("does not create assets", action["instruction"])
        self.assertIsNone(action["command"])
        with patch.object(cli, "send", return_value=ok({"records": [], "total": 0, "current": 1, "pages": 0})):
            assets = cli.list_real_human_assets("group1")
        self.assertEqual(assets["nextAction"]["code"], "SELECT_FILES_TO_UPLOAD")
        self.assertIsNone(assets["nextAction"]["command"])

    def test_read_and_input_interrupts_exit_130_without_traceback(self) -> None:
        for argv in (["credits", "balance"], ["setup"]):
            with self.subTest(argv=argv), patch.object(cli, "send", side_effect=KeyboardInterrupt), \
                    patch.object(cli.getpass, "getpass", side_effect=KeyboardInterrupt):
                code, _, errors = self.invoke(argv)
            self.assertEqual(code, 130)
            self.assertIn("Stopped", errors)
            self.assertNotIn("Traceback", errors)

    def test_generation_interrupt_persists_unknown_and_never_reposts(self) -> None:
        with patch.object(cli, "send", side_effect=[ok({"frozenCredit": 5}), KeyboardInterrupt]):
            code, output, _ = self.invoke(["generate", "create", "--kind", "image", "--json", json.dumps(self.request()), "--yes", "--attempt-id", "interrupted"])
        self.assertEqual(code, 130)
        self.assertEqual(json.loads(output)["state"], "unknown")
        self.assertEqual(cli.attempt_record("interrupted")["state"], "unknown")
        with patch.object(cli, "send") as send, self.assertRaises(SystemExit):
            cli.create_generation("image", self.request(), confirmed=True, attempt_id="interrupted")
        send.assert_not_called()

    def test_upload_interrupt_keeps_one_shot_plan_and_batch_inventory(self) -> None:
        files = [Path(self.temp.name) / f"file{index}.jpg" for index in range(3)]
        for file in files:
            file.write_bytes(b"\xff\xd8\xff\xe0fixture")
        preview = cli.prepare_upload_plan([str(file) for file in files])
        first = {"state": "uploaded", "assetUniqId": "asset1", "ready": False}
        with patch.object(cli, "upload_prepared_file", side_effect=[first, KeyboardInterrupt]), \
                self.assertRaises(cli.CommandInterrupted) as stopped:
            cli.execute_upload_plan(preview["uploadPlanId"], confirmed=True)
        result = stopped.exception.result
        self.assertEqual(len(result["uploaded"]), 1)
        self.assertEqual(result["failedOrUnknown"]["state"], "unknown")
        self.assertEqual(len(result["notAttempted"]), 1)
        persisted = json.loads(cli.upload_plan_path(preview["uploadPlanId"]).read_text())
        self.assertEqual(persisted["state"], "executing")
        self.assertEqual(len(persisted["uploaded"]), 1)
        with self.assertRaises(SystemExit):
            cli.execute_upload_plan(preview["uploadPlanId"], confirmed=True)

    def test_windows_environment_repairs_are_powershell_commands(self) -> None:
        with patch.object(cli.os, "name", "nt"):
            command = cli.clear_environment_command("HOLYCRAB_API_KEY")
        self.assertIn("Remove-Item Env:HOLYCRAB_API_KEY", command)
        self.assertNotIn("unset", command)

    def test_mcp_tool_calls_never_emit_console_feedback_even_with_tty(self) -> None:
        with patch.object(cli, "send", return_value=ok({"frozenCredit": 5})), \
                redirect_stdout(io.StringIO()) as output, redirect_stderr(TerminalBuffer()) as errors:
            result = cli.mcp_dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "generation_estimate", "arguments": {"kind": "image", "request": self.request()}}})
        self.assertFalse(result["result"]["isError"])
        self.assertIn("nextAction", result["result"]["structuredContent"])
        self.assertEqual(output.getvalue() + errors.getvalue(), "")

    def test_empty_and_malformed_key_verification_never_saves(self) -> None:
        for key, response in (("", ok({"credit": 10})), ("fixture-key", ok(None)), ("fixture-key", ok({}))):
            with self.subTest(key=bool(key), response=response), patch.object(cli.getpass, "getpass", return_value=key), \
                    patch.object(cli, "send", return_value=response), patch.object(cli, "save_config") as save:
                code, output, errors = self.invoke(["setup"])
            self.assertEqual(code, 1)
            save.assert_not_called()
            self.assertNotIn("connected", errors)
            self.assertNotIn("saved locally", output)

    def test_read_waits_interrupt_without_restarting_anything(self) -> None:
        for argv in (["tasks", "wait", "task1"], ["real-human", "wait", "auth1"], ["assets", "wait", "asset1"]):
            with self.subTest(argv=argv), patch.object(cli, "send", side_effect=KeyboardInterrupt) as send:
                code, _, errors = self.invoke(argv)
            self.assertEqual(code, 130)
            self.assertEqual(send.call_count, 1)
            self.assertIn("Ctrl+C", errors)
            self.assertNotIn("Traceback", errors)

    def test_multi_asset_wait_shows_each_asset_and_summary(self) -> None:
        replies = [ok({"uniqId": "asset1", "step": "UPLOADED_TO_ARK"}), ok({"uniqId": "asset2", "step": "UPLOADED"})]
        with patch.object(cli, "send", side_effect=replies):
            code, output, errors = self.invoke(["assets", "wait", "asset1", "asset2", "--timeout", "0"])
        self.assertEqual(code, 2)
        self.assertIn("per asset", errors)
        self.assertIn("2/2: asset2; 1 ready", errors)
        self.assertIn("1/2 ready", errors)
        self.assertTrue(json_documents(output)[-1]["timedOut"])

    def test_authorization_timeout_keeps_same_id(self) -> None:
        with patch.object(cli, "send", return_value=ok({"authorizationId": "auth1", "status": "CREATED"})) as send:
            code, output, _ = self.invoke(["real-human", "wait", "auth1", "--timeout", "0"])
        self.assertEqual(code, 2)
        self.assertIn("real-human get auth1", json_documents(output)[-1]["nextAction"]["command"])
        self.assertTrue(all(call.args[0] == "GET" for call in send.call_args_list))

    def test_generation_interruption_after_valid_id_preserves_it(self) -> None:
        original = cli.update_attempt
        interrupted = False

        def record(attempt_id, **changes):
            nonlocal interrupted
            if changes.get("state") == "created" and not interrupted:
                interrupted = True
                raise KeyboardInterrupt
            return original(attempt_id, **changes)

        with patch.object(cli, "send", side_effect=[ok({"credit": 5}), ok({"uniqId": "task1"})]), \
                patch.object(cli, "update_attempt", side_effect=record):
            code, output, _ = self.invoke(["generate", "create", "--kind", "image", "--json", json.dumps(self.request()), "--yes", "--attempt-id", "result-interrupt"])
        self.assertEqual(code, 130)
        self.assertEqual(json.loads(output)["taskId"], "task1")
        self.assertEqual(cli.attempt_record("result-interrupt")["taskId"], "task1")

    def test_write_interrupts_return_only_known_identifiers(self) -> None:
        cases = [(["real-human", "start", "--name", "Fixture"], None),
                 (["real-human", "groups", "rename", "group1", "--name", "Fixture"], "group1"),
                 (["real-human", "groups", "delete", "group1", "--yes"], "group1"),
                 (["real-human", "assets", "delete", "asset1", "--group", "group1", "--yes"], "asset1")]
        for argv, identifier in cases:
            with self.subTest(argv=argv), patch.object(cli, "find_real_human_group", return_value={"uniqId": "group1"}), \
                    patch.object(cli, "get_asset", return_value={"uniqId": "asset1"}), patch.object(cli, "send", side_effect=KeyboardInterrupt):
                code, output, _ = self.invoke(argv)
            self.assertEqual(code, 130)
            result = json_documents(output)[-1]
            self.assertEqual(result["state"], "unknown")
            self.assertIn("Do not", result["nextAction"]["instruction"])
            if identifier: self.assertIn(identifier, json.dumps(result))
            else: self.assertIsNone(result["nextAction"]["command"])

    def test_qr_interrupt_does_not_lose_created_authorization(self) -> None:
        fixture = {"authorizationId": "auth1", "h5Link": "https://verify.example/fixture", "expiresAt": "2099-01-01T00:00:00Z"}
        with patch.object(cli, "send", return_value=ok(fixture)) as send, \
                patch.object(cli, "validate_verification_link", side_effect=lambda value: value), \
                patch.object(cli, "authorization_qr", side_effect=KeyboardInterrupt):
            code, output, _ = self.invoke(["real-human", "start", "--name", "Fixture"])
        self.assertEqual(code, 130)
        self.assertEqual(json.loads(output)["authorizationId"], "auth1")
        self.assertEqual(send.call_count, 1)

    def test_upload_interrupt_presign_put_and_registration_never_retries(self) -> None:
        source = Path(self.temp.name) / "file.jpg"
        source.write_bytes(b"\xff\xd8\xff\xe0fixture")
        item = cli.inspect_upload_file(str(source), real_human=False)
        signed = ok({"uniqId": "asset1", "preSignedUrl": "https://storage.example/fixture", "objectKey": "fixture"})
        uploaded = MagicMock()
        uploaded.__enter__.return_value.status = 200
        for phase in ("presign", "upload", "registration"):
            with self.subTest(phase=phase), patch.object(cli, "send", side_effect=KeyboardInterrupt if phase == "presign" else [signed, KeyboardInterrupt]) as send, \
                    patch.object(cli, "open_presigned_upload", side_effect=KeyboardInterrupt if phase == "upload" else None, return_value=uploaded) as put:
                result = cli.upload_prepared_file(item, None)
            self.assertTrue(result["interrupted"])
            self.assertEqual(result["state"], "unknown")
            self.assertEqual(result["phase"], phase)
            self.assertEqual(send.call_count, 2 if phase == "registration" else 1)
            self.assertEqual(put.call_count, 0 if phase == "presign" else 1)

    def test_interrupted_upload_ledger_survives_plan_expiry(self) -> None:
        path = cli.upload_plan_path("plan1")
        cli.write_private_json(path, {"state": "executing", "expiresAtEpoch": 0, "uploaded": [{"assetUniqId": "asset1"}]})
        cli.cleanup_upload_plans()
        self.assertTrue(path.exists())

    def test_upload_interrupt_while_recording_preserves_known_asset_and_inventory(self) -> None:
        files = [Path(self.temp.name) / f"file{i}.jpg" for i in range(2)]
        for file in files: file.write_bytes(b"\xff\xd8\xff\xe0fixture")
        preview = cli.prepare_upload_plan([str(file) for file in files])
        original, interrupted = cli.write_private_json, False

        def record(path, value):
            nonlocal interrupted
            if value.get("uploaded") and not interrupted:
                interrupted = True
                raise KeyboardInterrupt
            return original(path, value)

        with patch.object(cli, "write_private_json", side_effect=record), \
                patch.object(cli, "upload_prepared_file", return_value={"state": "uploaded", "assetUniqId": "asset1"}), \
                self.assertRaises(cli.CommandInterrupted) as stopped:
            cli.execute_upload_plan(preview["uploadPlanId"], confirmed=True)
        self.assertEqual(stopped.exception.result["failedOrUnknown"]["assetUniqId"], "asset1")
        self.assertEqual(len(stopped.exception.result["notAttempted"]), 1)
        self.assertIn("asset1", stopped.exception.result["nextAction"]["command"])

    def test_download_interrupt_cleans_partial_and_keeps_existing_file(self) -> None:
        output = Path(self.temp.name) / "result.jpg"
        output.write_bytes(b"original")
        remote = MagicMock()
        remote.__enter__.return_value.headers = {}
        remote.__enter__.return_value.read.side_effect = [b"partial", KeyboardInterrupt]
        task = {"uniqId": "task1", "step": 2, "imageUrls": ["https://storage.example/fixture"]}
        with patch.object(cli, "send", return_value=ok(task)), patch.object(cli, "validate_download_url", side_effect=lambda value: value), \
                patch.object(cli, "open_download", return_value=remote):
            code, _, errors = self.invoke(["download", "task1", "--output", str(output), "--force"])
        self.assertEqual(code, 130)
        self.assertEqual(output.read_bytes(), b"original")
        self.assertEqual(list(output.parent.glob("*.part")), [])
        self.assertNotIn("Download completed", errors)

    def test_cancelled_upload_and_generation_have_json_only_stdout(self) -> None:
        source = Path(self.temp.name) / "file.jpg"
        source.write_bytes(b"\xff\xd8\xff\xe0fixture")
        cases = [["assets", "upload", str(source)], ["generate", "create", "--kind", "image", "--json", json.dumps(self.request())]]
        for argv in cases:
            with self.subTest(argv=argv), patch.object(cli.sys.stdin, "isatty", return_value=True), \
                    patch("builtins.input", return_value="n") as confirm, patch.object(cli, "send", return_value=ok({"credit": 5})) as send:
                code, output, errors = self.invoke(argv)
            self.assertEqual(code, 2)
            self.assertEqual(confirm.call_count, 1)
            self.assertIn("Cancelled", errors)
            json_documents(output)
            self.assertTrue(all("freeze-credit" in call.args[1] for call in send.call_args_list))

    def test_update_interrupt_before_install_does_not_execute_and_removes_download(self) -> None:
        name = "install.ps1" if os.name == "nt" else "install.sh"
        release = {"tag_name": "v0.4.2", "assets": [{"name": name, "browser_download_url": f"https://github.com/AstroxNetwork/skills/releases/download/v0.4.2/{name}", "digest": "sha256:" + "0" * 64}]}
        with patch.object(cli, "load_installation", return_value={}), patch.object(cli, "open_download", side_effect=KeyboardInterrupt), \
                patch.object(cli.subprocess, "run") as run, self.assertRaises(KeyboardInterrupt):
            cli.run_update(release)
        run.assert_not_called()

    def test_uninstall_interrupt_during_cleanup_reports_partial_state_without_network(self) -> None:
        root = Path(self.temp.name)
        with patch.object(cli, "validated_uninstall_manifest", return_value=({}, root, root / "lib", root / "bin/holycrab")), \
                patch.object(cli, "remove_managed_mcp_registrations", side_effect=KeyboardInterrupt), patch.object(cli, "send") as send:
            code, output, errors = self.invoke(["uninstall", "--yes"])
        self.assertEqual(code, 130)
        self.assertIn("interrupted", output)
        self.assertNotIn("uninstall completed", output + errors)
        send.assert_not_called()

    def test_semantic_bad_arguments_bypass_housekeeping(self) -> None:
        for argv in (["tasks", "wait", "task1", "--interval", "0"], ["tasks", "list", "--page", "0"]):
            with self.subTest(argv=argv), patch.object(cli.sys, "argv", ["holycrab", *argv]), \
                    patch.object(cli, "startup_maintenance") as maintenance, patch.object(cli, "cleanup_upload_plans") as cleanup, \
                    redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(), 2)
            maintenance.assert_not_called(); cleanup.assert_not_called()

    def test_update_cancel_is_once_and_never_runs_installer(self) -> None:
        with patch.object(cli, "check_for_update", return_value={"updateAvailable": True}), \
                patch.object(cli, "read_update_state", return_value={"release": {"tag_name": "v0.4.2"}}), \
                patch.object(cli.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="n") as confirm, \
                patch.object(cli, "run_update") as install:
            code, output, errors = self.invoke(["update"])
        self.assertEqual(code, 2)
        self.assertEqual(confirm.call_count, 1)
        self.assertIn("cancelled", errors)
        install.assert_not_called()
        json.loads(output)

    def test_update_install_interrupt_reports_no_unverified_rollback_promise(self) -> None:
        name = "install.ps1" if os.name == "nt" else "install.sh"
        body = (('$Version = "v0.4.2"' if os.name == "nt" else 'VERSION=v0.4.2') + '\n').encode()
        release = {"tag_name": "v0.4.2", "assets": [{"name": name,
            "browser_download_url": f"https://github.com/AstroxNetwork/skills/releases/download/v0.4.2/{name}",
            "digest": "sha256:" + cli.hashlib.sha256(body).hexdigest()}]}
        remote = MagicMock()
        remote.__enter__.return_value.read.side_effect = [body, b""]
        with patch.object(cli, "load_installation", return_value={}), patch.object(cli, "open_download", return_value=remote), \
                patch.object(cli.subprocess, "run", side_effect=KeyboardInterrupt), self.assertRaises(cli.CommandInterrupted) as interrupted:
            cli.run_update(release)
        self.assertIn("Do not assume rollback", interrupted.exception.result["nextAction"]["instruction"])

    def test_clear_key_does_not_clear_environment_or_revoke_online_key(self) -> None:
        cli.save_config({"apiKey": "hc_test_clear_key"})
        with patch.object(cli, "send") as send:
            code, output, _ = self.invoke(["auth", "clear-key"])
        self.assertEqual(code, 0)
        self.assertIn("not revoked", output)
        self.assertEqual(os.environ["HOLYCRAB_API_KEY"], "test-feedback-key")
        self.assertNotIn("apiKey", cli.load_config())
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
