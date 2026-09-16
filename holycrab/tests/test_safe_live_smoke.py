from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).with_name("safe_live_smoke.py")
SPEC = importlib.util.spec_from_file_location("holycrab_safe_live_smoke", SCRIPT)
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class SafeRequestGuardTests(unittest.TestCase):
    def test_allows_only_documented_reads_and_freeze_credit_posts(self) -> None:
        delegate = Mock(return_value=(200, {"code": 200, "data": {"credit": 10}}))
        guard = smoke.SafeRequestGuard(delegate)

        guard("GET", "/api/user/me")
        guard("GET", "/api/tasks")
        guard("GET", "/api/tasks/task123")
        guard("GET", "/api/real-human-groups")
        guard("GET", "/api/real-human-groups/group123/assets")
        for route in smoke.FREEZE_CREDIT_ROUTES:
            guard("POST", route, payload={"prompt": "safe"})

        self.assertEqual(delegate.call_count, 5 + len(smoke.FREEZE_CREDIT_ROUTES))
        summary = guard.summary()
        self.assertEqual(summary["generationCreateCalls"], 0)
        self.assertEqual(summary["blockedCalls"], 0)
        self.assertIn({"method": "GET", "route": "/api/tasks/{id}", "count": 1}, summary["requests"])
        self.assertNotIn("task123", json.dumps(summary))
        self.assertNotIn("group123", json.dumps(summary))

    def test_blocks_generation_creation_before_delegate(self) -> None:
        delegate = Mock()
        guard = smoke.SafeRequestGuard(delegate)

        for route in smoke.GENERATION_CREATE_ROUTES:
            with self.subTest(route=route), self.assertRaises(smoke.SafetyViolation) as stopped:
                guard("POST", route, payload={"prompt": "must not run"})
            self.assertTrue(stopped.exception.generation_create)

        delegate.assert_not_called()
        self.assertEqual(guard.summary()["blockedCalls"], len(smoke.GENERATION_CREATE_ROUTES))

    def test_records_allowed_attempt_even_when_delegate_fails(self) -> None:
        delegate = Mock(side_effect=OSError("offline"))
        guard = smoke.SafeRequestGuard(delegate)

        with self.assertRaises(OSError):
            guard("GET", "/api/user/me")

        self.assertEqual(
            guard.summary()["requests"],
            [{"method": "GET", "route": "/api/user/me", "count": 1}],
        )

    def test_blocks_upload_authorization_and_unknown_requests(self) -> None:
        delegate = Mock()
        guard = smoke.SafeRequestGuard(delegate)
        blocked = (
            ("POST", "/api/real-human-authorizations/sessions"),
            ("GET", "/api/user-assets/pre-signed-download-url"),
            ("POST", "/api/user-assets/upload"),
            ("PATCH", "/api/real-human-groups/group123"),
            ("DELETE", "/api/real-human-groups/group123"),
            ("GET", "/api/unknown"),
        )

        for method, path in blocked:
            with self.subTest(method=method, path=path), self.assertRaises(smoke.SafetyViolation):
                guard(method, path)

        delegate.assert_not_called()


class SafeMcpGuardTests(unittest.TestCase):
    def test_allows_read_only_tools_and_estimate(self) -> None:
        delegate = Mock(return_value={"ok": True})
        guard = smoke.SafeMcpGuard(delegate)

        for tool in smoke.SAFE_MCP_TOOLS:
            self.assertEqual(guard(tool, {}), {"ok": True})

        self.assertEqual(delegate.call_count, len(smoke.SAFE_MCP_TOOLS))

    def test_blocks_generation_and_other_writes_before_delegate(self) -> None:
        delegate = Mock()
        guard = smoke.SafeMcpGuard(delegate)

        for tool in ("generation_create", "asset_upload_execute", "real_human_authorization_start",
                     "real_human_group_rename", "real_human_group_delete", "real_human_asset_delete"):
            with self.subTest(tool=tool), self.assertRaises(smoke.SafetyViolation) as stopped:
                guard(tool, {})
            self.assertEqual(stopped.exception.generation_create, tool == "generation_create")

        delegate.assert_not_called()


class ReportSafetyTests(unittest.TestCase):
    def test_report_contains_no_key_ids_or_absolute_paths(self) -> None:
        report = {
            "apiKey": "secret-key",
            "taskId": "task123",
            "output": "/Users/test/private/result.png",
            "url": "https://cdn.example/result.png?X-Amz-Signature=secret",
            "nested": {"authorization": "private-link"},
        }

        safe = smoke.sanitize_report(report, secrets={"secret-key", "task123", "private-link"})
        encoded = json.dumps(safe, sort_keys=True)

        self.assertNotIn("secret-key", encoded)
        self.assertNotIn("task123", encoded)
        self.assertNotIn("private-link", encoded)
        self.assertNotIn("/Users/test/private", encoded)
        self.assertNotIn("X-Amz-Signature", encoded)
        self.assertEqual(safe["apiKey"], "[REDACTED]")

    def test_atomic_report_write_uses_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            smoke.write_private_report(path, {"ok": True})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"ok": True})
            if smoke.os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class SafeLiveSuiteTests(unittest.TestCase):
    def test_every_public_command_has_help_without_network(self) -> None:
        commands = (
            ("setup",),
            ("auth", "set-key"), ("auth", "status"), ("auth", "clear-key"),
            ("models", "list"), ("models", "show"),
            ("credits", "balance"), ("credits", "estimate"),
            ("generate", "estimate"), ("generate", "create"),
            ("generate", "attempts", "list"), ("generate", "attempts", "get"),
            ("tasks", "get"), ("tasks", "list"), ("tasks", "wait"), ("download",),
            ("real-human", "start"), ("real-human", "get"), ("real-human", "wait"),
            ("real-human", "groups", "list"), ("real-human", "groups", "rename"),
            ("real-human", "groups", "delete"), ("real-human", "assets", "list"),
            ("real-human", "assets", "delete"),
            ("assets", "upload"), ("assets", "get"), ("assets", "wait"),
            ("mcp", "serve"), ("update",), ("doctor",), ("uninstall",),
        )
        parser = smoke.cli.build_parser()
        for command in commands:
            with self.subTest(command=command):
                output = StringIO()
                with redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
                    parser.parse_args([*command, "--help"])
                self.assertEqual(stopped.exception.code, 0)
                self.assertIn("usage:", output.getvalue())

    def test_mocked_suite_covers_reads_and_estimates_without_writes(self) -> None:
        calls: list[tuple[str, str]] = []

        def fake_send(method: str, path: str, **kwargs: object) -> tuple[int, object]:
            calls.append((method, path))
            if path == "/api/user/me":
                return 200, {"code": 200, "data": {"username": "user1", "credit": 100}}
            if path == "/api/tasks":
                return 200, {"code": 200, "data": {"records": [], "total": 0}}
            if path == "/api/real-human-groups":
                return 200, {"code": 200, "data": {"records": [], "total": 0}}
            if path in smoke.FREEZE_CREDIT_ROUTES:
                return 200, {"code": 200, "data": {"frozenCredit": 1}}
            raise AssertionError(f"Unexpected request: {method} {path}")

        update = {"checkedAt": "2026-09-16T00:00:00+00:00", "latestVersion": "0.4.0",
                  "updateAvailable": False}
        with patch.object(smoke.cli, "send", side_effect=fake_send), \
                patch.object(smoke.cli, "check_for_update", return_value=update):
            report = smoke.run_safe_live_suite("secret-key", account_label="user1")

        self.assertTrue(report["ok"])
        self.assertEqual(report["safety"]["generationCreateCalls"], 0)
        self.assertEqual(report["safety"]["blockedCalls"], 0)
        self.assertEqual(report["onlineDataCreated"], False)
        self.assertTrue(all(method == "GET" or path in smoke.FREEZE_CREDIT_ROUTES
                            for method, path in calls))
        expected_estimates = {
            smoke.cli.GENERATION_ROUTES[name][0]
            for name in ("seedanceVideo", "imageGeneration", "audioGeneration")
        }
        self.assertEqual({path for method, path in calls if method == "POST"}, expected_estimates)
        self.assertNotIn("secret-key", json.dumps(report))
        statuses = {row["check"]: row["status"] for row in report["checks"]}
        self.assertEqual(statuses["task get/wait"], "not_applicable")
        self.assertEqual(statuses["download"], "not_applicable")

    def test_invalid_generation_is_rejected_before_network(self) -> None:
        delegate = Mock()
        guard = smoke.SafeRequestGuard(delegate)

        smoke.assert_invalid_request_stays_local(guard)

        delegate.assert_not_called()
        self.assertEqual(guard.summary()["requests"], [])

    def test_cli_main_writes_only_a_sanitized_private_report(self) -> None:
        raw = {
            "ok": True,
            "taskId": "private-task",
            "checks": [
                {"check": "one", "status": "passed"},
                {"check": "two", "status": "not_applicable"},
            ],
            "safety": {"generationCreateCalls": 0, "blockedCalls": 0},
            "onlineDataCreated": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "live.json"
            output = StringIO()
            with patch.object(smoke.getpass, "getpass", return_value="secret-key"), \
                    patch.object(smoke, "run_safe_live_suite", return_value=raw), \
                    patch.dict(os.environ, {}, clear=False), \
                    redirect_stdout(output):
                os.environ.pop("HOLYCRAB_API_KEY", None)
                self.assertEqual(smoke.main(["--report", str(report_path), "--account-label", "user1"]), 0)
            contents = report_path.read_text(encoding="utf-8")
            self.assertNotIn("secret-key", contents)
            self.assertNotIn("private-task", contents)
            self.assertTrue(json.loads(contents)["ok"])
            console = output.getvalue()
            self.assertIn("API Key received", console)
            self.assertIn("Running safe checks", console)
            self.assertIn("1 passed, 0 failed, 1 not applicable", console)
            self.assertIn("generation create calls: 0", console)
            self.assertIn("No further terminal action is required", console)
            self.assertNotIn("secret-key", console)
            self.assertNotIn("private-task", console)

    def test_report_write_failure_does_not_print_traceback_or_success(self) -> None:
        with patch.object(smoke.getpass, "getpass", return_value="hc_test_fixture_hidden"), \
                patch.object(smoke, "run_safe_live_suite", return_value={"ok": True}), \
                patch.object(smoke, "write_private_report", side_effect=OSError("permission denied")), \
                patch.dict(os.environ, {}, clear=False), redirect_stdout(StringIO()) as output, \
                redirect_stderr(StringIO()) as errors:
            os.environ.pop("HOLYCRAB_API_KEY", None)
            self.assertEqual(smoke.main(["--report", "fixture.json"]), 1)
        self.assertIn("report could not be saved", errors.getvalue())
        self.assertNotIn("Completed:", output.getvalue())
        self.assertNotIn("hc_test_fixture_hidden", output.getvalue() + errors.getvalue())


if __name__ == "__main__":
    unittest.main()
