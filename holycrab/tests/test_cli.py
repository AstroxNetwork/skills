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


if __name__ == "__main__":
    unittest.main()
