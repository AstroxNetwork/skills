"""Installation choices, diagnostics and recovery boundaries for v0.4.4."""
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("cli_update_v044", ROOT / "holycrab/scripts/holycrab_cli.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class UpdateRecoveryTests(unittest.TestCase):
    def record(self, agents=None):
        return {"managedBy": cli.INSTALLATION_MANAGER, "prefix": "/chosen", "agents": agents or [], "mcp": False}

    def test_fresh_defaults_and_repeat_choices(self):
        self.assertEqual(cli.installer_settings({}, "/chosen"), {"agents": "codex,claude", "mcp": "1"})
        for agents in ([], ["codex"], ["claude"], ["codex", "claude"]):
            result = cli.installer_settings(self.record(agents), "/chosen")
            self.assertEqual(result, {"agents": ",".join(agents) or "none", "mcp": "0"})
        self.assertEqual(cli.installer_settings(self.record(), "/chosen", "claude", "1"),
                         {"agents": "claude", "mcp": "1"})

    def test_corrupt_record_and_invalid_choices_fail_closed(self):
        for bad in ([], {"prefix": "/other"}, {**self.record(), "agents": ["other"]},
                    {**self.record(), "agents": [{}]},
                    {**self.record(), "mcp": "0"}, {**self.record(), "managedBy": "other"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                cli.installer_settings(bad, "/chosen")
        for agents in ("none,codex", "codex,codex", "other"):
            with self.assertRaises(ValueError):
                cli.installer_settings({}, "/chosen", agents)

    def test_missing_install_record_never_downloads_or_executes(self):
        with patch.object(cli, "release_installer", return_value=("install.sh", "url", "digest")), \
                patch.object(cli, "load_installation", return_value=None), \
                patch.object(cli, "open_download") as download, patch.object(cli.subprocess, "run") as execute:
            with self.assertRaisesRegex(SystemExit, "record is missing"):
                cli.run_update({})
        download.assert_not_called()
        execute.assert_not_called()

    def test_noninteractive_failure_has_bounded_sanitized_diagnostic(self):
        name = "install.ps1" if os.name == "nt" else "install.sh"
        body = (('$Version = "v0.4.5"' if os.name == "nt" else 'VERSION=v0.4.5') + '\n').encode()
        release = {"tag_name": "v0.4.5", "assets": [{"name": name,
            "browser_download_url": f"https://github.com/AstroxNetwork/skills/releases/download/v0.4.5/{name}",
            "digest": "sha256:" + cli.hashlib.sha256(body).hexdigest()}]}
        remote = MagicMock()
        remote.__enter__.return_value.read.side_effect = [body, b""]
        errors = io.StringIO()
        with patch.object(cli, "load_installation", return_value=self.record()), \
                patch.object(cli, "installation_path", return_value=Path('/chosen/lib/holycrab/installation.json')), \
                patch.object(cli, "open_download", return_value=remote), \
                patch.object(cli, "SENSITIVE_OUTPUT_VALUES", {"private-test-key"}), \
                patch.object(cli.subprocess, "run", return_value=MagicMock(returncode=1,
                    stderr=b"x" * 10000 + b"Permission denied private-test-key", stdout=b"")), redirect_stderr(errors):
            with self.assertRaisesRegex(SystemExit, "installer failed"):
                cli.run_update(release)
        self.assertIn("Permission denied", errors.getvalue())
        self.assertNotIn("private-test-key", errors.getvalue())
        self.assertLess(len(errors.getvalue()), 4100)

    def test_file_restore_preserves_unselected_and_extra_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup, prefix, home = root / 'backup', root / 'prefix', root / 'home'
            source = backup / 'lib/holycrab_cli.py'
            source.parent.mkdir(parents=True)
            source.write_text('old program')
            current = prefix / 'lib/holycrab/holycrab_cli.py'
            current.parent.mkdir(parents=True)
            current.write_text('new program')
            extra = current.parent / 'user-note.txt'
            extra.write_text('keep')
            unselected = home / '.claude/skills/holycrab/SKILL.md'
            unselected.parent.mkdir(parents=True)
            unselected.write_text('unchanged')
            with patch.object(cli.Path, 'home', return_value=home):
                cli.restore_installer_files(backup, prefix, ['codex'])
            self.assertEqual(current.read_text(), 'old program')
            self.assertEqual(extra.read_text(), 'keep')
            self.assertEqual(unselected.read_text(), 'unchanged')

    def test_mcp_restore_reinstates_old_command_and_refuses_external_changes(self):
        before = {'command': '/old/holycrab', 'args': ['mcp', 'serve'], 'scope': 'user'}
        after = {**before, 'command': '/new/holycrab'}
        journal = {'codex': {'executable': '/clients/codex', 'before': before, 'after': after}}
        with patch.object(cli, 'read_json_file', return_value=journal), \
                patch.object(cli, 'inspect_agent_registration', side_effect=[(after, False, 0), (before, False, 0)]), \
                patch.object(cli, 'run_agent_client', return_value=MagicMock(returncode=0)) as run:
            cli.restore_installer_mcp(Path('fixture.json'))
        self.assertEqual(run.call_count, 2)
        self.assertIn('/old/holycrab', run.call_args.args[1])
        with patch.object(cli, 'read_json_file', return_value=journal), \
                patch.object(cli, 'inspect_agent_registration', return_value=({**after, 'command': '/user/choice'}, False, 0)), \
                patch.object(cli, 'run_agent_client') as run:
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                cli.restore_installer_mcp(Path('fixture.json'))
        run.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'Native PowerShell integration covers Windows')
    def test_real_reinstall_with_blank_old_updater_settings_keeps_no_agents(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {**os.environ, 'HOME': str(root / 'home'), 'HOLYCRAB_INSTALL_SOURCE_DIR': str(ROOT),
                   'HOLYCRAB_INSTALL_PREFIX': str(root / 'prefix'), 'HOLYCRAB_CONFIG_DIR': str(root / 'config'),
                   'HOLYCRAB_INSTALL_AGENTS': 'none', 'HOLYCRAB_INSTALL_MCP': '0', 'HOLYCRAB_NO_UPDATE_CHECK': '1'}
            for value in ('none', ''):
                env['HOLYCRAB_INSTALL_AGENTS'] = value
                result = subprocess.run(['sh', str(ROOT / 'install.sh')], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            record = json.loads((root / 'prefix/lib/holycrab/installation.json').read_text())
            self.assertEqual(record['agents'], [])
            self.assertFalse((root / 'home/.agents/skills/holycrab').exists())
            self.assertFalse((root / 'home/.claude/skills/holycrab').exists())

    def test_unavailable_second_agent_does_not_rollback_first_agent(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = Path(temporary) / 'mcp-transaction.json'
            journal.write_text('{"codex": {"before": null}}')
            with patch.object(cli, 'load_installation', return_value={'prefix': '/chosen'}), \
                    patch.object(cli, 'find_agent_client', return_value=None), \
                    patch.object(cli, 'mcp_points_to_cli', return_value=True), \
                    patch.object(cli, 'write_private_json'), \
                    patch.object(cli, 'restore_installer_mcp') as restore:
                cli.register_installer_mcp('claude', None, '/chosen/bin/holycrab', ['mcp', 'serve'], '', str(journal))
            restore.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'Unix process fault injection')
    def test_failed_health_check_restores_previous_bytes_and_keeps_user_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {**os.environ, 'HOME': str(root / 'home'), 'HOLYCRAB_INSTALL_SOURCE_DIR': str(ROOT),
                   'HOLYCRAB_INSTALL_PREFIX': str(root / 'prefix'), 'HOLYCRAB_CONFIG_DIR': str(root / 'config'),
                   'HOLYCRAB_INSTALL_AGENTS': 'none', 'HOLYCRAB_INSTALL_MCP': '0', 'HOLYCRAB_NO_UPDATE_CHECK': '1'}
            first = subprocess.run(['sh', str(ROOT / 'install.sh')], env=env, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            program = root / 'prefix/lib/holycrab/holycrab_cli.py'
            program.write_bytes(program.read_bytes().replace(b'VERSION = "0.4.4"', b'VERSION = "0.4.3"'))
            old = program.read_bytes()
            note = program.parent / 'user-note.txt'
            note.write_text('preserve')
            fake = root / 'fake-bin'
            fake.mkdir()
            wrapper = fake / 'python3'
            wrapper.write_text('#!/bin/sh\ncase "$*" in *doctor*) exit 7;; esac\nexec "' + shutil.which('python3') + '" "$@"\n')
            wrapper.chmod(0o700)
            env['PATH'] = str(fake) + os.pathsep + env['PATH']
            failed = subprocess.run(['sh', str(ROOT / 'install.sh')], env=env, capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertNotIn('HolyCrab 0.4.4 installed', failed.stdout)
            self.assertIn('restored', failed.stderr)
            self.assertEqual(program.read_bytes(), old)
            self.assertEqual(note.read_text(), 'preserve')
