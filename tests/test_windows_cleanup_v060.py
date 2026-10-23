"""Owned-file cleanup regressions, with an actual Windows sharing-lock check."""
import argparse
import hashlib
import io
import os
import tempfile
import threading
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

try:
    from .test_reliability_v060 import cli
except ImportError:
    from test_reliability_v060 import cli

lifecycle = cli._owner('perform_uninstall')


class WindowsCleanupTests(unittest.TestCase):
    def test_cleanup_helper_does_not_inherit_other_powershell_module_paths(self):
        with patch.object(lifecycle.shutil, 'which', return_value='powershell.exe'), \
             patch.object(lifecycle.subprocess, 'Popen') as spawn, \
             patch.dict(lifecycle.os.environ, {'PSModulePath': 'PowerShell7/modules',
                                              'pSmOdUlEpAtH': 'other/modules',
                                              'HOLYCRAB_TEST_SENTINEL': 'keep'}):
            lifecycle.schedule_windows_program_cleanup(Path('launcher.cmd'), Path('library'))
            args, kwargs = spawn.call_args
            try:
                child_environment = kwargs['env']
                self.assertFalse(any(key.upper() == 'PSMODULEPATH' for key in child_environment))
                self.assertEqual(child_environment['HOLYCRAB_TEST_SENTINEL'], 'keep')
            finally:
                Path(args[0][args[0].index('-File') + 1]).unlink()

    def fixture(self, root):
        library = root / 'prefix/lib/holycrab'
        library.mkdir(parents=True)
        rows = []
        for name in ('holycrab_cli.py', 'holycrab_runtime/state.py', 'vendor/fixture.whl'):
            path = library / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'owned contents')
            rows.append({'path': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        (library / 'installation.json').write_text('{}')
        return library, {'coreFiles': rows}

    def test_transient_lock_is_retried_and_extra_files_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            library, manifest = self.fixture(Path(temp))
            locked = library / 'vendor/fixture.whl'
            note = library / 'notes.txt'
            note.write_text('user notes')
            original = Path.unlink
            attempts = []

            def unlink(path, *args, **kwargs):
                if path == locked:
                    attempts.append(path)
                    if len(attempts) < 3:
                        raise PermissionError('simulated Windows sharing violation')
                return original(path, *args, **kwargs)

            with patch.object(Path, 'unlink', unlink):
                warnings = lifecycle.remove_windows_program_files(manifest, library, timeout=1, interval=.001)
            self.assertEqual(len(attempts), 3)
            self.assertEqual(warnings, [])
            self.assertEqual(note.read_text(), 'user notes')
            self.assertFalse(locked.exists())
            self.assertFalse((library / 'holycrab_cli.py').exists())
            self.assertFalse((library / 'installation.json').exists())

    def test_persistent_lock_keeps_bootstrap_and_ownership_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            library, manifest = self.fixture(Path(temp))
            locked = library / 'vendor/fixture.whl'
            original = Path.unlink

            def unlink(path, *args, **kwargs):
                if path == locked:
                    raise PermissionError('simulated persistent sharing violation')
                return original(path, *args, **kwargs)

            with patch.object(Path, 'unlink', unlink):
                with self.assertRaises(PermissionError):
                    lifecycle.remove_windows_program_files(manifest, library, timeout=.01, interval=.002)
            self.assertTrue(locked.exists())
            self.assertTrue((library / 'holycrab_cli.py').exists())
            self.assertTrue((library / 'installation.json').exists())

    def test_modified_owned_file_is_preserved_without_retrying(self):
        with tempfile.TemporaryDirectory() as temp:
            library, manifest = self.fixture(Path(temp))
            modified = library / 'holycrab_runtime/state.py'
            modified.write_bytes(b'user modification')
            with patch.object(lifecycle.time, 'sleep') as sleep:
                warnings = lifecycle.remove_windows_program_files(manifest, library)
            sleep.assert_not_called()
            self.assertEqual(modified.read_bytes(), b'user modification')
            self.assertEqual(len(warnings), 1)
            self.assertTrue((library / 'installation.json').exists())

    def test_locked_runtime_prevents_purge_and_launcher_cleanup(self):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            root = Path(temp)
            library, manifest = self.fixture(root)
            prefix = root / 'prefix'
            launcher = prefix / 'bin/holycrab.cmd'
            launcher.parent.mkdir()
            launcher.write_text('fixture launcher')
            config = root / 'config'
            config.mkdir()
            (config / 'config.json').write_text('{"apiKey":"fixture"}')
            manifest.update(schemaVersion=2, pathRegistration={'kind': 'none', 'addedByInstaller': False})
            stack.enter_context(patch.object(lifecycle, 'os', SimpleNamespace(name='nt')))
            stack.enter_context(patch.object(lifecycle, 'validated_uninstall_manifest', return_value=(manifest, prefix, library, launcher)))
            stack.enter_context(patch.object(lifecycle._state, 'config_dir', return_value=config))
            stack.enter_context(patch.object(lifecycle, 'remove_managed_mcp_registrations', return_value=[]))
            stack.enter_context(patch.object(lifecycle, 'remove_managed_skill_files', return_value=[]))
            stack.enter_context(patch.object(lifecycle, 'remove_windows_program_files', side_effect=PermissionError('locked runtime')))
            cleanup = stack.enter_context(patch.object(lifecycle, 'schedule_windows_program_cleanup'))
            purge = stack.enter_context(patch.object(lifecycle, 'purge_known_local_state'))
            path_cleanup = stack.enter_context(patch.object(lifecycle, 'remove_managed_path_registration'))
            output, errors = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(errors):
                status = lifecycle.perform_uninstall(argparse.Namespace(yes=True, purge=True))
            self.assertEqual(status, 1)
            cleanup.assert_not_called()
            purge.assert_not_called()
            path_cleanup.assert_not_called()
            self.assertIn('local credentials and records were kept', errors.getvalue().lower())
            self.assertNotIn('uninstall completed', output.getvalue())
            self.assertTrue(launcher.exists())
            self.assertTrue((config / 'config.json').exists())

    def test_purge_includes_disposable_update_lock_but_keeps_unknown_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'update-state.lock').write_text('')
            (root / 'notes.txt').write_text('keep')
            with patch.object(lifecycle._state, 'config_dir', return_value=root):
                lifecycle.purge_known_local_state()
            self.assertFalse((root / 'update-state.lock').exists())
            self.assertEqual((root / 'notes.txt').read_text(), 'keep')

    @unittest.skipUnless(os.name == 'nt', 'Requires native Windows sharing locks')
    def test_native_file_lock_released_during_cleanup(self):
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        with tempfile.TemporaryDirectory() as temp:
            library, manifest = self.fixture(Path(temp))
            locked = library / 'vendor/fixture.whl'
            handle = kernel.CreateFileW(str(locked), 0x80000000, 0, None, 3, 0, None)
            self.assertNotEqual(handle, ctypes.c_void_p(-1).value, ctypes.get_last_error())
            released = threading.Event()

            def release():
                kernel.CloseHandle(handle)
                released.set()

            timer = threading.Timer(.3, release)
            timer.start()
            try:
                warnings = lifecycle.remove_windows_program_files(manifest, library, timeout=3, interval=.02)
                self.assertTrue(released.is_set(), 'Cleanup bypassed the native lock')
                self.assertEqual(warnings, [])
                self.assertFalse(library.exists())
            finally:
                timer.join()


if __name__ == '__main__':
    unittest.main()
