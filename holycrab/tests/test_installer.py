from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).parents[2]


class InstallerTests(unittest.TestCase):
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
            self.assertTrue((Path(home) / ".claude" / "skills" / "holycrab" / "SKILL.md").is_file())
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


if __name__ == "__main__":
    unittest.main()
