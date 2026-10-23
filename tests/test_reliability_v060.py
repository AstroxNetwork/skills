"""Fault-injection checks for the public workflows; never contact the service."""
import argparse
import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('reliability_cli', ROOT / 'holycrab/scripts/holycrab_cli.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
REQUEST = {'model': 'seedream-5-0-lite-260128', 'prompt': 'offline test', 'size': '2K'}


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {'HOLYCRAB_CONFIG_DIR': self.tmp.name, 'HOLYCRAB_NO_UPDATE_CHECK': '1'})
        env.start()
        self.addCleanup(env.stop)

    def test_invalid_estimates_never_reserve_or_create(self):
        for response in ({'raw': '<html>maintenance</html>'}, {}, {'code': 200, 'data': {}},
                         *({'code': 200, 'data': {'frozenCredit': value}} for value in (-1, True, '5', 1.5, None))):
            with self.subTest(response=response), patch.object(cli._owner('send'), 'send', return_value=(200, response)) as send:
                with self.assertRaises((ValueError, SystemExit)):
                    cli.create_generation('image', REQUEST, confirmed=True, attempt_id='invalid')
                self.assertEqual(send.call_count, 1)
                self.assertFalse(cli.attempts_path().exists())

    def test_success_keeps_task_id_when_recording_fails(self):
        update = cli.update_attempt
        def fail_success(identifier, **changes):
            if changes.get('state') == 'created':
                raise OSError('disk full')
            return update(identifier, **changes)
        with patch.object(cli._owner('send'), 'send', side_effect=[(200, {'code': 200, 'data': {'frozenCredit': 0}}),
                                                   (200, {'code': 200, 'data': {'uniqId': 'task1'}})]), \
                patch.object(cli._owner('update_attempt'), 'update_attempt', side_effect=fail_success):
            result = cli.create_generation('image', REQUEST, confirmed=True, attempt_id='known')
        self.assertEqual(result['taskId'], 'task1')
        self.assertEqual(result['state'], 'created')
        self.assertFalse(result['localStateSaved'])

    def test_reading_legacy_records_never_writes_or_releases_ids(self):
        cli.write_private_json(cli.attempts_path(), {'old': {'state': 'invalid'}, 'reserved': None})
        before = cli.attempts_path().read_bytes()
        records = cli.load_attempts()
        self.assertEqual(cli.attempts_path().read_bytes(), before)
        self.assertEqual(records['reserved']['state'], 'unknown')

    def test_doctor_marks_corrupt_ledger_unhealthy(self):
        cli.write_private_json(cli.attempts_path(), {})
        cli.attempts_path().write_text('{bad')
        result = cli.local_health_report()
        self.assertFalse(result['ok'])
        self.assertFalse(result['checks']['attempts']['readable'])

    def test_cached_update_is_recomputed_for_current_version(self):
        cached = {'checkedAtEpoch': cli.time.time(), 'update': {
            'latestVersion': cli.VERSION, 'updateAvailable': True}}
        with patch.object(cli._owner('read_update_state'), 'read_update_state', return_value=cached):
            self.assertFalse(cli.check_for_update()['updateAvailable'])
            self.assertIsNone(cli.cached_update_notice())

    def test_mcp_reports_mutation_rejection_as_error(self):
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
            'name': 'generation_create', 'arguments': {
                'kind': 'image', 'request': REQUEST, 'confirmed': True, 'attemptId': 'mcp1'}}}
        with patch.object(cli._owner('send'), 'send', side_effect=[(200, {'code': 200, 'data': {'frozenCredit': 2}}),
                                                   (400, {'code': 400, 'message': 'rejected'})]):
            result = cli.mcp_dispatch(request)
        self.assertTrue(result['result']['isError'])
        self.assertEqual(result['result']['structuredContent']['state'], 'failed')

    def test_completed_upload_has_queryable_one_shot_record(self):
        source = Path(self.tmp.name) / 'picture.png'
        source.write_bytes(b'\x89PNG\r\n\x1a\n' + b'a' * 32)
        plan = cli.prepare_upload_plan([str(source)])
        with patch.object(cli._owner('upload_prepared_file'), 'upload_prepared_file', return_value={'state': 'uploaded', 'assetUniqId': 'asset1'}):
            cli.execute_upload_plan(plan['uploadPlanId'], confirmed=True)
        record = cli.upload_plan_record(plan['uploadPlanId'])
        self.assertEqual(record['state'], 'completed')
        self.assertEqual(record['uploaded'][0]['assetUniqId'], 'asset1')
        with self.assertRaises(SystemExit):
            cli.execute_upload_plan(plan['uploadPlanId'], confirmed=True)

    def image(self, name='picture.png'):
        source = Path(self.tmp.name) / name
        source.write_bytes(b'\x89PNG\r\n\x1a\n' + b'a' * 32)
        return source

    def test_unknown_result_keeps_attempt_when_disk_also_fails(self):
        original = cli.update_attempt
        def persist(identifier, **changes):
            if changes.get('state') == 'unknown': raise OSError('disk full')
            return original(identifier, **changes)
        with patch.object(cli._owner('send'), 'send', side_effect=[(200, {'code': 200, 'data': {'frozenCredit': 2}}), OSError('offline')]), \
                patch.object(cli._owner('update_attempt'), 'update_attempt', side_effect=persist):
            result = cli.create_generation('image', REQUEST, confirmed=True, attempt_id='unknown1')
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual(result['attemptId'], 'unknown1')
        self.assertFalse(result['localStateSaved'])
        self.assertTrue(cli.attempt_exists('unknown1'))

    def test_changed_request_cannot_reuse_approved_estimate(self):
        with patch.object(cli._owner('send'), 'send', return_value=(200, {'code': 200, 'data': {'frozenCredit': 2}})) as send:
            estimate = cli.estimate_generation('image', REQUEST)
            with self.assertRaisesRegex(ValueError, 'does not match'):
                cli.create_generation('image', {**REQUEST, 'prompt': 'changed'}, confirmed=True, approved_estimate=estimate)
        self.assertEqual(send.call_count, 1)
        self.assertFalse(cli.attempts_path().exists())

    def test_batch_sends_frozen_bytes_even_if_later_original_is_replaced(self):
        first, second = self.image('first.png'), self.image('second.png')
        expected = second.read_bytes()
        plan = cli.prepare_upload_plan([str(first), str(second)])
        sent = []
        def upload(item, group):
            sent.append(item['_stream'].read())
            second.write_bytes(b'changed after first network write')
            return {'state': 'uploaded', 'assetUniqId': 'asset' + str(len(sent))}
        with patch.object(cli._owner('upload_prepared_file'), 'upload_prepared_file', side_effect=upload):
            result = cli.execute_upload_plan(plan['uploadPlanId'], confirmed=True)
        self.assertEqual(sent, [expected, expected])
        self.assertEqual(len(result['uploaded']), 2)

    def test_changed_later_file_stops_whole_batch_before_network(self):
        first, second = self.image('first.png'), self.image('second.png')
        plan = cli.prepare_upload_plan([str(first), str(second)])
        second.write_bytes(second.read_bytes() + b'changed')
        with patch.object(cli._owner('upload_prepared_file'), 'upload_prepared_file') as upload:
            with self.assertRaises(SystemExit): cli.execute_upload_plan(plan['uploadPlanId'], confirmed=True)
        upload.assert_not_called()
        self.assertEqual(cli.upload_plan_record(plan['uploadPlanId'])['state'], 'prepared')

    def test_disk_failure_stops_remaining_uploads_and_keeps_known_asset(self):
        plan = cli.prepare_upload_plan([str(self.image('first.png')), str(self.image('second.png'))])
        original = cli.write_private_json
        def persist(path, value):
            if value.get('uploaded'): raise OSError('disk full')
            original(path, value)
        with patch.object(cli._owner('write_private_json'), 'write_private_json', side_effect=persist), \
                patch.object(cli._owner('upload_prepared_file'), 'upload_prepared_file', return_value={'state': 'uploaded', 'assetUniqId': 'asset1'}) as upload:
            result = cli.execute_upload_plan(plan['uploadPlanId'], confirmed=True)
        self.assertEqual(upload.call_count, 1)
        self.assertEqual(result['uploaded'][0]['assetUniqId'], 'asset1')
        self.assertEqual(len(result['notAttempted']), 1)
        self.assertFalse(result['localStateSaved'])
        self.assertEqual(cli.upload_plan_record(plan['uploadPlanId'])['state'], 'executing')
        with self.assertRaises(SystemExit): cli.execute_upload_plan(plan['uploadPlanId'], confirmed=True)

    def test_two_processes_cannot_execute_the_same_plan(self):
        import subprocess, sys, time
        plan = cli.prepare_upload_plan([str(self.image())])
        marker, release = Path(self.tmp.name) / 'calls', Path(self.tmp.name) / 'release'
        script = '''import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from holycrab_runtime import uploads
marker,release=Path(sys.argv[3]),Path(sys.argv[4])
def upload(item,group):
 with marker.open('a') as out:out.write('call\\n')
 deadline=time.monotonic()+10
 while not release.exists():
  if time.monotonic()>deadline: raise RuntimeError('fixture timeout')
  time.sleep(.01)
 return {'state':'uploaded','assetUniqId':'asset1'}
uploads.upload_prepared_file=upload
uploads.execute_upload_plan(sys.argv[2],confirmed=True)
'''
        args = [sys.executable, '-c', script, str(ROOT / 'holycrab/scripts'), plan['uploadPlanId'], str(marker), str(release)]
        first = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and first.poll() is None and time.monotonic() < deadline: time.sleep(.01)
            self.assertTrue(marker.exists())
            second = subprocess.run(args, capture_output=True, timeout=10)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn(b'already been executed', second.stderr)
            release.touch()
            output, errors = first.communicate(timeout=10)
            self.assertEqual(first.returncode, 0, errors)
            self.assertEqual(marker.read_text(), 'call\n')
        finally:
            release.touch()
            if first.poll() is None: first.kill()
            first.communicate()

    def test_successful_read_of_failed_task_is_not_mcp_tool_error(self):
        with patch.object(cli._owner('send'), 'send', return_value=(200, {'code': 200, 'data': {'uniqId': 'task1', 'step': 3}})):
            result = cli.mcp_dispatch({'id': 1, 'method': 'tools/call', 'params': {'name': 'generation_get', 'arguments': {'taskId': 'task1'}}})
        self.assertFalse(result['result']['isError'])

    def test_atomic_write_failure_keeps_old_file(self):
        path = Path(self.tmp.name) / 'record.json'
        cli.write_private_json(path, {'value': 'old'})
        with patch.object(cli.os, 'replace', side_effect=OSError('fixture rename failure')):
            with self.assertRaises(OSError): cli.write_private_json(path, {'value': 'new'})
        self.assertEqual(json.loads(path.read_text()), {'value': 'old'})
        self.assertEqual(list(path.parent.glob('*.tmp')), [])

    def test_runtime_dependencies_have_no_cycles(self):
        import ast
        graph = {}
        for path in (ROOT / 'holycrab/scripts/holycrab_runtime').glob('*.py'):
            graph[path.stem] = [alias.name for node in ast.parse(path.read_text()).body
                               if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module is None
                               for alias in node.names]
        def visit(name, active):
            self.assertNotIn(name, active, ' -> '.join([*active, name]))
            for child in graph.get(name, []): visit(child, [*active, name])
        for name in graph: visit(name, [])

    def test_recording_warning_and_upload_plan_id_are_visible_in_terminal(self):
        output = cli.format_terminal_result({'attemptId': 'attempt1', 'taskId': 'task1', 'state': 'created',
                                            'localStateSaved': False, 'warning': 'Keep this task ID; disk full'})
        self.assertIn('disk full', output)
        self.assertIn('task1', output)
        preview = cli.prepare_upload_plan([str(self.image())])
        self.assertIn(preview['uploadPlanId'], cli.format_terminal_result(preview))

    def test_cleanup_retains_corrupt_upload_records(self):
        path = cli.upload_plan_path('oldplan')
        cli.write_private_json(path, None)
        cli.cleanup_upload_plans()
        self.assertTrue(path.exists())

    def test_update_check_succeeds_without_writable_cache(self):
        release = {'tag_name': 'v99.0.0', 'draft': False, 'prerelease': False,
                   'html_url': cli.RELEASE_PAGE_PREFIX + 'v99.0.0'}
        expected = cli.release_update_info(release)
        with patch.object(cli._owner('fetch_latest_release'), 'fetch_latest_release', return_value=expected), \
                patch.object(cli._owner('write_private_json'), 'write_private_json', side_effect=OSError('read only')):
            result = cli.check_for_update(force=True)
        self.assertTrue(result['updateAvailable'])
        self.assertEqual(result['release'], release)

    def test_invalid_upload_url_does_not_lose_valid_asset_id(self):
        item = {**cli.inspect_upload_file(str(self.image()), real_human=False), '_stream': io.BytesIO(b'fixture')}
        with patch.object(cli._owner('send'), 'send', return_value=(200, {'code': 200, 'data': {
                'uniqId': 'asset1', 'preSignedUrl': 'http://127.0.0.1/blocked', 'objectKey': 'fixture'}})), \
                patch.object(cli._owner('open_presigned_upload'), 'open_presigned_upload') as upload:
            result = cli.upload_prepared_file(item, None)
        upload.assert_not_called()
        self.assertEqual(result['assetUniqId'], 'asset1')
        self.assertEqual(result['state'], 'unknown')
