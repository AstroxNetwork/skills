from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).parents[1]


class InstallerTests(unittest.TestCase):
    def test_post_release_smoke_checks_both_native_platforms(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/post-release-smoke.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("os: [macos-latest, windows-latest]", workflow)
        self.assertIn("shell: powershell", workflow)
        self.assertIn('python-version: "3.10"', workflow)
        self.assertIn('AddSeconds(10)', workflow)

    def test_post_release_smoke_verifies_website_bytes_before_execution(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/post-release-smoke.yml").read_text(encoding="utf-8")
        self.assertIn('https://holycrab.ai/cli/', workflow)
        self.assertIn('Release is not the expected stable version', workflow)
        self.assertIn('sha256:', workflow)
        self.assertIn('Website installer differs from the immutable release', workflow)
        self.assertLess(workflow.index('Verify the deployed entry points'), workflow.index('Install on macOS'))
        self.assertLess(workflow.index('Verify the deployed entry points'), workflow.index('Install on PowerShell'))

    def test_post_release_smoke_has_no_business_writes_or_real_credentials(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/post-release-smoke.yml").read_text(encoding="utf-8")
        self.assertIn('HOLYCRAB_INSTALL_AGENTS: none', workflow)
        self.assertIn('HOLYCRAB_INSTALL_MCP: "0"', workflow)
        self.assertIn('HOLYCRAB_NO_UPDATE_CHECK: "1"', workflow)
        self.assertIn('"name": "cli_status"', workflow)
        self.assertIn('"name": "capabilities_list"', workflow)
        self.assertIn('"asset_upload" not in names', workflow)
        self.assertNotIn('generation_create', workflow)
        self.assertNotIn('auth status', workflow)
        self.assertNotIn('HOLYCRAB_API_KEY', workflow)
        self.assertNotIn('contents: write', workflow)

    def test_post_release_smoke_does_not_use_runner_context_in_job_env(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/post-release-smoke.yml").read_text(encoding="utf-8")
        job_env = workflow.split('    env:\n', 1)[1].split('    steps:\n', 1)[0]
        self.assertNotIn('${{ runner.temp }}', job_env)
        self.assertIn('GITHUB_ENV', workflow)

    def test_draft_verification_lists_releases_instead_of_published_tag_endpoint(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertNotIn('releases/tags/$release_version', workflow)
        self.assertIn('select(.tag_name == $version)', workflow)
        self.assertIn('release.get("draft") is True', workflow)

    def test_v043_resume_reuses_draft_without_overwriting_and_keeps_required_checks(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn('workflow_dispatch:', workflow)
        self.assertIn('options: [v0.4.3]', workflow)
        self.assertIn('Existing v0.4.3 Draft assets will only be verified, never overwritten', workflow)
        self.assertIn('needs: [quality, official-skill-validation, generate-contract, install-and-mcp, install-windows]', workflow)
        self.assertIn('needs: [release, verify-draft-install]', workflow)
        self.assertNotIn('--clobber', workflow)

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
            self.assertEqual(version.stdout.strip(), "holycrab 0.4.3")
            self.assertIn("Next: connect your account\n  holycrab setup", result.stdout)
            self.assertIn("HolyCrab 0.4.3 installed", result.stdout)
            self.assertNotIn("For Agents:", result.stdout)
            self.assertNotIn("Available workflows after account verification:", result.stdout)
            self.assertNotIn("account is connected", result.stdout)

    def test_invalid_download_ref_is_rejected_before_installing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            for ref in ("main", "../main", "v0.4.3-rc1", "a" * 39, "a?token=secret"):
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
        self.assertIn("VERSION=v0.4.3", installer)
        self.assertIn('$Version = "v0.4.3"', powershell_installer)

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
            allowed_program = {"holycrab_cli.py", "installation.json", "references/capabilities.json",
                               "vendor/segno-1.6.6-py3-none-any.whl", "vendor/LICENSE.segno"}
            library = Path(home) / ".local/lib/holycrab"
            installed_files = {p.relative_to(library).as_posix() for p in library.rglob("*")
                               if p.is_file() and "__pycache__" not in p.parts}
            self.assertEqual(installed_files, allowed_program)
            for agent_dir in (".agents", ".claude"):
                skill_dir = Path(home) / agent_dir / "skills/holycrab"
                self.assertEqual({p.relative_to(skill_dir).as_posix() for p in skill_dir.rglob("*") if p.is_file()},
                                 {"SKILL.md", "references/capabilities.json", "agents/openai.yaml"})

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
            self.assertIn("If holycrab is not found in this terminal, run:", result.stdout)
            manifest = json.loads(
                (Path(home) / ".local" / "lib" / "holycrab" / "installation.json").read_text(encoding="utf-8")
            )
            self.assertTrue(manifest["pathRegistration"]["addedByInstaller"])
            self.assertEqual(Path(manifest["pathRegistration"]["profile"]).resolve(), profile.resolve())
            self.assertFalse((Path(home) / ".bashrc").exists())
            self.assertFalse((Path(home) / ".bash_profile").exists())

    def test_installer_success_messages_follow_all_verification(self) -> None:
        for name, marker, success in (("install.sh", "INSTALL_COMPLETE=1", "printf '\\n%s\\n' \"$onboarding_summary\""),
                                      ("install.ps1", "$InstallComplete = $true", "Write-Host ($Summary -join [Environment]::NewLine)")):
            with self.subTest(installer=name):
                text = (REPO_ROOT / name).read_text(encoding="utf-8")
                self.assertLess(text.index(marker), text.index(success))
                self.assertIn("Verifying the installed CLI version and local health", text)
                self.assertIn("[4/5] Configuring the selected Agents", text)

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
            self.assertNotIn("HolyCrab 0.4.3 installed", interrupted.stdout)
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
            for previous_version in (None, "0.4.0", "0.4.1", "0.4.2"):
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
relative=${url#*/v0.4.3/}
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

    def test_windows_mcp_helper_accepts_utf8_bom_and_chinese_paths(self) -> None:
        installer = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")
        helper = installer.split("$Helper = @'\n", 1)[1].split("\n'@", 1)[0]
        server = ["C:/test/python.exe", "-X", "utf8", "C:/中文用户/holycrab_cli.py", "mcp", "serve"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper_path = root / "register-mcp.py"
            helper_path.write_text(helper, encoding="utf-8")
            module = root / "fake-cli.py"
            module.write_text("import json\ndef register_installer_mcp(agent, client, command, args, previous):\n    print(json.dumps([command, *args]))\n", encoding="utf-8")
            for marker in ("", "\ufeff"):
                result = subprocess.run([sys.executable, str(helper_path), str(module), "codex", "--discover", "old.json"],
                                        input=marker + json.dumps(server, ensure_ascii=False),
                                        text=True, encoding="utf-8", capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), server)

    def test_windows_summary_helper_accepts_bom_without_printing_internal_instructions(self) -> None:
        installer = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")
        helper = installer.split("$SummaryHelper = @'\n", 1)[1].split("\n'@", 1)[0]
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "summary.py"
            script.write_text(helper, encoding="utf-8")
            for bom in ("", "\ufeff"):
                for upgrading in ("0", "1"):
                    value = {"state": "VERIFY_ACCOUNT", "command": "holycrab auth status", "agentInstruction": "PRIVATE_INTERNAL_RULE"}
                    result = subprocess.run([sys.executable, str(script), str(REPO_ROOT / "holycrab/scripts/holycrab_cli.py"), upgrading, "onboarding"],
                                            input=bom + json.dumps(value), text=True, encoding="utf-8", capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("HolyCrab 0.4.3 installed", result.stdout)
                    self.assertNotIn("PRIVATE_INTERNAL_RULE", result.stdout)
                    self.assertNotIn("account connected", result.stdout)
                    self.assertEqual("Create product listing images" in result.stdout, upgrading == "0")
            report = {"ok": False, "checks": {"config": {"readable": False, "error": "Unreadable configuration"}}, "repairs": ["holycrab setup"]}
            result = subprocess.run([sys.executable, str(script), str(REPO_ROOT / "holycrab/scripts/holycrab_cli.py"), "0", "doctor"],
                                    input="\ufeff" + json.dumps(report), text=True, encoding="utf-8", capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Unreadable configuration", result.stdout)
            self.assertNotIn('"checks"', result.stdout)

    def test_windows_agent_fixture_emits_utf8_under_a_legacy_codepage(self) -> None:
        source = (REPO_ROOT / "tests/test_windows_installer.ps1").read_text(encoding="utf-8")
        program = source.split("$FakeCodexPython = @'\n", 1)[1].split("\n'@", 1)[0]
        value = {"transport": {"type": "stdio", "command": "C:/old/python.exe",
                               "args": ["-X", "utf8", "C:/中文用户/holycrab_cli.py", "mcp", "serve"]}}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script, state = root / "codex.py", root / "state.json"
            script.write_text(program, encoding="utf-8")
            state.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            env = {**os.environ, "PYTHONIOENCODING": "cp1252", "FAKE_CODEX_STATE": str(state),
                   "FAKE_CODEX_LOG": str(root / "actions.log")}
            result = subprocess.run([sys.executable, str(script), "mcp", "get", "holycrab", "--json"],
                                    env=env, text=True, encoding="utf-8", capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), value)

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
            "python3 -m unittest discover -s tests -v",
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
            'release_version="$RELEASE_VERSION"',
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
        notes = (REPO_ROOT / ".github" / "releases" / "v0.4.3.md").read_text(encoding="utf-8")
        self.assertTrue(notes.startswith("# HolyCrab Agent Tools v0.4.3\n"))
        self.assertIn("SHA-256", notes)
        self.assertIn("Windows", notes)
        self.assertIn("DPAPI", notes)

    def test_release_stays_draft_until_digest_and_native_install_checks_pass(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("--draft", workflow)
        self.assertLess(workflow.index("--draft"), workflow.index("Verify published bytes"))
        self.assertIn("verify-draft-install:", workflow)
        self.assertIn("needs: [release, verify-draft-install]", workflow)
        self.assertIn("verify_release_acceptance.py", workflow)
        self.assertIn('gh release edit "$RELEASE_VERSION" --draft=false --latest', workflow)

    def test_local_generate_exception_is_scoped_to_user_approved_v043(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("steps.contract-version.outputs.version == 'v0.4.3'", workflow)
        self.assertIn("steps.contract-version.outputs.version != 'v0.4.3'", workflow)
        self.assertIn("secrets.GENERATE_CONTRACT_SSH_KEY", workflow)
        self.assertIn("repository: AstroxNetwork/seedance-2.0", workflow)
        self.assertIn("verify_release_acceptance.py v0.4.3 --contract-only", workflow)


if __name__ == "__main__":
    unittest.main()
