from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
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

    def test_public_install_uses_the_branded_stable_entrypoint(self) -> None:
        installer = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("VERSION=v0.3.0", installer)

        stable_url = "https://holycrab.ai/cli/install.sh"
        versioned_raw_url = "https://raw.githubusercontent.com/AstroxNetwork/skills/v0.3.0/install.sh"
        for document in (REPO_ROOT / "README.md", REPO_ROOT / "HolyCrab CLI 使用指南.md"):
            content = document.read_text(encoding="utf-8")
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
            command = Path(home) / ".local" / "bin" / "holycrab"
            self.assertTrue(command.is_file())
            self.assertTrue((Path(home) / ".agents" / "skills" / "holycrab" / "SKILL.md").is_file())
            self.assertTrue((Path(home) / ".agents" / "skills" / "holycrab" / "agents" / "openai.yaml").is_file())
            self.assertTrue((Path(home) / ".claude" / "skills" / "holycrab" / "SKILL.md").is_file())
            self.assertTrue((Path(home) / ".claude" / "skills" / "holycrab" / "agents" / "openai.yaml").is_file())
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

    def test_fresh_install_and_upgrade_can_generate_qr_without_pip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prefix = root / "prefix"
            config = root / "config"
            config.mkdir()
            config_file = config / "config.json"
            config_file.write_text('{"apiKey":"unchanged-local-key"}')
            env = {**os.environ, "HOLYCRAB_INSTALL_SOURCE_DIR": str(REPO_ROOT),
                   "HOLYCRAB_INSTALL_PREFIX": str(prefix), "HOLYCRAB_CONFIG_DIR": str(config),
                   "HOLYCRAB_INSTALL_AGENTS": "none", "HOLYCRAB_INSTALL_MCP": "0"}
            for _ in range(2):
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
"""
                result = subprocess.run(["python3", "-S", "-c", program,
                                         str(prefix / "lib" / "holycrab" / "holycrab_cli.py")],
                                        env=env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(config_file.read_text(), '{"apiKey":"unchanged-local-key"}')
                self.assertTrue((prefix / "lib" / "holycrab" / "vendor" / "LICENSE.segno").is_file())

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
relative=${url#*/v0.3.0/}
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

    def test_docs_explain_supported_systems_update_and_uninstall(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Python 3.10+", readme)
        self.assertIn("macOS", readme)
        self.assertIn("Linux", readme)
        self.assertIn("覆盖安装", readme)
        self.assertIn("卸载", readme)

    def test_ci_runs_release_gates_on_linux_and_macos(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        for token in (
            "ubuntu-latest",
            "macos-latest",
            "python3 -m unittest discover -s holycrab/tests -v",
            "python3 -m py_compile",
            "validate_capabilities.py",
            "quick_validate.py",
            "49f948faa9258a0c61caceaf225e179651397431",
            "HOLYCRAB_INSTALL_SOURCE_DIR",
            "mcp serve",
        ):
            self.assertIn(token, workflow)

    def test_tagged_release_uploads_the_installer_after_all_release_gates(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        for token in (
            "needs: [quality, official-skill-validation, install-and-mcp]",
            "if: startsWith(github.ref, 'refs/tags/v')",
            "contents: write",
            'release_version="$GITHUB_REF_NAME"',
            'grep -Fx "VERSION=$release_version" install.sh',
            'test -f ".github/releases/$release_version.md"',
            "sha256sum install.sh > SHA256SUMS",
            'gh release create "$release_version"',
            "install.sh SHA256SUMS",
            "--verify-tag",
            "--latest",
            '--notes-file ".github/releases/$release_version.md"',
        ):
            self.assertIn(token, workflow)
        self.assertNotIn("--clobber", workflow)
        self.assertNotIn('gh release upload "$release_version"', workflow)

    def test_release_notes_have_the_approved_english_title(self) -> None:
        notes = (REPO_ROOT / ".github" / "releases" / "v0.3.0.md").read_text(encoding="utf-8")
        self.assertTrue(notes.startswith("# HolyCrab Agent Tools v0.3.0\n"))
        self.assertIn("SHA-256", notes)
        self.assertIn("Seedance 2.5", notes)
        self.assertIn("Seed Audio", notes)


if __name__ == "__main__":
    unittest.main()
