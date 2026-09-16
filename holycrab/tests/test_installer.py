from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).parents[2]


class InstallerTests(unittest.TestCase):
    RELEASE_FILES = {
        "SHA256_HOLYCRAB_CLI": REPO_ROOT / "holycrab" / "scripts" / "holycrab_cli.py",
        "SHA256_CAPABILITIES": REPO_ROOT / "holycrab" / "references" / "capabilities.json",
        "SHA256_LAUNCHER": REPO_ROOT / "bin" / "holycrab",
        "SHA256_SKILL": REPO_ROOT / "holycrab" / "SKILL.md",
        "SHA256_OPENAI_YAML": REPO_ROOT / "holycrab" / "agents" / "openai.yaml",
        "SHA256_SEGNO": REPO_ROOT / "holycrab" / "scripts" / "vendor" / "segno-1.6.6-py3-none-any.whl",
        "SHA256_SEGNO_LICENSE": REPO_ROOT / "holycrab" / "scripts" / "vendor" / "LICENSE.segno",
    }

    def test_fixed_commit_ref_is_independent_of_the_installed_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            curl = fake_bin / "curl"
            curl.write_text('#!/usr/bin/env python3\nimport os, shutil, sys\nfrom pathlib import Path\n'
                            'url = next(arg for arg in sys.argv if arg.startswith("https://"))\n'
                            'ref, relative = url.split("/skills/", 1)[1].split("/", 1)\n'
                            'assert ref == os.environ["HOLYCRAB_INSTALL_REF"]\n'
                            'shutil.copyfile(Path(os.environ["FAKE_RELEASE_ROOT"]) / relative, sys.argv[sys.argv.index("-o")+1])\n',
                            encoding="utf-8")
            curl.chmod(0o755)
            env = {**os.environ, "HOME": str(root / "home"), "HOLYCRAB_CONFIG_DIR": str(root / "config"),
                   "HOLYCRAB_INSTALL_PREFIX": str(root / "prefix"), "HOLYCRAB_INSTALL_REF": "a" * 40,
                   "FAKE_RELEASE_ROOT": str(REPO_ROOT), "HOLYCRAB_INSTALL_AGENTS": "none", "HOLYCRAB_INSTALL_MCP": "0",
                   "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"]}
            env.pop("HOLYCRAB_INSTALL_SOURCE_DIR", None)
            result = subprocess.run(["sh", str(REPO_ROOT / "install.sh")], env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            version = subprocess.run([str(root / "prefix/bin/holycrab"), "--version"], env=env, text=True, capture_output=True)
            self.assertEqual(version.stdout.strip(), "holycrab 0.4.2")
            self.assertIn("Next: holycrab setup", result.stdout)
            self.assertNotIn("account is connected", result.stdout)

    def test_invalid_download_ref_is_rejected_before_installing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            for ref in ("main", "../main", "v0.4.2-rc1", "a" * 39, "a?token=secret"):
                with self.subTest(ref=ref):
                    env = {**os.environ, "HOME": temporary, "HOLYCRAB_INSTALL_REF": ref,
                           "HOLYCRAB_INSTALL_SOURCE_DIR": str(REPO_ROOT), "HOLYCRAB_INSTALL_AGENTS": "none"}
                    result = subprocess.run(["sh", str(REPO_ROOT / "install.sh")], env=env, text=True, capture_output=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("HOLYCRAB_INSTALL_REF", result.stderr)
                    self.assertFalse((Path(temporary) / ".local/lib/holycrab").exists())

    def test_public_install_uses_the_branded_stable_entrypoint(self) -> None:
        installer = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        powershell_installer = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("VERSION=v0.4.2", installer)
        self.assertIn('$Version = "v0.4.2"', powershell_installer)

        stable_urls = (
            "https://holycrab.ai/cli/install.sh",
            "https://holycrab.ai/cli/install.ps1",
        )
        versioned_raw_url = "https://raw.githubusercontent.com/AstroxNetwork/skills/v0.4.1/install.sh"
        for document in (REPO_ROOT / "README.md", REPO_ROOT / "HolyCrab CLI 使用指南.md"):
            content = document.read_text(encoding="utf-8")
            for stable_url in stable_urls:
                self.assertIn(stable_url, content)
            self.assertNotIn(versioned_raw_url, content)
            self.assertNotIn(
                "https://raw.githubusercontent.com/AstroxNetwork/skills/main/install.sh",
                content,
            )

    def test_installs_cli_and_skill_into_isolated_home(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env.update(
                {
                    "HOME": home,
                    "HOLYCRAB_INSTALL_SOURCE_DIR": str(REPO_ROOT),
                    "HOLYCRAB_INSTALL_MCP": "0",
                    "HOLYCRAB_INSTALL_AGENTS": "codex,claude",
                }
            )
            result = subprocess.run(
                ["sh", str(REPO_ROOT / "install.sh")],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("Downloading or copying", result.stderr)
            command = Path(home) / ".local" / "bin" / "holycrab"
            self.assertTrue(command.is_file())
            self.assertTrue((Path(home) / ".agents" / "skills" / "holycrab" / "SKILL.md").is_file())
            self.assertTrue((Path(home) / ".agents" / "skills" / "holycrab" / "agents" / "openai.yaml").is_file())
            self.assertTrue((Path(home) / ".claude" / "skills" / "holycrab" / "SKILL.md").is_file())
            self.assertTrue((Path(home) / ".claude" / "skills" / "holycrab" / "agents" / "openai.yaml").is_file())
            manifest = json.loads(
                (Path(home) / ".local" / "lib" / "holycrab" / "installation.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["schemaVersion"], 2)
            self.assertEqual(manifest["managedBy"], "holycrab-installer")
            self.assertEqual(manifest["pathRegistration"]["kind"], "shell-profile")
            self.assertTrue(manifest["pathRegistration"]["addedByInstaller"])
            check = subprocess.run(
                [str(command), "doctor", "--json"],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(check.returncode, 0, check.stderr)
            handshake = subprocess.run(
                [str(command), "mcp", "serve"],
                env=env,
                input='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}\n',
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(handshake.returncode, 0, handshake.stderr)
            self.assertIn('"name":"holycrab-local"', handshake.stdout)
            self.assertIn('"protocolVersion":"2025-11-25"', handshake.stdout)

    def test_installer_persists_zsh_path_once_without_replacing_user_content(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            profile = Path(home) / ".zshrc"
            profile.write_text("export USER_SETTING=kept\n", encoding="utf-8")
            env = {
                **os.environ,
                "HOME": home,
                "SHELL": "/bin/zsh",
                "HOLYCRAB_INSTALL_SOURCE_DIR": str(REPO_ROOT),
                "HOLYCRAB_INSTALL_MCP": "0",
                "HOLYCRAB_INSTALL_AGENTS": "none",
            }
            for install_number in range(2):
                if install_number == 1:
                    env["SHELL"] = "/bin/bash"
                result = subprocess.run(
                    ["sh", str(REPO_ROOT / "install.sh")],
                    env=env,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

            content = profile.read_text(encoding="utf-8")
            self.assertIn("export USER_SETTING=kept", content)
            self.assertEqual(content.count("# HolyCrab CLI"), 1)
            self.assertIn('export PATH="$HOME/.local/bin:$PATH"', content)
            self.assertIn("PATH is active inside the installer", result.stdout)
            manifest = json.loads(
                (Path(home) / ".local" / "lib" / "holycrab" / "installation.json").read_text(encoding="utf-8")
            )
            self.assertTrue(manifest["pathRegistration"]["addedByInstaller"])
            self.assertEqual(Path(manifest["pathRegistration"]["profile"]).resolve(), profile.resolve())
            self.assertFalse((Path(home) / ".bashrc").exists())
            self.assertFalse((Path(home) / ".bash_profile").exists())

    def test_installer_success_messages_follow_all_verification(self) -> None:
        for name, marker, success in (("install.sh", "INSTALL_COMPLETE=1", 'echo "HolyCrab CLI, local MCP, and Skill are installed."'),
                                      ("install.ps1", "$InstallComplete = $true", 'Write-Host "HolyCrab CLI, local MCP, and Skill are installed."')):
            with self.subTest(installer=name):
                text = (REPO_ROOT / name).read_text(encoding="utf-8")
                self.assertLess(text.index(marker), text.index(success))
                self.assertIn("Verifying the installed CLI version and local health", text)
                self.assertIn("Installing Skills", text)

    def test_unix_installer_sigint_restores_existing_files_without_success_message(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {**os.environ, "HOME": str(root), "HOLYCRAB_CONFIG_DIR": str(root / "config"),
                "HOLYCRAB_INSTALL_SOURCE_DIR": str(REPO_ROOT), "HOLYCRAB_INSTALL_AGENTS": "none",
                "HOLYCRAB_INSTALL_MCP": "0", "HOLYCRAB_INSTALL_PREFIX": str(root / "prefix")}
            initial = subprocess.run(["sh", str(REPO_ROOT / "install.sh")], env=env, text=True, capture_output=True)
            self.assertEqual(initial.returncode, 0, initial.stderr)
            old = (root / "prefix/lib/holycrab/holycrab_cli.py").read_bytes()
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            actual_python = shutil.which("python3")
            self.assertIsNotNone(actual_python)
            fake_python = fake_bin / "python3"
            fake_python.write_text('#!/bin/sh\ncase "$*" in *doctor*) kill -INT "$PPID"; exit 130;; esac\nexec "' + str(actual_python) + '" "$@"\n')
            fake_python.chmod(0o755)
            env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
            interrupted = subprocess.run(["sh", str(REPO_ROOT / "install.sh")], env=env, text=True, capture_output=True, timeout=15)
            self.assertEqual(interrupted.returncode, 130, interrupted.stdout + interrupted.stderr)
            self.assertNotIn("are installed", interrupted.stdout)
            self.assertEqual((root / "prefix/lib/holycrab/holycrab_cli.py").read_bytes(), old)
            self.assertIn("restored", interrupted.stderr)

    def test_fresh_install_and_v040_upgrade_preserve_state_and_qr_without_pip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prefix = root / "prefix"
            config = root / "config"
            config.mkdir()
            config_file = config / "config.json"
            config_file.write_text('{"apiKey":"test-only-upgrade-key","installMarker":"preserved"}')
            config_file.chmod(0o600)
            state_files = {
                "attempts.json": '{"upgrade-attempt":{"state":"unknown"}}',
                "update-state.json": '{"checkedAtEpoch":1,"update":{"updateAvailable":false}}',
                "health-state.json": '{"files":{}}',
                "upload-plans/upgrade-plan.json": json.dumps({
                    "state": "prepared", "files": [], "expiresAtEpoch": time.time() + 3600,
                }),
            }
            for relative, contents in state_files.items():
                path = config / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(contents, encoding="utf-8")
                path.chmod(0o600)
            env = {**os.environ, "HOLYCRAB_INSTALL_SOURCE_DIR": str(REPO_ROOT),
                   "HOLYCRAB_INSTALL_PREFIX": str(prefix), "HOLYCRAB_CONFIG_DIR": str(config),
                   "HOLYCRAB_INSTALL_AGENTS": "none", "HOLYCRAB_INSTALL_MCP": "0"}
            for previous_version in (None, "0.4.0", "0.4.1"):
                if previous_version is not None:
                    # Simulate an older CLI, then reinstall without touching local user state.
                    (prefix / "lib" / "holycrab" / "holycrab_cli.py").write_text('VERSION = "' + previous_version + '"\n')
                installed = subprocess.run(["sh", str(REPO_ROOT / "install.sh")], env=env,
                                           text=True, capture_output=True)
                self.assertEqual(installed.returncode, 0, installed.stderr)
                program = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location('installed', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
raw = module.qr_png('https://example.com/verification?pl=offline-test')
assert raw.startswith(b'\\x89PNG\\r\\n\\x1a\\n')
assert len(raw) > 100
assert 'real_human_authorization_start' in {t['name'] for t in module.MCP_TOOLS}
assert 'real_human_group_delete' in {t['name'] for t in module.MCP_TOOLS}
"""
                result = subprocess.run(["python3", "-S", "-c", program,
                                         str(prefix / "lib" / "holycrab" / "holycrab_cli.py")],
                                        env=env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    config_file.read_text(),
                    '{"apiKey":"test-only-upgrade-key","installMarker":"preserved"}',
                )
                attempts = json.loads((config / "attempts.json").read_text(encoding="utf-8"))
                self.assertEqual(attempts["upgrade-attempt"]["state"], "unknown")
                self.assertEqual(
                    json.loads((config / "update-state.json").read_text(encoding="utf-8"))["update"]["updateAvailable"],
                    False,
                )
                self.assertIsInstance(
                    json.loads((config / "health-state.json").read_text(encoding="utf-8")), dict
                )
                upload_plan = json.loads(
                    (config / "upload-plans" / "upgrade-plan.json").read_text(encoding="utf-8")
                )
                self.assertEqual(upload_plan["state"], "prepared")
                self.assertTrue((prefix / "lib" / "holycrab" / "vendor" / "LICENSE.segno").is_file())

    def test_failed_upgrade_restores_managed_files_and_preserves_user_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            prefix = root / "prefix"
            config = root / "config"
            source = root / "corrupt-release"
            shutil.copytree(REPO_ROOT / "holycrab", source / "holycrab")
            shutil.copytree(REPO_ROOT / "bin", source / "bin")
            cli_source = source / "holycrab" / "scripts" / "holycrab_cli.py"
            cli_source.write_text(cli_source.read_text(encoding="utf-8") + "\n# corrupt release fixture\n", encoding="utf-8")

            old_lib = prefix / "lib" / "holycrab"
            old_lib.mkdir(parents=True)
            (old_lib / "old.txt").write_text("old-lib", encoding="utf-8")
            old_bin = prefix / "bin"
            old_bin.mkdir(parents=True)
            (old_bin / "holycrab").write_text("old-launcher", encoding="utf-8")
            for destination in (home / ".agents" / "skills" / "holycrab",
                                home / ".claude" / "skills" / "holycrab"):
                destination.mkdir(parents=True)
                (destination / "old.txt").write_text("old-skill", encoding="utf-8")
            config.mkdir(parents=True)
            (config / "config.json").write_text('{"apiKey":"preserved-key"}', encoding="utf-8")
            (config / "attempts.json").write_text('{"attempt1":{"state":"unknown"}}', encoding="utf-8")
            os.chmod(config / "config.json", 0o600)
            os.chmod(config / "attempts.json", 0o600)

            env = {**os.environ, "HOME": str(home), "HOLYCRAB_INSTALL_SOURCE_DIR": str(source),
                   "HOLYCRAB_INSTALL_PREFIX": str(prefix), "HOLYCRAB_CONFIG_DIR": str(config),
                   "HOLYCRAB_INSTALL_AGENTS": "codex,claude", "HOLYCRAB_INSTALL_MCP": "0"}
            result = subprocess.run(["sh", str(REPO_ROOT / "install.sh")], env=env,
                                    text=True, capture_output=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("previous managed files were restored", result.stderr)
            self.assertEqual((old_lib / "old.txt").read_text(encoding="utf-8"), "old-lib")
            self.assertEqual((old_bin / "holycrab").read_text(encoding="utf-8"), "old-launcher")
            self.assertEqual((home / ".agents" / "skills" / "holycrab" / "old.txt").read_text(), "old-skill")
            self.assertEqual((home / ".claude" / "skills" / "holycrab" / "old.txt").read_text(), "old-skill")
            self.assertIn("preserved-key", (config / "config.json").read_text(encoding="utf-8"))
            self.assertIn("attempt1", (config / "attempts.json").read_text(encoding="utf-8"))

    def test_remote_release_hashes_match_the_bundled_files(self) -> None:
        installer = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        for variable, path in self.RELEASE_FILES.items():
            match = re.search(rf"^{variable}=([0-9a-f]{{64}})$", installer, re.MULTILINE)
            self.assertIsNotNone(match, f"missing {variable}")
            expected = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(match.group(1), expected, path)

    def test_remote_install_rejects_a_tampered_release_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_curl = fake_bin / "curl"
            fake_curl.write_text(
                """#!/bin/sh
set -eu
url=
destination=
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) destination=$2; shift 2 ;;
    http*) url=$1; shift ;;
    *) shift ;;
  esac
done
relative=${url#*/v0.4.2/}
cp "$FAKE_RELEASE_ROOT/$relative" "$destination"
if [ "$relative" = "holycrab/scripts/holycrab_cli.py" ]; then
  printf '\\n# tampered\\n' >> "$destination"
fi
""",
                encoding="utf-8",
            )
            fake_curl.chmod(0o755)
            env = os.environ.copy()
            env.pop("HOLYCRAB_INSTALL_SOURCE_DIR", None)
            env.update(
                {
                    "HOME": str(root / "home"),
                    "FAKE_RELEASE_ROOT": str(REPO_ROOT),
                    "HOLYCRAB_INSTALL_MCP": "0",
                    "HOLYCRAB_INSTALL_AGENTS": "codex",
                    "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
                }
            )
            result = subprocess.run(
                ["sh", str(REPO_ROOT / "install.sh")],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SHA-256 verification failed", result.stderr)

    def test_installer_requires_python_310_or_newer(self) -> None:
        installer = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("sys.version_info >= (3, 10)", installer)

        powershell_installer = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("Python 3.10 or newer", powershell_installer)
        self.assertIn("winget install --id Python.Python.3.12", powershell_installer)
        self.assertIn("SetEnvironmentVariable", powershell_installer)
        self.assertIn("holycrab.cmd", powershell_installer)
        self.assertIn("chcp 65001", powershell_installer)
        self.assertIn("PYTHONUTF8=1", powershell_installer)
        self.assertIn('@("-X", "utf8", $CliPath, "mcp", "serve")', powershell_installer)
        self.assertIn("-Raw -Encoding UTF8 | ConvertFrom-Json", powershell_installer)

        attributes = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
        for release_file in ("/holycrab/SKILL.md", "/holycrab/references/*.json",
                             "/holycrab/scripts/*.py", "/holycrab/scripts/vendor/LICENSE.segno"):
            self.assertIn(f"{release_file} text eol=lf", attributes)
        self.assertIn("& $Launcher doctor --json", powershell_installer)

    def test_docs_explain_supported_systems_update_and_uninstall(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Python 3.10+", readme)
        self.assertIn("macOS", readme)
        self.assertIn("Linux", readme)
        self.assertIn("覆盖安装", readme)
        self.assertIn("卸载", readme)
        self.assertIn("holycrab uninstall --purge --yes", readme)
        self.assertNotIn('rm -rf "$HOME/.local/lib/holycrab"', readme)
        skill = (REPO_ROOT / "holycrab" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("not an MCP tool", skill)
        self.assertIn("explicitly asks to uninstall", skill)

    def test_ci_runs_release_gates_on_linux_macos_and_windows(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        for token in (
            "ubuntu-latest",
            "macos-latest",
            "windows-latest",
            "python3 -m unittest discover -s holycrab/tests -v",
            "python3 -m py_compile",
            "validate_capabilities.py",
            "quick_validate.py",
            "49f948faa9258a0c61caceaf225e179651397431",
            "HOLYCRAB_INSTALL_SOURCE_DIR",
            "test_windows_installer.ps1",
            "mcp serve",
        ):
            self.assertIn(token, workflow)

    def test_tagged_release_uploads_the_installer_after_all_release_gates(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        for token in (
            "needs: [quality, official-skill-validation, generate-contract, install-and-mcp, install-windows]",
            "if: startsWith(github.ref, 'refs/tags/v')",
            "contents: write",
            'release_version="$GITHUB_REF_NAME"',
            'grep -Fx "VERSION=$release_version" install.sh',
            'test -f ".github/releases/$release_version.md"',
            "sha256sum install.sh install.ps1 > SHA256SUMS",
            'gh release create "$release_version"',
            "install.sh install.ps1 SHA256SUMS",
            "--verify-tag",
            "--latest",
            '--notes-file ".github/releases/$release_version.md"',
        ):
            self.assertIn(token, workflow)
        self.assertNotIn("--clobber", workflow)
        self.assertNotIn('gh release upload "$release_version"', workflow)

    def test_release_notes_have_the_approved_english_title(self) -> None:
        notes = (REPO_ROOT / ".github" / "releases" / "v0.4.2.md").read_text(encoding="utf-8")
        self.assertTrue(notes.startswith("# HolyCrab Agent Tools v0.4.2\n"))
        self.assertIn("SHA-256", notes)
        self.assertIn("Windows", notes)
        self.assertIn("DPAPI", notes)


if __name__ == "__main__":
    unittest.main()
