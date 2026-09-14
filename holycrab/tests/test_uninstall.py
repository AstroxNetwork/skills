from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


REPO_ROOT = Path(__file__).parents[2]
SCRIPT = REPO_ROOT / "holycrab" / "scripts" / "holycrab_cli.py"
SPEC = importlib.util.spec_from_file_location("holycrab_uninstall", SCRIPT)
assert SPEC and SPEC.loader
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


class UninstallTests(unittest.TestCase):
    def install(self, home: str, *, agents: str = "none") -> tuple[dict[str, str], Path]:
        env = {
            **os.environ,
            "HOME": home,
            "SHELL": "/bin/zsh",
            "HOLYCRAB_INSTALL_SOURCE_DIR": str(REPO_ROOT),
            "HOLYCRAB_INSTALL_MCP": "0",
            "HOLYCRAB_INSTALL_AGENTS": agents,
            "HOLYCRAB_NO_UPDATE_CHECK": "1",
        }
        installed = subprocess.run(
            ["sh", str(REPO_ROOT / "install.sh")],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(installed.returncode, 0, installed.stderr)
        return env, Path(home) / ".local" / "bin" / "holycrab"

    def test_default_uninstall_requires_confirmation_and_preserves_local_state(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            env, command = self.install(home)
            config = Path(home) / ".config" / "holycrab"
            config.mkdir(parents=True, exist_ok=True)
            for name in ("config.json", "attempts.json", "update-state.json", "health-state.json"):
                path = config / name
                path.write_text("{}", encoding="utf-8")
                path.chmod(0o600)
            (config / "upload-plans").mkdir()
            saved_plan = config / "upload-plans" / "saved.json"
            saved_plan.write_text(
                json.dumps({"uploadPlanId": "saved", "expiresAtEpoch": time.time() + 3600}), encoding="utf-8"
            )
            (config / "real-human").mkdir()

            cancelled = subprocess.run(
                [str(command), "uninstall"], env=env, text=True, capture_output=True, check=False
            )
            self.assertEqual(cancelled.returncode, 2)
            self.assertTrue(command.exists())
            self.assertTrue(saved_plan.exists())

            removed = subprocess.run(
                [str(command), "uninstall", "--yes"], env=env, text=True, capture_output=True, check=False
            )
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertFalse(command.exists())
            self.assertFalse((Path(home) / ".local" / "lib" / "holycrab").exists())
            self.assertTrue((config / "config.json").exists())
            self.assertIn("preserved", removed.stdout.lower())
            self.assertNotIn("HolyCrab CLI", (Path(home) / ".zshrc").read_text(encoding="utf-8"))

            _, reinstalled_command = self.install(home)
            self.assertTrue(reinstalled_command.exists())
            self.assertTrue((config / "config.json").exists())
            self.assertTrue((config / "attempts.json").exists())
            self.assertTrue(saved_plan.exists())

    def test_purge_removes_known_state_but_keeps_unknown_files(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            env, command = self.install(home)
            config = Path(home) / ".config" / "holycrab"
            config.mkdir(parents=True, exist_ok=True)
            for name in ("config.json", "attempts.json", "attempts.lock", "update-state.json", "health-state.json"):
                path = config / name
                path.write_text("{}", encoding="utf-8")
                path.chmod(0o600)
            (config / "upload-plans").mkdir()
            (config / "upload-plans" / "plan.json").write_text(
                '{"uploadPlanId":"plan"}', encoding="utf-8"
            )
            nested_unknown = config / "upload-plans" / "notes.txt"
            nested_unknown.write_text("keep", encoding="utf-8")
            (config / "real-human").mkdir()
            authorization = config / "real-human" / "auth1"
            authorization.mkdir()
            (authorization / "qr.png").write_bytes(b"png")
            (authorization / "metadata.json").write_text("{}", encoding="utf-8")
            authorization_unknown = authorization / "notes.txt"
            authorization_unknown.write_text("keep", encoding="utf-8")
            unknown = config / "keep-me.txt"
            unknown.write_text("user data", encoding="utf-8")

            removed = subprocess.run(
                [str(command), "uninstall", "--purge", "--yes"],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertTrue(unknown.exists())
            self.assertTrue(nested_unknown.exists())
            self.assertTrue(authorization_unknown.exists())
            self.assertFalse((config / "config.json").exists())
            self.assertFalse((config / "upload-plans" / "plan.json").exists())
            self.assertFalse((authorization / "qr.png").exists())
            self.assertFalse((authorization / "metadata.json").exists())
            self.assertIn("revoke", removed.stdout.lower())

    def test_purge_refuses_a_symlinked_config_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside" / "holycrab"
            outside.mkdir(parents=True)
            credential = outside / "config.json"
            credential.write_text("{}", encoding="utf-8")
            linked = root / "holycrab"
            linked.symlink_to(outside, target_is_directory=True)
            with patch.object(cli, "config_dir", return_value=linked):
                warnings = cli.purge_known_local_state()
            self.assertTrue(credential.exists())
            self.assertIn("Refused to purge", warnings[0])

    def test_modified_skill_and_extra_files_are_retained(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            env, command = self.install(home, agents="codex")
            skill = Path(home) / ".agents" / "skills" / "holycrab"
            (skill / "SKILL.md").write_text("user customization", encoding="utf-8")
            extra = skill / "notes.txt"
            extra.write_text("keep", encoding="utf-8")

            removed = subprocess.run(
                [str(command), "uninstall", "--yes"], env=env, text=True, capture_output=True, check=False
            )
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertEqual((skill / "SKILL.md").read_text(encoding="utf-8"), "user customization")
            self.assertTrue(extra.exists())
            self.assertFalse((skill / "references" / "capabilities.json").exists())
            self.assertIn("modified", removed.stdout.lower())

    def test_legacy_manifest_does_not_remove_profile_path(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            env, command = self.install(home)
            manifest_path = Path(home) / ".local" / "lib" / "holycrab" / "installation.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.pop("pathRegistration", None)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            manifest_path.chmod(0o600)

            removed = subprocess.run(
                [str(command), "uninstall", "--yes"], env=env, text=True, capture_output=True, check=False
            )
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertIn("HolyCrab CLI", (Path(home) / ".zshrc").read_text(encoding="utf-8"))
            self.assertIn("PATH", removed.stdout)

    def test_shared_bin_keeps_profile_path_and_other_program(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            env, command = self.install(home)
            other = Path(home) / ".local" / "bin" / "other-tool"
            other.write_text("keep", encoding="utf-8")
            removed = subprocess.run(
                [str(command), "uninstall", "--yes"], env=env, text=True, capture_output=True, check=False
            )
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertTrue(other.exists())
            self.assertIn("HolyCrab CLI", (Path(home) / ".zshrc").read_text(encoding="utf-8"))
            self.assertIn("contains other programs", removed.stdout)

    def test_uninstall_skips_startup_checks_and_only_removes_owned_mcp(self) -> None:
        with patch.object(cli, "local_health_report") as health, patch.object(cli, "check_for_update") as update:
            cli.startup_maintenance(["uninstall", "--yes"])
        health.assert_not_called()
        update.assert_not_called()

        manifest = {"agents": ["codex"], "mcp": True}
        owned = MagicMock(returncode=0, stdout="command: /chosen/bin/holycrab mcp serve", stderr="")
        removed = MagicMock(returncode=0, stdout="", stderr="")
        unmanaged = MagicMock(returncode=0, stdout="command: custom-server", stderr="")
        with patch.object(cli.shutil, "which", return_value="/usr/bin/codex"), \
                patch.object(cli.subprocess, "run", side_effect=[owned, removed]) as run:
            warnings = cli.remove_managed_mcp_registrations(manifest, "/chosen/bin/holycrab")
        self.assertEqual(warnings, [])
        self.assertEqual(run.call_args_list[1].args[0], ["/usr/bin/codex", "mcp", "remove", "holycrab"])

        with patch.object(cli.shutil, "which", return_value="/usr/bin/codex"), \
                patch.object(cli.subprocess, "run", return_value=unmanaged) as run:
            warnings = cli.remove_managed_mcp_registrations(manifest, "/chosen/bin/holycrab")
        self.assertEqual(run.call_count, 1)
        self.assertIn("unmanaged", warnings[0].lower())

        failed = MagicMock(returncode=1, stdout="", stderr="denied")
        with patch.object(cli.shutil, "which", return_value="/usr/bin/codex"), \
                patch.object(cli.subprocess, "run", side_effect=[owned, failed]), \
                self.assertRaisesRegex(RuntimeError, "no program files were removed"):
            cli.remove_managed_mcp_registrations(manifest, "/chosen/bin/holycrab")

    def test_interactive_cancel_makes_no_changes(self) -> None:
        args = cli.build_parser().parse_args(["uninstall"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "lib" / "holycrab"
            launcher = root / "bin" / "holycrab"
            manifest = {"agents": [], "mcp": False, "pathRegistration": {"kind": "none", "addedByInstaller": False}}
            with patch.object(cli, "validated_uninstall_manifest", return_value=(manifest, root, library, launcher)), \
                    patch.object(cli.sys.stdin, "isatty", return_value=True), \
                    patch("builtins.input", return_value="n"), \
                    patch.object(cli, "remove_managed_mcp_registrations") as remove_mcp, \
                    patch.object(cli, "remove_managed_skill_files") as remove_skill:
                result = cli.command_uninstall(args)
        self.assertEqual(result, 2)
        remove_mcp.assert_not_called()
        remove_skill.assert_not_called()

    def test_uninstall_main_makes_no_network_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "lib" / "holycrab"
            launcher = root / "bin" / "holycrab"
            manifest = {"agents": [], "mcp": False, "pathRegistration": {"kind": "none", "addedByInstaller": False}}
            with patch.object(cli, "validated_uninstall_manifest", return_value=(manifest, root, library, launcher)), \
                    patch.object(cli, "remove_managed_mcp_registrations", return_value=[]), \
                    patch.object(cli, "remove_managed_skill_files", return_value=[]), \
                    patch.object(cli, "send") as send, \
                    patch.object(cli.urllib.request, "urlopen") as urlopen, \
                    patch.object(sys, "argv", ["holycrab", "uninstall", "--yes"]):
                result = cli.main()
        self.assertEqual(result, 0)
        send.assert_not_called()
        urlopen.assert_not_called()

    def test_manifest_must_match_the_running_installed_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "lib" / "holycrab" / "installation.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(json.dumps({
                "prefix": str(root / "different"), "agents": [], "mcp": False,
            }), encoding="utf-8")
            manifest_path.chmod(0o600)
            with patch.object(cli, "installation_path", return_value=manifest_path), \
                    self.assertRaisesRegex(SystemExit, "does not match"):
                cli.validated_uninstall_manifest()


if __name__ == "__main__":
    unittest.main()
