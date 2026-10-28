"""Keep standalone acceptance drivers executable and explicit about live writes."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tests" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SmokeDriverTests(unittest.TestCase):
    def test_live_write_phases_require_fresh_explicit_opt_in(self):
        for phase in ("prepare", "create"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as root:
                result = subprocess.run([sys.executable, str(ROOT / "tests/live_release_smoke.py"), phase,
                                         "--root", root], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 2)
                self.assertIn("--allow-live-writes is required", result.stderr)
                self.assertEqual(list(Path(root).iterdir()), [])

    def test_live_inventory_tracks_candidate_and_rejects_missing_tools(self):
        live = load("live_release_smoke")
        with tempfile.TemporaryDirectory() as root:
            run = live.LiveRun(Path(root))
            self.assertEqual(run.inventory["version"], "0.7.0")
            self.assertIn("asset_upload_plan_get", {row["name"] for row in run.inventory["mcpTools"]})
            replies = [{"result": {"serverInfo": {"version": run.inventory["version"]}}},
                       {"result": {"tools": run.inventory["mcpTools"]}},
                       {"result": {"isError": False, "structuredContent": {"ok": True}}}]
            with patch.object(run, "cli", return_value=replies):
                self.assertEqual(run.mcp("cli_status", {}), {"ok": True})
                replies[1]["result"]["tools"] = run.inventory["mcpTools"][:-1]
                with self.assertRaisesRegex(RuntimeError, "inventory"):
                    run.mcp("cli_status", {})

    def test_live_prepare_requires_parameters_before_reading_user_files(self):
        run = load("live_release_smoke").LiveRun(Path(tempfile.gettempdir()) / "unused-smoke-fixture")
        with patch.object(run, "cli") as execute:
            with self.assertRaisesRegex(RuntimeError, "Explicit account"):
                run.prepare()
        execute.assert_not_called()

    def test_lifecycle_driver_propagates_subprocess_failure(self):
        smoke = load("local_lifecycle_smoke")
        with self.assertRaisesRegex(RuntimeError, "Unexpected exit 7"):
            smoke.checked([sys.executable, "-c", "raise SystemExit(7)"], os.environ.copy())

    def test_live_pending_or_empty_tasks_are_not_reported_as_success(self):
        live = load("live_release_smoke")
        for tasks in ({}, {kind: kind + '1' for kind in ('image', 'video', 'audio')}):
            with self.subTest(tasks=tasks), tempfile.TemporaryDirectory() as root:
                run = live.LiveRun(Path(root))
                run.state['tasks'] = tasks
                with patch.object(run, 'cli', return_value=[{}]), \
                        patch.object(run, 'mcp', return_value={'step': 1}), redirect_stdout(io.StringIO()):
                    self.assertEqual(run.check(), 2)
                self.assertFalse(run.state['businessComplete'])
                with patch.object(live, 'LiveRun', return_value=run), \
                        patch.object(run, 'check', return_value=2), \
                        patch.object(sys, 'argv', ['live_release_smoke.py', 'check', '--root', root]):
                    self.assertEqual(live.main(), 2)

    def test_live_complete_requires_three_successful_media_decodes(self):
        live = load("live_release_smoke")
        with tempfile.TemporaryDirectory() as root:
            run = live.LiveRun(Path(root))
            run.state['tasks'] = {kind: kind + '1' for kind in ('image', 'video', 'audio')}
            outputs = Path(root) / 'outputs'
            outputs.mkdir()
            for name in ('cat.jpg', 'cat.mp4', 'cat.mp3'):
                (outputs / name).write_bytes(b'offline fixture')
            probe = subprocess.CompletedProcess([], 0, json.dumps({'streams': [{'codec_type': 'video'}, {'codec_type': 'audio'}], 'format': {'duration': '1'}}))
            with patch.object(run, 'cli', return_value=[{}]), \
                    patch.object(run, 'mcp', return_value={'step': 2}), \
                    patch.object(live.sys, 'platform', 'linux'), \
                    patch.object(live.subprocess, 'run', return_value=probe) as decode, redirect_stdout(io.StringIO()):
                self.assertEqual(run.check(), 0)
            self.assertEqual(decode.call_count, 3)
            self.assertTrue(run.state['businessComplete'])

    @unittest.skipIf(os.name == "nt", "Native Windows lifecycle is tested by PowerShell CI")
    def test_disposable_install_rollback_and_uninstall(self):
        smoke = load("local_lifecycle_smoke")
        result = smoke.run_lifecycle()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["candidate"], "holycrab 0.7.0")
        self.assertIn("failed-upgrade-byte-rollback", result["checks"])
        self.assertTrue(result["temporaryHome"])
