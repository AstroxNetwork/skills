"""Regression oracles for adversarial review findings, without real accounts."""
import argparse
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tests.test_reliability_v060 import cli
from tests import test_download_integrity as downloads


class AdversarialRegressionTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'HOLYCRAB_NO_UPDATE_CHECK': '1'})
        environment.start()
        self.addCleanup(environment.stop)

    def test_modified_owned_skill_is_not_deleted(self):
        lifecycle = cli._owner('remove_managed_skill_files')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = root / 'SKILL.md'
            skill.write_text('user customization', encoding='utf-8')
            manifest = {'agents': ['codex'], 'coreFiles': [{'path': str(skill), 'sha256': '0' * 64}]}
            with patch.object(lifecycle, '_skill_root', return_value=root):
                warnings = lifecycle.remove_managed_skill_files(manifest)
            self.assertTrue(skill.is_file(), 'Uninstall deleted a customized user file')
            self.assertEqual(skill.read_text(encoding='utf-8'), 'user customization')
            self.assertTrue(any('kept' in warning for warning in warnings))
    def test_changed_quote_is_rejected_before_attempt_or_submission(self):
        request = {'model': 'berry-1.0', 'prompt': 'fixture'}
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'HOLYCRAB_CONFIG_DIR': temp}), \
                patch.object(cli._owner('send'), 'send', side_effect=[
                    (200, {'code': 200, 'data': {'frozenCredit': 7}}),
                    (200, {'code': 200, 'data': {'frozenCredit': 8}}),
                    (200, {'code': 200, 'data': {'uniqId': 'unexpectedTask'}})]) as send:
            quote = cli.create_generation('video', request, confirmed=False)
            with self.assertRaisesRegex(ValueError, 'changed from 7 to 8'):
                cli.create_generation('video', request, confirmed=True, approved_estimate=quote, attempt_id='changed')
            self.assertTrue(all(call.args[1].endswith('/freeze-credit') for call in send.call_args_list))
            self.assertFalse(cli.attempt_exists('changed'))

    def test_explicit_approved_credit_zero_and_changed_price(self):
        request = {'model': 'berry-1.0', 'prompt': 'fixture'}
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'HOLYCRAB_CONFIG_DIR': temp}), \
                patch.object(cli._owner('send'), 'send', return_value=(200, {'code': 200, 'data': {'frozenCredit': 1}})) as send:
            result = cli.mcp_dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                'name': 'generation_create', 'arguments': {'kind': 'video', 'request': request,
                'confirmed': True, 'attemptId': 'zero', 'approvedCredits': 0}}})
            self.assertTrue(result['result']['isError'])
            self.assertIn('changed from 0 to 1', result['result']['content'][0]['text'])
            self.assertEqual(send.call_count, 1)

    def test_explicit_zero_limit_cannot_be_overridden_by_interactive_quote(self):
        request = {'model': 'berry-1.0', 'prompt': 'fixture'}
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'HOLYCRAB_CONFIG_DIR': temp}), \
                patch.object(cli._owner('send'), 'send', side_effect=[
                    (200, {'code': 200, 'data': {'frozenCredit': 7}}),
                    (200, {'code': 200, 'data': {'frozenCredit': 7}}),
                    (200, {'code': 200, 'data': {'uniqId': 'unexpectedTask'}})]) as send:
            quote = cli.create_generation('video', request, confirmed=False)
            with self.assertRaisesRegex(ValueError, 'changed from 0 to 7'):
                cli.create_generation('video', request, confirmed=True, approved_estimate=quote,
                                      approved_credits=0, attempt_id='conflict')
            self.assertFalse(cli.attempt_exists('conflict'))
            self.assertTrue(all(call.args[1].endswith('/freeze-credit') for call in send.call_args_list))
    def test_invalid_credential_never_reaches_header_or_saved_config(self):
        state = cli._owner("credential")
        for value in ("private\nkey", "private\rkey", "private\x00key", "private中文"):
            with self.subTest(value=repr(value)), self.assertRaisesRegex(SystemExit, "invalid characters") as raised:
                state.credential(value)
            self.assertNotIn("private", str(raised.exception))

    def test_multiline_piped_key_is_rejected_without_saving_or_verifying(self):
        commands = cli._owner('command_set_key')
        with patch.object(commands.sys, 'stdin', io.StringIO('fixture-first\nfixture-second\n')), \
                patch.object(commands._state, 'save_config') as save, \
                patch.object(commands._tasks, 'query_account') as query, \
                self.assertRaisesRegex(SystemExit, 'invalid characters'):
            commands.command_set_key(argparse.Namespace(stdin=True, no_verify=False))
        save.assert_not_called()
        query.assert_not_called()

    def test_empty_nonobject_mcp_params_and_arguments_are_rejected(self):
        for value in (None, False, 0, '', []):
            for method in ('initialize', 'tools/call'):
                with self.subTest(value=value, method=method):
                    result = cli.mcp_dispatch({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': value})
                    self.assertEqual(result['error']['code'], -32602)
            result = cli.mcp_dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                      'params': {'name': 'cli_status', 'arguments': value}})
            self.assertTrue(result['result']['isError'])
    def test_damaged_dpapi_can_be_replaced_or_cleared_without_decrypting(self):
        state = cli._owner("load_config")
        commands = cli._owner("command_set_key")
        with tempfile.TemporaryDirectory() as temp:
            self._credential_recovery(Path(temp), state, commands)

    def _credential_recovery(self, directory, state, commands):
        source = directory / "config.json"
        with patch.dict(os.environ, {"HOLYCRAB_CONFIG_DIR": str(directory)}), \
                patch.object(state.os, "name", "nt"), \
                patch.object(state, "_dpapi_unprotect", side_effect=OSError("invalid ciphertext")), \
                patch.object(state, "_dpapi_protect", return_value="new encrypted credential"), \
                patch.object(state, "config_path", return_value=source), \
                patch.object(state, "config_dir", return_value=directory), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            for action in ("replace", "clear"):
                source.write_text(json.dumps({"apiKeyDpapi": "broken", "custom": "preserved"}))
                if action == "replace":
                    with patch.object(commands.sys, "stdin", io.StringIO("fixture-new-key\n")):
                        commands.command_set_key(argparse.Namespace(stdin=True, no_verify=True))
                else:
                    commands.command_clear_key(argparse.Namespace())
                result = json.loads(source.read_text())
                self.assertEqual(result["custom"], "preserved")
                self.assertNotIn("apiKey", result)
                self.assertEqual(result.get("apiKeyDpapi"), "new encrypted credential" if action == "replace" else None)

    def test_eof_is_safe_cancellation_without_traceback(self):
        commands = cli._owner("main")
        with patch.object(commands.sys, "argv", ["holycrab", "setup"]), \
                patch.object(commands, "run_cli_command", side_effect=EOFError), \
                redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(commands.main(), 130)
        self.assertIn("input", errors.getvalue().lower())
        self.assertNotIn("Traceback", errors.getvalue())

    def test_malformed_signed_url_redaction_never_raises(self):
        result = cli.sanitize_text_for_output("Rejected https://[?token=secret-value")
        self.assertNotIn("secret-value", result)


class PartialResponseTests(unittest.TestCase):
    def test_partial_range_response_never_replaces_complete_output(self):
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / "output.mp4"
            destination.write_bytes(b"original complete file")
            with self.assertRaisesRegex(SystemExit, "complete|partial"):
                downloads.DownloadIntegrityTests().download(destination, b"abc", b"Content-Length: 3\r\nContent-Range: bytes 0-2/10\r\n", force=True)
            self.assertEqual(destination.read_bytes(), b"original complete file")
