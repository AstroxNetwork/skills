from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from contextlib import redirect_stdout

SCRIPT = Path(__file__).parents[1] / "scripts" / "holycrab_cli.py"
SPEC = importlib.util.spec_from_file_location("holycrab_agent_cleanup", SCRIPT)
assert SPEC and SPEC.loader
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


class AgentCleanupTests(unittest.TestCase):
    def manifest(self, agent="codex", executable=None):
        return {"agents": [agent], "mcp": True, "agentRegistrations": {agent: {
            "executable": executable or f"/clients/{agent}", "managed": True,
            "scope": "user", "command": "/chosen/bin/holycrab", "args": ["mcp", "serve"],
        }}}

    def response(self, command="/chosen/bin/holycrab", args=None):
        return MagicMock(returncode=0, stdout=json.dumps({"transport": {
            "type": "stdio", "command": command, "args": args or ["mcp", "serve"],
        }}), stderr="")

    def test_recorded_client_is_used_when_missing_from_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / ("codex.exe" if os.name == "nt" else "codex")
            executable.touch()
            executable.chmod(0o700)
            with patch.object(cli.shutil, "which", return_value=None), \
                    patch.object(cli.subprocess, "run", side_effect=[self.response(), MagicMock(returncode=0)]) as run:
                warnings = cli.remove_managed_mcp_registrations(
                    self.manifest(executable=str(executable)), "/chosen/bin/holycrab")
            self.assertEqual(warnings, [])
            self.assertEqual(run.call_args_list[0].args[0][0], str(executable))
            self.assertIn("--json", run.call_args_list[0].args[0])

    def test_command_prefix_or_extra_arguments_do_not_prove_ownership(self):
        for response in (self.response(command="/chosen/bin/holycrab-other"),
                         self.response(args=["mcp", "serve", "--other"]),
                         MagicMock(returncode=0, stdout="command: unrelated", stderr="/chosen/bin/holycrab mcp serve")):
            with self.subTest(response=response), patch.object(cli.shutil, "which", return_value="/clients/codex"), \
                    patch.object(cli.subprocess, "run", return_value=response) as run:
                warnings = cli.remove_managed_mcp_registrations(self.manifest(), "/chosen/bin/holycrab")
            self.assertEqual(run.call_count, 1)
            self.assertTrue(warnings)

    def test_explicitly_unmanaged_registration_is_never_inspected_or_removed(self):
        manifest = self.manifest()
        manifest["agentRegistrations"]["codex"]["managed"] = False
        with patch.object(cli.subprocess, "run") as run:
            cli.remove_managed_mcp_registrations(manifest, "/chosen/bin/holycrab")
        run.assert_not_called()

    def test_claude_user_scope_is_required(self):
        response = MagicMock(returncode=0, stdout="holycrab:\n  Scope: User config (available in all projects)\n  Type: stdio\n  Command: /chosen/bin/holycrab\n  Args: mcp serve\n", stderr="")
        with patch.object(cli.shutil, "which", return_value="/clients/claude"), \
                patch.object(cli.subprocess, "run", side_effect=[response, MagicMock(returncode=0)]) as run:
            warnings = cli.remove_managed_mcp_registrations(self.manifest("claude"), "/chosen/bin/holycrab")
        self.assertEqual(warnings, [])
        self.assertEqual(run.call_args_list[1].args[0], ["/clients/claude", "mcp", "remove", "--scope", "user", "holycrab"])
        response.stdout = response.stdout.replace("User config", "Project config")
        with patch.object(cli.shutil, "which", return_value="/clients/claude"), \
                patch.object(cli.subprocess, "run", return_value=response) as run:
            cli.remove_managed_mcp_registrations(self.manifest("claude"), "/chosen/bin/holycrab")
        self.assertEqual(run.call_count, 1)

    def test_failed_inspection_is_pending_but_missing_entry_is_not(self):
        for message, pending in (("Permission denied", True), ("No MCP server named 'holycrab' found.", False)):
            with patch.object(cli.shutil, "which", return_value="/clients/codex"), \
                    patch.object(cli.subprocess, "run", return_value=MagicMock(returncode=1, stdout="", stderr=message)):
                warnings = cli.remove_managed_mcp_registrations(self.manifest(), "/chosen/bin/holycrab")
            self.assertEqual(any(item.startswith("MCP cleanup pending") for item in warnings), pending)

    def test_unavailable_client_has_manual_recovery_without_deletion(self):
        with patch.object(cli, "find_agent_client", return_value=None), patch.object(cli.subprocess, "run") as run:
            warnings = cli.remove_managed_mcp_registrations(self.manifest(), "/chosen/bin/holycrab")
        run.assert_not_called()
        self.assertTrue(warnings[0].startswith("MCP cleanup pending for codex"))
        self.assertIn("remove only", warnings[0])

    def test_manifest_cannot_redirect_cleanup_to_another_program(self):
        manifest = self.manifest(executable="/bin/sh")
        with patch.object(cli.shutil, "which", return_value="/clients/codex"), \
                patch.object(cli.subprocess, "run", side_effect=[self.response(), MagicMock(returncode=0)]) as run:
            cli.remove_managed_mcp_registrations(manifest, "/chosen/bin/holycrab")
        self.assertEqual(run.call_args_list[0].args[0][0], "/clients/codex")

    def test_manifest_cannot_claim_an_unrelated_server_as_managed(self):
        manifest = self.manifest()
        manifest["agentRegistrations"]["codex"]["command"] = "/other/server"
        with patch.object(cli.shutil, "which", return_value="/clients/codex"), \
                patch.object(cli.subprocess, "run", return_value=self.response(command="/other/server")) as run:
            cli.remove_managed_mcp_registrations(manifest, "/chosen/bin/holycrab")
        self.assertLessEqual(run.call_count, 1)

    def test_powershell_client_is_invoked_through_powershell(self):
        with patch.object(cli.shutil, "which", return_value="powershell.exe"):
            command = cli.agent_client_command("C:/clients/codex.ps1", ["mcp", "get", "holycrab"])
        self.assertEqual(command[:2], ["powershell.exe", "-NoProfile"])
        self.assertIn("-File", command)
        self.assertEqual(command[-3:], ["mcp", "get", "holycrab"])

    def test_online_doctor_uses_the_same_exact_match(self):
        with patch.object(cli, "load_installation", return_value=self.manifest()), \
                patch.object(cli.shutil, "which", return_value="/clients/codex"), \
                patch.object(cli.subprocess, "run", return_value=self.response(command="/chosen/bin/holycrab-other")):
            result = cli.mcp_registration_check("codex", "/chosen/bin/holycrab")
        self.assertFalse(result["ok"])

    @unittest.skipIf(os.name == "nt", "Unix registration fixture; native PowerShell test covers Windows registration")
    def test_installer_records_client_and_preserves_ownership_on_repeat_install(self):
        for previous, owned in (({}, False), (self.manifest(), True)):
            previous.update({"prefix": "/chosen", "managedBy": cli.INSTALLATION_MANAGER})
            current = {"prefix": "/chosen", "agents": ["codex"], "mcp": True}
            with patch.object(cli, "load_installation", return_value=current), \
                    patch.object(cli, "read_json_file", return_value=previous), \
                    patch.object(cli.shutil, "which", return_value="/clients/codex"), \
                    patch.object(cli.subprocess, "run", return_value=self.response()) as run, \
                    patch.object(cli, "write_private_json") as write:
                cli.register_installer_mcp("codex", None, "/chosen/bin/holycrab", ["mcp", "serve"], "old.json")
            self.assertEqual(run.call_count, 1)
            record = write.call_args.args[1]["agentRegistrations"]["codex"]
            self.assertEqual(record["executable"], "/clients/codex")
            self.assertEqual(record["managed"], owned)

    @unittest.skipIf(os.name == "nt", "Unix registration fixture; native PowerShell test covers Windows registration")
    def test_new_registration_is_checked_and_recorded_without_secrets(self):
        missing = MagicMock(returncode=1, stdout="", stderr="No MCP server named 'holycrab' found.")
        with patch.object(cli, "load_installation", return_value={"prefix": "/chosen"}), \
                patch.object(cli, "read_json_file", return_value={}), \
                patch.object(cli.shutil, "which", return_value="/clients/codex"), \
                patch.object(cli.subprocess, "run", side_effect=[missing, MagicMock(returncode=0), self.response()]) as run, \
                patch.object(cli, "write_private_json") as write:
            cli.register_installer_mcp("codex", None, "/chosen/bin/holycrab", ["mcp", "serve"], "old.json")
        self.assertEqual(run.call_count, 3)
        record = write.call_args.args[1]["agentRegistrations"]["codex"]
        self.assertTrue(record["managed"])
        self.assertEqual(set(record), {"command", "args", "scope", "executable", "managed"})

    @unittest.skipIf(os.name == "nt", "Unix registration fixture; native PowerShell test covers Windows registration")
    def test_unknown_inspection_never_overwrites_registration(self):
        with patch.object(cli, "load_installation", return_value={"prefix": "/chosen"}), \
                patch.object(cli, "read_json_file", return_value={}), \
                patch.object(cli.shutil, "which", return_value="/clients/codex"), \
                patch.object(cli.subprocess, "run", return_value=MagicMock(returncode=1, stdout="", stderr="denied")) as run, \
                patch.object(cli, "write_private_json") as write:
            cli.register_installer_mcp("codex", None, "/chosen/bin/holycrab", ["mcp", "serve"], "old.json")
        self.assertEqual(run.call_count, 1)
        self.assertFalse(write.call_args.args[1]["agentRegistrations"]["codex"]["managed"])

    @unittest.skipIf(os.name == "nt", "Unix registration fixture; native PowerShell test covers Windows registration")
    def test_legacy_installation_gains_ownership_record(self):
        previous = {"prefix": "/chosen", "managedBy": cli.INSTALLATION_MANAGER, "mcp": True, "agents": ["codex"]}
        with patch.object(cli, "load_installation", return_value={"prefix": "/chosen"}), \
                patch.object(cli, "read_json_file", return_value=previous), \
                patch.object(cli.shutil, "which", return_value="/clients/codex"), \
                patch.object(cli.subprocess, "run", return_value=self.response()), \
                patch.object(cli, "write_private_json") as write:
            cli.register_installer_mcp("codex", None, "/chosen/bin/holycrab", ["mcp", "serve"], "old.json")
        self.assertTrue(write.call_args.args[1]["agentRegistrations"]["codex"]["managed"])

    def test_pending_cleanup_does_not_claim_complete_uninstall(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "lib" / "holycrab"
            launcher = root / "bin" / "holycrab"
            args = cli.build_parser().parse_args(["uninstall", "--yes"])
            output = io.StringIO()
            with patch.object(cli, "validated_uninstall_manifest", return_value=({}, root, library, launcher)), \
                    patch.object(cli, "remove_managed_mcp_registrations", return_value=["MCP cleanup pending for codex: client unavailable"]), \
                    patch.object(cli, "remove_managed_skill_files", return_value=[]), \
                    patch.object(cli, "remove_managed_path_registration", return_value=["PATH registration was kept because the installer did not add it"]), \
                    patch.object(cli, "schedule_windows_program_cleanup"), \
                    patch.object(cli, "config_dir", return_value=root / "config"), redirect_stdout(output):
                result = cli.command_uninstall(args)
        self.assertEqual(result, 0)
        self.assertIn("Agent registration cleanup is incomplete", output.getvalue())
        self.assertNotIn("HolyCrab uninstall completed", output.getvalue())
        self.assertIn("Info: PATH", output.getvalue())
        self.assertNotIn("Warning: PATH", output.getvalue())


if __name__ == "__main__":
    unittest.main()
