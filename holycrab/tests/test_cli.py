from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "holycrab_cli.py"
SPEC = importlib.util.spec_from_file_location("holycrab_cli", SCRIPT_PATH)
assert SPEC and SPEC.loader
holycrab = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(holycrab)


class LocalAuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(
            os.environ,
            {"HOLYCRAB_CONFIG_DIR": self.temp.name},
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop("HOLYCRAB_API_KEY", None)

    def test_saved_api_key_uses_user_only_permissions(self) -> None:
        holycrab.save_config({"apiKey": "local-secret"})
        path = holycrab.config_path()
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(holycrab.credential(), ("X-User-Token", "local-secret"))

    def test_environment_api_key_overrides_saved_key(self) -> None:
        holycrab.save_config({"apiKey": "saved-secret"})
        with patch.dict(os.environ, {"HOLYCRAB_API_KEY": "environment-secret"}):
            self.assertEqual(holycrab.credential(), ("X-User-Token", "environment-secret"))

    def test_set_key_reads_key_from_stdin_and_never_prints_it(self) -> None:
        args = argparse.Namespace(stdin=True, no_verify=False)
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO("entered-secret\n")), patch.object(
            holycrab,
            "send",
            return_value=(200, {"code": 0, "data": {"email": "student@example.com"}}),
        ) as send, redirect_stdout(output):
            self.assertEqual(holycrab.command_set_key(args), 0)
        self.assertNotIn("entered-secret", output.getvalue())
        self.assertEqual(holycrab.load_config()["apiKey"], "entered-secret")
        send.assert_called_once_with("GET", "/api/user/me", api_key="entered-secret")

    def test_set_key_accepts_production_success_code_200(self) -> None:
        args = argparse.Namespace(stdin=True, no_verify=False)
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO("entered-secret\n")), patch.object(
            holycrab,
            "send",
            return_value=(200, {"code": 200, "data": {"email": "student@example.com"}}),
        ), redirect_stdout(output):
            self.assertEqual(holycrab.command_set_key(args), 0)
        self.assertEqual(holycrab.load_config()["apiKey"], "entered-secret")
        self.assertIn("student@example.com", output.getvalue())

    def test_set_key_warns_when_environment_key_will_override_saved_key(self) -> None:
        args = argparse.Namespace(stdin=True, no_verify=False)
        output = io.StringIO()
        with patch.dict(os.environ, {"HOLYCRAB_API_KEY": "old-environment-secret"}), patch(
            "sys.stdin", io.StringIO("new-saved-secret\n")
        ), patch.object(
            holycrab,
            "send",
            return_value=(200, {"code": 200, "data": {"email": "student@example.com"}}),
        ), redirect_stdout(output):
            self.assertEqual(holycrab.command_set_key(args), 0)
        self.assertEqual(holycrab.load_config()["apiKey"], "new-saved-secret")
        self.assertIn("unset HOLYCRAB_API_KEY", output.getvalue())
        self.assertNotIn("old-environment-secret", output.getvalue())
        self.assertNotIn("new-saved-secret", output.getvalue())

    def test_clear_key_removes_saved_key(self) -> None:
        holycrab.save_config({"apiKey": "saved-secret"})
        with redirect_stdout(io.StringIO()):
            self.assertEqual(holycrab.command_clear_key(argparse.Namespace()), 0)
        self.assertNotIn("apiKey", holycrab.load_config())

    def test_status_describes_configuration_and_validity_without_login_language(self) -> None:
        holycrab.save_config({"apiKey": "saved-secret"})
        output = io.StringIO()
        with patch.object(
            holycrab,
            "send",
            return_value=(200, {"code": 200, "data": {"email": "student@example.com"}}),
        ), redirect_stdout(output):
            self.assertEqual(holycrab.command_auth_status(argparse.Namespace()), 0)
        value = json.loads(output.getvalue())
        self.assertEqual(value["configured"], True)
        self.assertEqual(value["valid"], True)
        self.assertNotIn("loggedIn", value)

    def test_parser_exposes_setup_and_key_commands_without_login_aliases(self) -> None:
        parser = holycrab.build_parser()
        setup = parser.parse_args(["setup", "--stdin", "--no-verify"])
        set_key = parser.parse_args(["auth", "set-key", "--stdin", "--no-verify"])
        clear_key = parser.parse_args(["auth", "clear-key"])
        self.assertIs(setup.func, holycrab.command_set_key)
        self.assertIs(set_key.func, holycrab.command_set_key)
        self.assertIs(clear_key.func, holycrab.command_clear_key)
        for legacy in (["auth", "login"], ["auth", "logout"]):
            with self.subTest(legacy=legacy), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser.parse_args(legacy)


class OutputRedactionTests(unittest.TestCase):
    def test_redacts_signed_result_urls_under_ordinary_response_keys(self) -> None:
        tos_url = (
            "https://cdn.example/result.mp4?X-Tos-Algorithm=TOS4-HMAC-SHA256"
            "&X-Tos-Credential=temporary&X-Tos-Signature=tos-secret"
        )
        amazon_url = "https://cdn.example/result.jpg?X-Amz-Credential=temporary&X-Amz-Signature=aws-secret"
        stable_url = "https://cdn.example/public.jpg?version=2"

        sanitized = holycrab.sanitize_for_output(
            {
                "data": {
                    "videoUrl": tos_url,
                    "imageUrls": [amazon_url],
                    "stableUrl": stable_url,
                }
            }
        )

        self.assertEqual(sanitized["data"]["videoUrl"], "<redacted-signed-url>")
        self.assertEqual(sanitized["data"]["imageUrls"], ["<redacted-signed-url>"])
        self.assertEqual(sanitized["data"]["stableUrl"], stable_url)

    def test_poll_output_never_writes_signed_result_url(self) -> None:
        signed_url = "https://cdn.example/result.mp4?X-Tos-Signature=terminal-secret"
        args = argparse.Namespace(uniq_id="task-1", timeout=0.0, interval=0.0)
        output = io.StringIO()
        with patch.object(
            holycrab,
            "send",
            return_value=(200, {"code": 0, "data": {"step": 2, "videoUrl": signed_url}}),
        ), redirect_stdout(output):
            self.assertEqual(holycrab.command_poll_task(args), 0)

        self.assertNotIn("terminal-secret", output.getvalue())
        self.assertNotIn("X-Tos-Signature", output.getvalue())
        self.assertIn("<redacted-signed-url>", output.getvalue())

    def test_redacts_signed_urls_embedded_in_error_text(self) -> None:
        value = (
            "upload failed for "
            "https://cdn.example/result.mp4?X-Amz-Credential=temporary&X-Amz-Signature=embedded-secret"
        )
        sanitized = holycrab.sanitize_for_output({"message": value})
        self.assertNotIn("embedded-secret", sanitized["message"])
        self.assertIn("<redacted-signed-url>", sanitized["message"])

    def test_redacts_html_escaped_signed_url_query(self) -> None:
        value = "https://cdn.example/result.mp4?download=1&amp;X-Tos-Signature=html-secret"
        sanitized = holycrab.sanitize_for_output(value)
        self.assertEqual(sanitized, "<redacted-signed-url>")

    def test_response_errors_redact_the_active_api_key(self) -> None:
        with patch.dict(os.environ, {"HOLYCRAB_API_KEY": "test-secret"}):
            holycrab.credential()
            with self.assertRaises(SystemExit) as raised:
                holycrab.response_data(
                    400,
                    {"code": 400, "message": "invalid credential test-secret"},
                )
        self.assertNotIn("test-secret", str(raised.exception))
        self.assertIn("<redacted-secret>", str(raised.exception))


class GenerationWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(
            os.environ,
            {
                "HOLYCRAB_CONFIG_DIR": self.temp.name,
                "HOLYCRAB_API_KEY": "test-secret",
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_routes_models_to_existing_production_endpoints(self) -> None:
        self.assertEqual(
            holycrab.generation_endpoints("video", {"model": "dreamina-seedance-2-5-260628"}),
            ("/api/tasks/generation/freeze-credit", "/api/tasks/generation"),
        )
        self.assertEqual(
            holycrab.generation_endpoints("video", {"model": "MiniMax-H3"}),
            ("/api/tasks/minimax-generation/freeze-credit", "/api/tasks/minimax-generation"),
        )

    def test_seedance_video_defaults_to_generated_audio(self) -> None:
        payload = {
            "model": "seedance-2-0-mini",
            "prompt": "a paper boat",
            "duration": 5,
            "resolution": "480p",
        }
        replies = [
            (200, {"code": 0, "data": {"frozenCredit": 10}}),
            (200, {"code": 0, "data": {"uniqId": "task-audio-default"}}),
        ]
        with patch.object(holycrab, "send", side_effect=replies) as send:
            holycrab.create_generation("video", payload, confirmed=True)
        self.assertTrue(send.call_args_list[0].kwargs["payload"]["generateAudio"])
        self.assertTrue(send.call_args_list[1].kwargs["payload"]["generateAudio"])

    def test_explicit_false_keeps_seedance_video_silent(self) -> None:
        payload = {
            "model": "seedance-2-0-mini",
            "prompt": "a paper boat",
            "duration": 5,
            "resolution": "480p",
            "generateAudio": False,
        }
        with patch.object(
            holycrab,
            "send",
            return_value=(200, {"code": 0, "data": {"frozenCredit": 10}}),
        ) as send:
            holycrab.create_generation("video", payload, confirmed=False)
        self.assertIs(send.call_args.kwargs["payload"]["generateAudio"], False)

    def test_h3_does_not_receive_unsupported_generate_audio_field(self) -> None:
        payload = {
            "model": "MiniMax-H3",
            "prompt": "a paper boat",
            "duration": 5,
            "resolution": "768P",
            "ratio": "16:9",
        }
        with patch.object(
            holycrab,
            "send",
            return_value=(200, {"code": 0, "data": {"frozenCredit": 10}}),
        ) as send:
            holycrab.create_generation("video", payload, confirmed=False)
        self.assertNotIn("generateAudio", send.call_args.kwargs["payload"])

    def test_confirmed_repeated_draws_create_distinct_attempts(self) -> None:
        payload = {
            "model": "MiniMax-H3",
            "prompt": "same prompt",
            "resolution": "768P",
            "duration": 5,
            "ratio": "16:9",
        }
        replies = [
            (200, {"code": 0, "data": {"frozenCredit": 10}}),
            (200, {"code": 0, "data": {"uniqId": "task-1"}}),
            (200, {"code": 0, "data": {"frozenCredit": 10}}),
            (200, {"code": 0, "data": {"uniqId": "task-2"}}),
        ]
        with patch.object(holycrab, "send", side_effect=replies):
            first = holycrab.create_generation("video", payload, confirmed=True)
            second = holycrab.create_generation("video", payload, confirmed=True)
        self.assertNotEqual(first["attemptId"], second["attemptId"])
        self.assertEqual(first["taskId"], "task-1")
        self.assertEqual(second["taskId"], "task-2")

    def test_reusing_same_attempt_id_never_submits_twice(self) -> None:
        payload = {"model": "seedream-5-0-lite-260128", "prompt": "draw", "size": "2k"}
        replies = [
            (200, {"code": 0, "data": {"frozenCredit": 5}}),
            (200, {"code": 0, "data": {"uniqId": "task-1"}}),
        ]
        with patch.object(holycrab, "send", side_effect=replies) as send:
            holycrab.create_generation("image", payload, confirmed=True, attempt_id="attempt-fixed")
            with self.assertRaisesRegex(SystemExit, "already exists"):
                holycrab.create_generation("image", payload, confirmed=True, attempt_id="attempt-fixed")
        self.assertEqual(send.call_count, 2)

    def test_reserving_same_attempt_id_twice_is_rejected(self) -> None:
        holycrab.reserve_attempt("locked-attempt", {"state": "submitting"})
        with self.assertRaisesRegex(SystemExit, "already exists"):
            holycrab.reserve_attempt("locked-attempt", {"state": "submitting"})

    def test_unconfirmed_generation_only_estimates(self) -> None:
        payload = {"model": "seedream-5-0-lite-260128", "prompt": "draw", "size": "2k"}
        with patch.object(
            holycrab,
            "send",
            return_value=(200, {"code": 0, "data": {"frozenCredit": 5}}),
        ) as send:
            result = holycrab.create_generation("image", payload, confirmed=False)
        self.assertTrue(result["confirmationRequired"])
        send.assert_called_once_with(
            "POST", "/api/tasks/image-generation/freeze-credit", payload=payload
        )

    def test_interactive_confirmation_reuses_the_displayed_estimate(self) -> None:
        args = argparse.Namespace(
            kind="image",
            json='{"model":"seedream-5-0-lite-260128","prompt":"draw","size":"2k"}',
            yes=False,
            attempt_id=None,
        )
        replies = [
            (200, {"code": 0, "data": {"frozenCredit": 5}}),
            (200, {"code": 0, "data": {"frozenCredit": 5}}),
            (200, {"code": 0, "data": {"uniqId": "task-1"}}),
        ]
        with patch.object(holycrab, "send", side_effect=replies) as send, patch.object(
            sys.stdin, "isatty", return_value=True
        ), patch("builtins.input", return_value="y"), redirect_stdout(io.StringIO()):
            self.assertEqual(holycrab.command_generation_create(args), 0)
        self.assertEqual(send.call_count, 2)

    def test_ambiguous_network_error_is_recorded_without_retry(self) -> None:
        payload = {"model": "seedream-5-0-lite-260128", "prompt": "draw", "size": "2k"}
        replies = [
            (200, {"code": 0, "data": {"frozenCredit": 5}}),
            urllib.error.URLError("timed out"),
        ]
        with patch.object(holycrab, "send", side_effect=replies) as send:
            with self.assertRaises(urllib.error.URLError):
                holycrab.create_generation(
                    "image", payload, confirmed=True, attempt_id="attempt-unknown"
                )
        self.assertEqual(send.call_count, 2)
        record = holycrab.load_attempts()["attempt-unknown"]
        self.assertEqual(record["state"], "unknown")

    def test_download_fetches_a_task_output_without_printing_its_url(self) -> None:
        destination = Path(self.temp.name) / "result.mp4"
        response = MagicMock()
        response.__enter__.return_value.read.side_effect = [b"video-bytes", b""]
        with patch.object(
            holycrab,
            "send",
            return_value=(
                200,
                {"code": 0, "data": {"uniqId": "task-1", "videoUrl": "https://cdn.example/result.mp4?secret=value"}},
            ),
        ), patch.object(holycrab.urllib.request, "urlopen", return_value=response), redirect_stdout(io.StringIO()) as output:
            args = argparse.Namespace(uniq_id="task-1", output=str(destination), index=0)
            self.assertEqual(holycrab.command_download(args), 0)
        self.assertEqual(destination.read_bytes(), b"video-bytes")
        self.assertNotIn("secret=value", output.getvalue())

    def test_download_uses_private_exclusive_staging_instead_of_following_symlink(self) -> None:
        destination = Path(self.temp.name) / "result.mp4"
        protected = Path(self.temp.name) / "protected.txt"
        protected.write_bytes(b"keep-me")
        predictable_partial = destination.with_name(f".{destination.name}.part")
        predictable_partial.symlink_to(protected)
        response = MagicMock()
        response.__enter__.return_value.read.side_effect = [b"video-bytes", b""]
        with patch.object(
            holycrab,
            "send",
            return_value=(
                200,
                {"code": 0, "data": {"uniqId": "task-1", "videoUrl": "https://cdn.example/result.mp4"}},
            ),
        ), patch.object(holycrab.urllib.request, "urlopen", return_value=response), redirect_stdout(
            io.StringIO()
        ):
            args = argparse.Namespace(uniq_id="task-1", output=str(destination), index=0)
            self.assertEqual(holycrab.command_download(args), 0)

        self.assertEqual(protected.read_bytes(), b"keep-me")
        self.assertEqual(destination.read_bytes(), b"video-bytes")
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)


class PublicProjectionTests(unittest.TestCase):
    def test_task_output_omits_private_and_internal_backend_fields(self) -> None:
        output = io.StringIO()
        response = {
            "code": 0,
            "data": {
                "uniqId": "task-1",
                "step": 2,
                "videoUrl": "https://cdn.example/result.mp4",
                "provider": "internal-provider",
                "seedanceTaskId": "upstream-123",
                "request": {"prompt": "private prompt"},
            },
        }
        with patch.object(holycrab, "send", return_value=(200, response)), redirect_stdout(output):
            self.assertEqual(holycrab.command_task_get(argparse.Namespace(uniq_id="task-1")), 0)
        data = json.loads(output.getvalue())["response"]["data"]
        self.assertEqual(data["uniqId"], "task-1")
        self.assertEqual(data["videoUrl"], "https://cdn.example/result.mp4")
        self.assertNotIn("provider", data)
        self.assertNotIn("seedanceTaskId", data)
        self.assertNotIn("request", data)

    def test_account_output_uses_public_allowlist(self) -> None:
        output = io.StringIO()
        response = {
            "code": 0,
            "data": {"email": "student@example.com", "credit": 100, "databaseId": 42, "admin": True},
        }
        with patch.object(holycrab, "send", return_value=(200, response)), redirect_stdout(output):
            self.assertEqual(holycrab.command_credits_balance(argparse.Namespace()), 0)
        data = json.loads(output.getvalue())["response"]["data"]
        self.assertEqual(data, {"email": "student@example.com", "credit": 100})


class AssetUploadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(
            os.environ,
            {
                "HOLYCRAB_CONFIG_DIR": self.temp.name,
                "HOLYCRAB_API_KEY": "test-secret",
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    @staticmethod
    def upload_response() -> MagicMock:
        response = MagicMock()
        response.__enter__.return_value.status = 200
        return response

    def test_multipart_encoder_preserves_utf8_values(self) -> None:
        encoder = getattr(holycrab, "encode_multipart_form", None)
        self.assertIsNotNone(encoder)
        body, content_type = encoder(
            {"name": "懵懵.jpeg", "object_key": "user/asset.jpeg"},
            boundary="HolyCrabBoundary",
        )
        self.assertEqual(content_type, "multipart/form-data; boundary=HolyCrabBoundary")
        self.assertIn('name="name"'.encode(), body)
        self.assertIn("懵懵.jpeg".encode("utf-8"), body)
        self.assertTrue(body.endswith(b"--HolyCrabBoundary--\r\n"))

    def test_upload_registers_chinese_filename_as_multipart(self) -> None:
        image = Path(self.temp.name) / "懵懵.jpeg"
        image.write_bytes(b"jpeg-bytes")
        replies = [
            (
                200,
                {
                    "code": 0,
                    "data": {
                        "preSignedUrl": "https://storage.example/object?signature=secret",
                        "objectKey": "user/asset.jpeg",
                        "uniqId": "asset-1",
                    },
                },
            ),
            (200, {"code": 0, "data": None}),
        ]
        with patch.object(holycrab, "send", side_effect=replies) as send, patch.object(
            holycrab.urllib.request, "urlopen", return_value=self.upload_response()
        ):
            result = holycrab.upload_asset(str(image))

        self.assertEqual(result["assetUniqId"], "asset-1")
        send.assert_any_call(
            "POST",
            "/api/user-assets/upload",
            form={
                "name": "懵懵.jpeg",
                "object_key": "user/asset.jpeg",
                "content_type": "image/jpeg",
            },
        )

    def test_consecutive_uploads_register_each_asset(self) -> None:
        images = []
        replies = []
        for index, name in enumerate(("yuna_1.jpeg", "懵懵.jpeg", "yuna.jpeg"), start=1):
            image = Path(self.temp.name) / name
            image.write_bytes(f"image-{index}".encode())
            images.append(image)
            replies.extend(
                [
                    (
                        200,
                        {
                            "code": 0,
                            "data": {
                                "preSignedUrl": f"https://storage.example/{index}?signature=secret",
                                "objectKey": f"user/asset-{index}.jpeg",
                                "uniqId": f"asset-{index}",
                            },
                        },
                    ),
                    (200, {"code": 0, "data": None}),
                ]
            )

        with patch.object(holycrab, "send", side_effect=replies) as send, patch.object(
            holycrab.urllib.request, "urlopen", return_value=self.upload_response()
        ):
            results = [holycrab.upload_asset(str(image)) for image in images]

        self.assertEqual([result["assetUniqId"] for result in results], ["asset-1", "asset-2", "asset-3"])
        registration_calls = [
            call for call in send.call_args_list if call.args == ("POST", "/api/user-assets/upload")
        ]
        self.assertEqual(len(registration_calls), 3)
        self.assertTrue(all("form" in call.kwargs and "payload" not in call.kwargs for call in registration_calls))

    def test_registration_400_is_reported_without_returning_asset(self) -> None:
        image = Path(self.temp.name) / "yuna.jpeg"
        image.write_bytes(b"jpeg-bytes")
        replies = [
            (
                200,
                {
                    "code": 0,
                    "data": {
                        "preSignedUrl": "https://storage.example/object?signature=secret",
                        "objectKey": "user/asset.jpeg",
                        "uniqId": "asset-not-registered",
                    },
                },
            ),
            (400, {"code": 400, "message": "missing multipart field"}),
        ]
        with patch.object(holycrab, "send", side_effect=replies), patch.object(
            holycrab.urllib.request, "urlopen", return_value=self.upload_response()
        ):
            with self.assertRaisesRegex(SystemExit, "HTTP 400: missing multipart field"):
                holycrab.upload_asset(str(image))


if __name__ == "__main__":
    unittest.main()
