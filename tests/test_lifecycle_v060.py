"""Installation ownership and recovery checks in disposable homes only."""
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
try:
    from .test_reliability_v060 import cli, ROOT
except ImportError:
    from test_reliability_v060 import cli, ROOT


class OwnershipTests(unittest.TestCase):
    def test_disabling_agents_retains_modified_skill_and_unavailable_mcp_ownership(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, prefix = root / 'home', root / 'prefix'
            skill = home / '.agents/skills/holycrab/SKILL.md'
            skill.parent.mkdir(parents=True)
            skill.write_text('user customization')
            previous = {'schemaVersion': 2, 'managedBy': cli.INSTALLATION_MANAGER, 'prefix': str(prefix),
                        'agents': ['codex'], 'mcp': True, 'coreFiles': [
                            {'path': str(skill), 'sha256': hashlib.sha256(b'original skill').hexdigest()}],
                        'agentRegistrations': {'codex': {'managed': True, 'command': str(prefix / 'bin/holycrab'), 'args': ['mcp', 'serve']}}}
            old_path, new_path = root / 'previous.json', root / 'installation.json'
            for iteration in range(2):
                cli.write_private_json(old_path, previous)
                cli.write_private_json(new_path, {'managedBy': cli.INSTALLATION_MANAGER, 'prefix': str(prefix), 'agents': [], 'mcp': False, 'coreFiles': []})
                with patch.object(cli.Path, 'home', return_value=home), \
                        patch.object(cli._owner('installation_path'), 'installation_path', return_value=new_path), \
                        patch.object(cli._owner('find_agent_client'), 'find_agent_client', return_value=None):
                    cli.finalize_installation(str(old_path), str(root / 'journal.json'))
                previous = json.loads(new_path.read_text())
                self.assertEqual(previous['enabledAgents'], [])
                self.assertFalse(previous['enabledMcp'])
                self.assertEqual(previous['agents'], ['codex'])
                self.assertTrue(previous['agentRegistrations']['codex']['managed'])
                self.assertEqual(previous['retainedFiles'][0]['path'], str(skill))
                self.assertEqual(skill.read_text(), 'user customization')
                self.assertEqual(cli.installer_settings(previous, str(prefix)), {'agents': 'none', 'mcp': '0'})

    def test_uninstall_only_removes_unchanged_owned_files(self):
        with tempfile.TemporaryDirectory() as temp:
            library = Path(temp) / 'lib'
            library.mkdir()
            rows = []
            for name in ('owned.py', 'modified.py'):
                path = library / name
                path.write_bytes(b'original')
                rows.append({'path': name, 'sha256': hashlib.sha256(b'original').hexdigest()})
            (library / 'modified.py').write_bytes(b'user modified')
            (library / 'notes.txt').write_text('keep')
            (library / 'installation.json').write_text('keep ownership')
            warnings = cli.remove_managed_program_files({'coreFiles': rows}, library)
            self.assertFalse((library / 'owned.py').exists())
            self.assertEqual((library / 'modified.py').read_bytes(), b'user modified')
            self.assertTrue((library / 'notes.txt').exists())
            self.assertTrue((library / 'installation.json').exists())
            self.assertEqual(len(warnings), 1)

    def test_installation_lock_excludes_competing_lifecycle_operations(self):
        with tempfile.TemporaryDirectory() as temp:
            prefix = Path(temp)
            with cli.installation_lock(prefix):
                with self.assertRaises((OSError, SystemExit)):
                    with cli.installation_lock(prefix): pass
            self.assertFalse((prefix / '.holycrab-install.lock').exists())


@unittest.skipIf(os.name == 'nt', 'Native PowerShell installation is exercised by Windows CI')
class InstalledRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.prefix = self.root / 'prefix'
        self.env = {**os.environ, 'HOME': str(self.root / 'home'),
                    'HOLYCRAB_INSTALL_SOURCE_DIR': str(ROOT), 'HOLYCRAB_INSTALL_PREFIX': str(self.prefix),
                    'HOLYCRAB_INSTALL_AGENTS': 'none', 'HOLYCRAB_INSTALL_MCP': '0',
                    'HOLYCRAB_CONFIG_DIR': str(self.root / 'config'), 'HOLYCRAB_NO_UPDATE_CHECK': '1'}
        self.env.pop('HOLYCRAB_API_KEY', None)

    def install(self):
        result = subprocess.run(['sh', str(ROOT / 'install.sh')], env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def command(self, *args):
        return subprocess.run([str(self.prefix / 'bin/holycrab'), *args], env=self.env, capture_output=True, text=True, timeout=10)

    def test_install_repairs_runtime_without_discarding_corrupt_attempts(self):
        config = self.root / 'config'
        config.mkdir()
        ledger = config / 'attempts.json'
        ledger.write_text('{bad ledger')
        ledger.chmod(0o600)
        self.install()
        result = self.command('doctor', '--json')
        report = json.loads(result.stdout)
        self.assertFalse(report['ok'])
        self.assertTrue(report['runtimeOk'])
        self.assertEqual(ledger.read_text(), '{bad ledger')

    def test_schema3_doctor_rejects_missing_module_inventory_entry(self):
        self.install()
        path = self.prefix / 'lib/holycrab/installation.json'
        record = json.loads(path.read_text())
        record['coreFiles'] = [row for row in record['coreFiles'] if row['path'] != 'holycrab_runtime/uploads.py']
        path.write_text(json.dumps(record))
        report = json.loads(self.command('doctor', '--json').stdout)
        self.assertFalse(report['ok'])
        self.assertFalse(report['checks']['installation']['formatValid'])

    def test_reinstall_can_reduce_agents_and_uninstall_preserves_extra_file(self):
        self.env['HOLYCRAB_INSTALL_AGENTS'] = 'codex,claude'
        self.install()
        self.env['HOLYCRAB_INSTALL_AGENTS'] = 'none'
        self.install()
        for agent in ('.agents', '.claude'):
            self.assertFalse((self.root / 'home' / agent / 'skills/holycrab/SKILL.md').exists())
        library = self.prefix / 'lib/holycrab'
        note = library / 'my-note.txt'
        note.write_text('keep me')
        result = self.command('uninstall', '--yes')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(note.read_text(), 'keep me')
        self.assertFalse((library / 'holycrab_runtime').exists())
        self.assertFalse((self.prefix / 'bin/holycrab').exists())
