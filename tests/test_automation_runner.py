"""Execute the validation harness against failing, skipped, empty and timed-out fixtures."""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_runner", ROOT / "tools/run_checks.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = runner.isolated_environment(self.root)

    def stage(self, code, timeout=5):
        return runner.run_stage("fixture", [sys.executable, "-c", code], directory=self.root,
                                env=self.env, timeout=timeout)

    def test_real_nonzero_exit_is_failure_with_usable_log(self):
        result = self.stage("import sys; print('expected failure'); sys.exit(17)")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["exitCode"], 17)
        self.assertIn("expected failure", Path(result["log"]).read_text())

    def test_real_success_and_missing_executable_have_different_states(self):
        self.assertEqual(self.stage("print('fixture passed')")["status"], "passed")
        result = runner.run_stage("missing", [str(self.root / "absent-python")], directory=self.root,
                                  env=self.env, timeout=5)
        self.assertEqual(result["status"], "unavailable")

    def test_timeout_terminates_the_actual_child(self):
        result = self.stage("import time; time.sleep(60)", timeout=0.2)
        self.assertEqual(result["status"], "timeout")
        self.assertIsNotNone(result["exitCode"])
        self.assertLess(result["seconds"], 15)

    def test_python_network_guard_blocks_unmocked_connections(self):
        result = self.stage("import socket; socket.create_connection(('127.0.0.1', 9))")
        self.assertEqual(result["status"], "failed")
        self.assertIn("Offline validation blocked", Path(result["log"]).read_text())

    def test_shell_installers_use_the_selected_test_python(self):
        command = 'python' if os.name == 'nt' else 'python3'
        result = self.stage("import subprocess; print(subprocess.check_output([" + repr(command)
                            + ", '-c', 'import sys; print(sys.version_info[:2])'], text=True).strip())")
        self.assertEqual(result['status'], 'passed')
        self.assertIn(str(sys.version_info[:2]), Path(result['log']).read_text())

    def test_environment_does_not_inherit_credentials_or_real_home(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
                "HOLYCRAB_API_KEY": "do-not-inherit", "HOLYCRAB_INSTALL_PREFIX": "/real/prefix",
                "CODEX_HOME": "/real/codex", "HOLYCRAB_GUIDE_SOURCE_DIR": "/stale/website"}):
            env = runner.isolated_environment(Path(temp))
            self.assertNotIn("HOLYCRAB_API_KEY", env)
            self.assertNotIn("HOLYCRAB_INSTALL_PREFIX", env)
            self.assertNotIn("CODEX_HOME", env)
            self.assertNotIn("HOLYCRAB_GUIDE_SOURCE_DIR", env)
            self.assertEqual(Path(env["HOME"]).parent, Path(temp))
            self.assertEqual(env["HOLYCRAB_NO_UPDATE_CHECK"], "1")

    def worker(self, body, full=False):
        tests = self.root / "fixture-tests"
        tests.mkdir()
        if body:
            (tests / "test_fixture.py").write_text("import unittest\n" + body)
        report = self.root / ("report.json" if full else "unit.json")
        command = [sys.executable, str(ROOT / "tools/run_checks.py"),
                   "--report" if full else "--unit-worker", str(report), "--test-directory", str(tests)]
        result = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=45)
        return result, json.loads(report.read_text())

    def test_discovery_finds_new_modules_and_reports_skips_separately(self):
        result, report = self.worker("class Fixture(unittest.TestCase):\n"
                                    " def test_new_case(self): self.assertEqual(3+4, 7)\n"
                                    " @unittest.skip('external input missing')\n"
                                    " def test_missing(self): pass\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["run"], 2)
        self.assertEqual(report["status"], "passed_with_skips")
        self.assertEqual(report["skipped"][0]["reason"], "external input missing")

    def test_test_failure_reaches_full_runner_exit_code_and_json(self):
        result, report = self.worker("class Fixture(unittest.TestCase):\n"
                                    " def test_failure(self): self.assertEqual(1, 2)\n", full=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(report["status"], "failed")
        unit = next(stage for stage in report["checks"] if stage["name"] == "unit")
        self.assertEqual(unit["status"], "failed")
        self.assertEqual(len(unit["tests"]["failures"]), 1)
        self.assertEqual(len(report["source"]["sha256"]), 64)
        self.assertTrue(any(stage["status"] == "skipped" for stage in report["checks"]))

    def test_empty_discovery_is_failure(self):
        result, report = self.worker("")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report["run"], 0)
        self.assertEqual(report["status"], "failed")

    def test_all_skipped_suite_is_failure_without_executed_tests(self):
        result, report = self.worker("class Fixture(unittest.TestCase):\n"
                                    " @unittest.skip('not available')\n"
                                    " def test_missing(self): pass\n")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report["run"], 1)
        self.assertEqual(report["executed"], 0)
        self.assertEqual(report["status"], "failed")

    def test_windows_filter_does_not_drop_new_test_modules(self):
        tests = []
        for name in ("test_installer.InstallerTests.test_unix", "test_future.FutureTests.test_new"):
            test = unittest.FunctionTestCase(lambda: None)
            test.id = lambda name=name: name
            tests.append(test)
        suite = runner.platform_suite(unittest.TestSuite(tests), "Windows")
        result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
        self.assertEqual(result.testsRun, 2)
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(result.skipped[0][0].id(), "test_installer.InstallerTests.test_unix")

    def test_fingerprint_changes_when_source_or_test_bytes_change(self):
        tools = self.root / "tools"
        tools.mkdir()
        file = tools / "check.py"
        file.write_text("first")
        before = runner.source_fingerprint(self.root)
        file.write_text("second")
        after = runner.source_fingerprint(self.root)
        self.assertNotEqual(before["sha256"], after["sha256"])

    def test_fingerprint_covers_public_docs_acceptance_and_nested_fixtures(self):
        for name in (".gitattributes", "README.md", "SECURITY.md", "docs/release-acceptance/v0.6.0.json", "tests/fixtures/request.json"):
            with self.subTest(path=name):
                path = self.root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("before")
                before = runner.source_fingerprint(self.root)
                path.write_text("after")
                after = runner.source_fingerprint(self.root)
                self.assertIn(name, before["files"])
                self.assertNotEqual(before["sha256"], after["sha256"])


if __name__ == "__main__":
    unittest.main()
