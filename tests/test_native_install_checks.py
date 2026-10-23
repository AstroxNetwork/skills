"""Verify native CI helpers on disposable files, including deliberately broken fixtures."""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("native_checks", ROOT / "tools/native_install_checks.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


class NativeCheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.library = self.root / "installed"
        runtime = self.source / "holycrab/scripts/holycrab_runtime"
        runtime.mkdir(parents=True)
        (runtime / "settings.py").write_text('VERSION = "0.6.0"\n')
        shim = self.source / "holycrab/scripts/holycrab_cli.py"
        shim.write_text('from holycrab_runtime.settings import VERSION\nprint("holycrab " + VERSION)\n')
        rows = []
        for path in (runtime / "settings.py", shim):
            target = path.relative_to(self.source / "holycrab/scripts")
            rows.append({"source": path.relative_to(self.source).as_posix(), "target": target.as_posix(),
                         "kind": "runtime", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        (self.source / "runtime-files.json").write_text(json.dumps({"files": rows}))
        shutil.copytree(self.source / "holycrab/scripts", self.library)
        shutil.copy2(self.source / "runtime-files.json", self.library / "runtime-files.json")
        (self.library / "installation.json").write_text('{}')

    def test_version_reading_and_fixture_actually_change_running_package(self):
        self.assertEqual(checks.read_version(self.source / "holycrab/scripts"), "0.6.0")
        for version in ("0.4.0", "0.5.0", "0.6.0"):
            checks.set_fixture_version(self.library, version)
            result = subprocess.run([sys.executable, str(self.library / "holycrab_cli.py"), "--version"],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "holycrab " + version)

    def test_unchanged_complete_installed_inventory_passes(self):
        checks.verify_runtime(self.library, self.source)

    def test_missing_extra_and_modified_runtime_fail(self):
        module = self.library / "holycrab_runtime/settings.py"
        original = module.read_bytes()
        module.unlink()
        with self.assertRaisesRegex(ValueError, "missing="):
            checks.verify_runtime(self.library, self.source)
        module.write_bytes(original)
        extra = self.library / "unexpected.py"
        extra.write_text("unexpected")
        with self.assertRaisesRegex(ValueError, "extra="):
            checks.verify_runtime(self.library, self.source)
        extra.unlink()
        module.write_text('VERSION = "9.9.9"\n')
        with self.assertRaisesRegex(ValueError, "bytes differ"):
            checks.verify_runtime(self.library, self.source)

    def test_manifest_cannot_hide_an_omitted_source_module(self):
        (self.source / "holycrab/scripts/holycrab_runtime/new_module.py").write_text("value = 1\n")
        with self.assertRaisesRegex(ValueError, "complete source package"):
            checks.verify_runtime(self.library, self.source)

    def test_matching_modified_manifest_does_not_hide_different_source_bytes(self):
        source_path = self.source / "holycrab/scripts/holycrab_runtime/settings.py"
        source_path.write_text('VERSION = "9.9.9"\n')
        with self.assertRaisesRegex(ValueError, "bytes differ"):
            checks.verify_runtime(self.library, self.source)

    def test_snapshot_comparison_checks_bytes_and_missing_files(self):
        snapshot = self.root / "before.json"
        command = [sys.executable, str(ROOT / "tools/native_install_checks.py")]
        subprocess.run(command + ["snapshot", str(self.library), str(snapshot)], check=True, timeout=5)
        subprocess.run(command + ["compare-snapshot", str(self.library), str(snapshot)], check=True, timeout=5)
        (self.library / "holycrab_runtime/settings.py").unlink()
        changed = subprocess.run(command + ["compare-snapshot", str(self.library), str(snapshot)],
                                 capture_output=True, text=True, timeout=5)
        self.assertNotEqual(changed.returncode, 0)

    def test_broken_source_reaches_doctor_failure_with_other_commands_intact(self):
        target = self.root / "broken-source"
        checks.make_broken_source(target)
        script = target / "holycrab/scripts/holycrab_cli.py"
        version = subprocess.run([sys.executable, str(script), "--version"], capture_output=True, text=True, timeout=5)
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertEqual(version.stdout.strip(), "holycrab " + checks.read_version(ROOT / "holycrab/scripts"))
        doctor = subprocess.run([sys.executable, str(script), "doctor", "--json"], capture_output=True, text=True, timeout=5)
        self.assertEqual(doctor.returncode, 7)
        self.assertEqual(json.loads(doctor.stdout), {"ok": False, "runtimeOk": False})
        manifest = json.loads((target / "runtime-files.json").read_text())
        for row in manifest["files"]:
            self.assertEqual(hashlib.sha256((target / row["source"]).read_bytes()).hexdigest(), row["sha256"])

    @unittest.skipIf(os.name == "nt", "Native PowerShell rollback is covered by test_windows_installer.ps1")
    def test_broken_health_candidate_rolls_back_real_isolated_install(self):
        env = {**os.environ, "HOME": str(self.root / "home"), "HOLYCRAB_CONFIG_DIR": str(self.root / "config"),
               "HOLYCRAB_INSTALL_PREFIX": str(self.root / "prefix"), "HOLYCRAB_INSTALL_SOURCE_DIR": str(ROOT),
               "HOLYCRAB_INSTALL_AGENTS": "none", "HOLYCRAB_INSTALL_MCP": "0", "HOLYCRAB_NO_UPDATE_CHECK": "1"}
        env.pop("HOLYCRAB_API_KEY", None)
        original = subprocess.run(["sh", str(ROOT / "install.sh")], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(original.returncode, 0, original.stdout + original.stderr)
        installed = self.root / "prefix/lib/holycrab"
        (installed / "user-note.txt").write_text("keep this file")
        before = checks.snapshot(installed)
        target = self.root / "broken-install"
        checks.make_broken_source(target)
        env["HOLYCRAB_INSTALL_SOURCE_DIR"] = str(target)
        failed = subprocess.run(["sh", str(target / "install.sh")], env=env, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("restored", failed.stderr)
        self.assertEqual(checks.snapshot(installed), before)


if __name__ == "__main__":
    unittest.main()
