"""Release discovery/reminder contracts; no external requests or real installs."""
from __future__ import annotations

import argparse
import errno
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "holycrab/scripts"))
from holycrab_runtime import lifecycle, mcp, results, settings, state

FETCH_LATEST_RELEASE = lifecycle.fetch_latest_release


class UpdateNotificationTests(unittest.TestCase):
    def context(self, manager):
        # unittest.TestCase.enterContext was only added in Python 3.11.
        value = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)
        return value

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.context(patch.dict(os.environ, {
            "HOLYCRAB_CONFIG_DIR": str(self.root / "config"), "HOLYCRAB_NO_UPDATE_CHECK": "0",
        }))
        self.context(patch.object(settings, "VERSION", "0.6.0"))
        self.context(patch.object(lifecycle, "_volatile_update_state", None))
        self.context(patch.object(mcp, "_update_versions_notified", set()))
        self.now = 1700000000.0
        self.context(patch.object(lifecycle.time, "time", side_effect=lambda: self.now))
        self.fetch = self.context(patch.object(lifecycle, "fetch_latest_release", return_value=self.release()))
        self.context(patch.object(mcp._real_human, "cleanup_authorization_qrs", return_value=None))

    def release(self, version="0.7.0"):
        return lifecycle.release_update_info({
            "tag_name": "v" + version, "draft": False, "prerelease": False,
            "html_url": settings.RELEASE_PAGE_PREFIX + "v" + version,
        })

    def request(self, name="capabilities_list", arguments=None):
        return {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
            "name": name, "arguments": arguments or {},
        }}

    def test_success_cache_expires_at_24_hours(self):
        lifecycle.check_for_update()
        self.now += 86399
        lifecycle.check_for_update()
        self.fetch.assert_called_once_with(settings.UPDATE_CHECK_TIMEOUT)
        self.now += 1
        lifecycle.check_for_update()
        self.assertEqual(self.fetch.call_count, 2)

    def test_real_release_parser_drives_the_notification_and_uses_bounded_fetch(self):
        self.fetch.side_effect = FETCH_LATEST_RELEASE
        response = io.BytesIO(json.dumps(self.release()["release"]).encode())
        with patch.object(lifecycle.urllib.request, "urlopen", return_value=response) as request:
            self.assertIn("0.6.0 -> 0.7.0", lifecycle.automatic_update_notice())
        self.assertEqual(request.call_args.args[0].full_url, settings.UPDATE_API_URL)
        self.assertEqual(request.call_args.kwargs["timeout"], settings.UPDATE_CHECK_TIMEOUT)
        self.assertEqual(lifecycle.read_update_state()["release"]["tag_name"], "v0.7.0")

    def test_invalid_remote_payloads_never_create_a_notification(self):
        self.fetch.side_effect = FETCH_LATEST_RELEASE
        release = self.release()["release"]
        payloads = [b"<html>upstream error</html>", b"\xff", b"x" * (1024 * 1024 + 1),
                    *[json.dumps({**release, **change}).encode() for change in (
                        {"draft": True}, {"prerelease": True}, {"tag_name": "v0.8.0-rc.1"},
                        {"html_url": "https://evil.example/update"})]]
        for payload in payloads:
            with self.subTest(payload_length=len(payload)), \
                    patch.object(lifecycle.urllib.request, "urlopen", return_value=io.BytesIO(payload)):
                self.assertIn("error", lifecycle.check_for_update(force=True))
                self.assertIsNone(lifecycle.cached_update_notice())

    def test_failed_check_retries_at_one_hour_and_recovers(self):
        self.fetch.side_effect = urllib.error.URLError("offline")
        self.assertIsNone(lifecycle.automatic_update_notice())
        self.now += 3599
        lifecycle.check_for_update()
        self.fetch.assert_called_once()
        self.now += 1
        self.fetch.side_effect = None
        self.assertIn("0.6.0 -> 0.7.0", lifecycle.automatic_update_notice())
        self.assertEqual(self.fetch.call_count, 2)
        self.assertFalse(lifecycle.read_update_state()["checkFailed"])

    def test_same_cli_version_reminds_once_a_day_and_newer_version_immediately(self):
        self.assertIn("holycrab update", lifecycle.automatic_update_notice())
        self.assertIsNone(lifecycle.automatic_update_notice())
        self.now += 10
        self.fetch.return_value = self.release("0.8.0")
        lifecycle.check_for_update(force=True)
        self.assertIn("0.8.0", lifecycle.automatic_update_notice())
        self.now += 86399
        self.assertIsNone(lifecycle.automatic_update_notice())
        self.now += 1
        self.assertIn("0.8.0", lifecycle.automatic_update_notice())

    def test_release_regression_does_not_bypass_daily_notice_limit(self):
        self.fetch.return_value = self.release("0.8.0")
        lifecycle.automatic_update_notice()
        self.fetch.return_value = self.release("0.7.0")
        lifecycle.check_for_update(force=True)
        self.assertIsNone(lifecycle.automatic_update_notice())

    def test_installed_upgrade_or_release_downgrade_cancels_stale_update_available(self):
        lifecycle.check_for_update()
        with patch.object(settings, "VERSION", "0.7.0"):
            self.assertFalse(lifecycle.check_for_update()["updateAvailable"])
            self.assertIsNone(lifecycle.automatic_update_notice())
        with patch.object(settings, "VERSION", "0.8.0"):
            self.assertIsNone(lifecycle.cached_update_notice())

    def test_failed_refresh_keeps_status_but_does_not_advertise_expired_release(self):
        lifecycle.check_for_update()
        self.now += 86400
        self.fetch.side_effect = urllib.error.URLError("offline")
        self.assertIsNone(lifecycle.automatic_update_notice())
        cached = lifecycle.check_for_update()
        self.assertEqual(cached["latestVersion"], "0.7.0")
        self.assertTrue(cached["stale"])
        self.assertIsNone(lifecycle.cached_update_notice())

    def test_expired_cache_alone_does_not_produce_initialization_notice(self):
        lifecycle.check_for_update()
        self.now += 86400
        response = mcp.mcp_dispatch({"id": 1, "method": "initialize"})
        self.assertNotIn("HolyCrab update available:", response["result"]["instructions"])
        self.fetch.assert_called_once()

    def test_oversized_cached_version_cannot_break_mcp_initialization(self):
        state.write_private_json(state.update_state_path(), {
            "checkedAtEpoch": self.now, "update": {
                "latestVersion": "9" * 5000 + ".0.0", "updateAvailable": True,
                "releasePage": settings.RELEASE_PAGE_PREFIX + "v0.7.0"}})
        response = mcp.mcp_dispatch({"id": 1, "method": "initialize"})
        self.assertEqual(response["result"]["serverInfo"]["version"], "0.6.0")
        self.assertNotIn("HolyCrab update available:", response["result"]["instructions"])
        self.fetch.assert_not_called()

    def test_disable_suppresses_cached_notices_and_network_but_explicit_check_works(self):
        lifecycle.check_for_update()
        with patch.dict(os.environ, {"HOLYCRAB_NO_UPDATE_CHECK": "1"}):
            self.assertTrue(lifecycle.check_for_update()["disabled"])
            self.assertIsNone(lifecycle.automatic_update_notice())
            self.assertIsNone(lifecycle.cached_update_notice())
            response = mcp.mcp_dispatch(self.request())
            self.assertNotIn("_meta", response["result"])
            self.fetch.assert_called_once()
            checked = lifecycle.check_for_update(force=True)
            self.assertTrue(checked["updateAvailable"])
            self.assertEqual(self.fetch.call_count, 2)

    def test_explicit_check_reports_error_instead_of_presenting_stale_success(self):
        lifecycle.check_for_update()
        self.fetch.side_effect = TimeoutError("offline")
        checked = lifecycle.check_for_update(force=True)
        self.assertIn("error", checked)
        self.assertFalse(checked["updateAvailable"])
        self.assertIsNone(checked["latestVersion"])

    def test_invalid_cache_timestamps_do_not_suppress_refresh(self):
        for invalid in (True, "yesterday", float("nan"), float("inf"), 10**400, self.now + 1, -1):
            with self.subTest(timestamp=str(invalid)):
                state.write_private_json(state.update_state_path(), {
                    "checkedAtEpoch": invalid, "update": self.release(),
                })
                before = self.fetch.call_count
                lifecycle.check_for_update()
                self.assertEqual(self.fetch.call_count, before + 1)

    def test_truncated_non_utf8_and_non_object_caches_repair(self):
        state.update_state_path().parent.mkdir(parents=True, exist_ok=True)
        for data in (b"{broken", b"\xff", b"[]", b'{"checkedAtEpoch": 1700000000, "update": []}'):
            with self.subTest(data=data):
                state.update_state_path().write_bytes(data)
                self.assertTrue(lifecycle.check_for_update()["updateAvailable"])
                self.assertIsInstance(json.loads(state.update_state_path().read_text()), dict)
        self.assertEqual(self.fetch.call_count, 4)

    def test_untrusted_cached_release_url_is_not_shown(self):
        for page in ("https://evil.example/update", ["unexpected"], None):
            with self.subTest(page=page):
                state.write_private_json(state.update_state_path(), {
                    "checkedAtEpoch": self.now, "update": {**self.release(), "releasePage": page},
                })
                self.assertIsNone(lifecycle.cached_update_notice())

    def test_read_only_cache_uses_process_memory_for_check_and_notice_throttling(self):
        with patch.object(state, "write_private_json", side_effect=OSError("read-only")):
            self.assertIn("0.7.0", lifecycle.automatic_update_notice())
            self.assertIsNone(lifecycle.automatic_update_notice())
            self.fetch.assert_called_once()
            self.now += 86400
            self.assertIn("0.7.0", lifecycle.automatic_update_notice())
            self.assertEqual(self.fetch.call_count, 2)

    def test_read_only_cache_does_not_lose_release_for_explicit_update(self):
        with patch.object(state, "write_private_json", side_effect=OSError("read-only")):
            checked = lifecycle.check_for_update(force=True)
            self.assertEqual(checked["release"]["tag_name"], "v0.7.0")
            self.assertTrue(checked["updateAvailable"])

    def test_mcp_status_uses_the_same_volatile_release_as_the_notice(self):
        with patch.object(state, "write_private_json", side_effect=OSError("read-only")):
            response = mcp.mcp_dispatch(self.request("cli_status"))
        result = response["result"]
        self.assertEqual(result["structuredContent"]["update"]["latestVersion"], "0.7.0")
        self.assertEqual(result["_meta"]["holycrab/update"]["latestVersion"], "0.7.0")

    def test_memory_cache_is_scoped_to_config_directory(self):
        with patch.object(state, "write_private_json", side_effect=OSError("read-only")):
            lifecycle.check_for_update()
        with patch.dict(os.environ, {"HOLYCRAB_CONFIG_DIR": str(self.root / "different")}):
            lifecycle.check_for_update()
        self.assertEqual(self.fetch.call_count, 2)

    def test_memory_cache_does_not_hide_a_newer_cross_process_refresh(self):
        with patch.object(state, "write_private_json", side_effect=OSError("read-only")):
            lifecycle.check_for_update()
        self.now += 1
        state.write_private_json(state.update_state_path(), {
            "checkedAtEpoch": self.now, "update": self.release("0.8.0"),
        })
        self.assertIn("0.8.0", lifecycle.cached_update_notice())

    def test_unavailable_lock_still_uses_memory_cache(self):
        @contextmanager
        def unavailable(*args, **kwargs):
            raise OSError(errno.EROFS, "read-only filesystem")
            yield
        with patch.object(state, "file_lock", unavailable), \
                patch.object(state, "write_private_json", side_effect=OSError("read-only")):
            self.assertIn("0.7.0", lifecycle.automatic_update_notice())
            self.assertIsNone(lifecycle.automatic_update_notice())
        self.fetch.assert_called_once()

    def test_busy_lock_skips_automatic_network_and_notice_without_waiting(self):
        @contextmanager
        def busy(*args, **kwargs):
            raise BlockingIOError(errno.EAGAIN, "busy")
            yield
        with patch.object(state, "file_lock", busy):
            self.assertFalse(lifecycle.check_for_update()["updateAvailable"])
            self.assertIsNone(lifecycle.automatic_update_notice())
        self.fetch.assert_not_called()

    def test_windows_eacces_distinguishes_byte_lock_from_unwritable_storage(self):
        @contextmanager
        def denied(*args, **kwargs):
            raise OSError(errno.EACCES, "access denied")
            yield
        cache_path = state.update_state_path()
        cache_path.parent.mkdir(parents=True)
        lock_path = cache_path.with_suffix(".lock")
        lock_path.touch()
        with patch.object(state, "update_state_path", return_value=cache_path), \
                patch.object(state, "file_lock", denied), patch.object(lifecycle.os, "name", "nt"):
            self.assertFalse(lifecycle.check_for_update()["updateAvailable"])
        self.fetch.assert_not_called()
        lock_path.unlink()
        with patch.object(state, "update_state_path", return_value=cache_path), \
                patch.object(state, "file_lock", denied), patch.object(lifecycle.os, "name", "nt"), \
                patch.object(state, "write_private_json", side_effect=PermissionError("read-only")):
            self.assertIn("0.7.0", lifecycle.automatic_update_notice())
            self.assertIsNone(lifecycle.automatic_update_notice())
        self.fetch.assert_called_once()

    def test_startup_maintenance_prints_only_one_cli_notice_to_stderr(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(lifecycle, "local_health_report", return_value={"repairs": []}), \
                redirect_stdout(output), redirect_stderr(errors):
            lifecycle.startup_maintenance(["models", "list"])
            lifecycle.startup_maintenance(["models", "list"])
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(errors.getvalue().count("HolyCrab update available:"), 1)

    def test_mcp_startup_does_not_consume_cli_notice(self):
        errors = io.StringIO()
        with patch.object(lifecycle, "local_health_report", return_value={"repairs": []}), redirect_stderr(errors):
            lifecycle.startup_maintenance(["mcp", "serve"])
        self.assertEqual(errors.getvalue(), "")
        self.assertIn("0.7.0", lifecycle.automatic_update_notice())

    def test_mcp_first_tool_notice_matches_metadata_and_preserves_business_result(self):
        value = {"taskId": "task-1", "state": "unknown", "nextAction": {"command": None}}
        with patch.object(mcp, "mcp_tool_call", return_value=value):
            response = mcp.mcp_dispatch(self.request())
        result = response["result"]
        self.assertEqual(result["structuredContent"], value)
        self.assertEqual(json.loads(result["content"][0]["text"]), value)
        self.assertFalse(result["isError"])
        notification = result["_meta"]["holycrab/update"]
        self.assertEqual(notification["latestVersion"], "0.7.0")
        self.assertIn(notification["message"], result["content"][1]["text"])
        self.assertIn("never install", result["content"][1]["text"])

    def test_initialize_notice_does_not_consume_first_tool_reminder(self):
        lifecycle.check_for_update()
        response = mcp.mcp_dispatch({"id": 1, "method": "initialize"})
        self.assertIn("0.7.0", response["result"]["instructions"])
        self.assertIn("_meta", mcp.mcp_dispatch(self.request())["result"])
        self.assertNotIn("_meta", mcp.mcp_dispatch(self.request())["result"])

    def test_mcp_long_session_refreshes_but_only_notifies_newer_versions(self):
        self.assertIn("_meta", mcp.mcp_dispatch(self.request())["result"])
        self.now += 86400
        self.assertNotIn("_meta", mcp.mcp_dispatch(self.request())["result"])
        self.assertEqual(self.fetch.call_count, 2)
        self.now += 86400
        self.fetch.return_value = self.release("0.8.0")
        result = mcp.mcp_dispatch(self.request())["result"]
        self.assertEqual(result["_meta"]["holycrab/update"]["latestVersion"], "0.8.0")
        self.now += 86400
        self.fetch.return_value = self.release("0.7.0")
        self.assertNotIn("_meta", mcp.mcp_dispatch(self.request())["result"])

    def test_mcp_failed_check_retries_after_an_hour_without_failing_tool(self):
        self.fetch.side_effect = urllib.error.URLError("offline")
        first = mcp.mcp_dispatch(self.request())["result"]
        self.assertFalse(first["isError"])
        self.assertNotIn("_meta", first)
        self.now += 3599
        mcp.mcp_dispatch(self.request())
        self.fetch.assert_called_once()
        self.now += 1
        self.fetch.side_effect = None
        self.assertIn("_meta", mcp.mcp_dispatch(self.request())["result"])

    def test_mcp_notification_does_not_change_error_or_interruption_semantics(self):
        for exception in (SystemExit("original business error"),
                          results.CommandInterrupted({"taskId": "kept", "state": "unknown"})):
            with self.subTest(error=type(exception).__name__):
                mcp._update_versions_notified.clear()
                with patch.object(mcp, "mcp_tool_call", side_effect=exception):
                    response = mcp.mcp_dispatch(self.request())
                result = response["result"]
                self.assertTrue(result["isError"])
                self.assertIn("_meta", result)
                if isinstance(exception, results.CommandInterrupted):
                    self.assertEqual(result["structuredContent"], {"taskId": "kept", "state": "unknown"})
                else:
                    self.assertEqual(result["content"][0]["text"], "original business error")

    def test_mcp_unexpected_maintenance_exception_does_not_skip_business_call(self):
        with patch.object(lifecycle, "check_for_update", side_effect=RuntimeError("cache bug")), \
                patch.object(mcp, "mcp_tool_call", return_value={"ok": True}) as tool:
            response = mcp.mcp_dispatch(self.request())
        tool.assert_called_once()
        self.assertEqual(response["result"]["structuredContent"], {"ok": True})
        self.assertNotIn("_meta", response["result"])

    def test_mcp_serve_outputs_only_json_rpc_and_resets_server_session_deduplication(self):
        mcp.mcp_dispatch(self.request())
        messages = [self.request(), {"id": 8, "method": "ping"}, self.request("unknown")]
        output = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO("\n".join(map(json.dumps, messages)) + "\n")), \
                redirect_stdout(output):
            self.assertEqual(mcp.command_mcp_serve(argparse.Namespace()), 0)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(responses), 3)
        self.assertTrue(all(row["jsonrpc"] == "2.0" for row in responses))
        self.assertIn("_meta", responses[0]["result"])
        self.assertEqual(responses[1]["result"], {})
        self.assertTrue(responses[2]["result"]["isError"])
        self.assertNotIn("_meta", responses[2]["result"])


class CrossProcessNotificationTests(unittest.TestCase):
    def test_concurrent_cli_processes_share_one_check_and_one_reminder(self):
        # Independent Python processes use the real OS lock and atomic cache.
        # Only the remote fetch is replaced; no network or account is involved.
        script = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from holycrab_runtime import lifecycle, settings
def fetch(timeout):
    with Path(sys.argv[2]).open('a') as log:
        log.write('fetch\\n')
    time.sleep(0.15)
    return lifecycle.release_update_info({'tag_name': 'v99.0.0', 'draft': False,
        'prerelease': False, 'html_url': settings.RELEASE_PAGE_PREFIX + 'v99.0.0'})
lifecycle.fetch_latest_release = fetch
notice = lifecycle.automatic_update_notice()
print(notice or 'no-notice')
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = {**os.environ, "HOLYCRAB_CONFIG_DIR": str(root / "config"),
                           "HOLYCRAB_NO_UPDATE_CHECK": "0"}
            command = [sys.executable, "-c", script, str(ROOT / "holycrab/scripts"), str(root / "fetch.log")]
            processes = [subprocess.Popen(command, env=environment, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, text=True) for _ in range(6)]
            try:
                output = []
                for process in processes:
                    stdout, stderr = process.communicate(timeout=15)
                    self.assertEqual(process.returncode, 0, stderr)
                    output.append(stdout)
                self.assertEqual((root / "fetch.log").read_text().splitlines(), ["fetch"])
                self.assertEqual(sum(text.count("HolyCrab update available:") for text in output), 1)
                again = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=15)
                self.assertEqual(again.returncode, 0, again.stderr)
                self.assertEqual(again.stdout.strip(), "no-notice")
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                    process.wait()


if __name__ == "__main__":
    unittest.main()
