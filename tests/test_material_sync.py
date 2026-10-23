"""Backend-owned material policy and Berry request regressions; no network or paid jobs."""
from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from tests.network_fixtures import getaddrinfo

from tests.test_cli import holycrab as cli

BERRY = ('berry-1.0', 'berry-1.0-pro', 'berry-1.0-turbo', 'berry-1.0-pro-turbo')
REJECTION = {'code': 400, 'message': 'Material rejected', 'errorCode': 'TASK_ASSET_SIZE_INVALID',
             'requestId': 'request123', 'apiKey': 'private-test-key',
             'errors': [{'code': 'TASK_ASSET_SIZE_INVALID', 'scope': 'asset', 'assetId': 'asset1',
                         'requestPath': 'imageAssetIds[0]', 'role': 'image_reference', 'field': 'sizeBytes',
                         'actual': 0, 'rule': {'min': 0, 'minInclusive': False, 'max': 30, 'unit': 'byte',
                                               'apiKey': 'private-test-key'},
                         'message': 'invalid size https://cdn.example/a?token=secret', 'retryable': False,
                         'internal': 'hidden'}]}


class MaterialSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'HOLYCRAB_CONFIG_DIR': self.temp.name, 'HOLYCRAB_NO_UPDATE_CHECK': '1'})
        env.start(); self.addCleanup(env.stop)

    def test_error_details_preserve_false_zero_null_and_whitelist(self):
        for actual in (0, False, None):
            response = {**REJECTION, 'errors': [{**REJECTION['errors'][0], 'actual': actual}]}
            with self.assertRaises(SystemExit) as caught:
                cli.response_data(400, response)
            result = caught.exception.result
            self.assertEqual(result['httpStatus'], 400)
            self.assertEqual(result['errorCode'], REJECTION['errorCode'])
            self.assertEqual(result['requestId'], 'request123')
            self.assertIs(result['errors'][0]['actual'], actual)
            self.assertIs(result['errors'][0]['retryable'], False)
            self.assertNotIn('internal', result['errors'][0])
            self.assertNotIn('apiKey', result['errors'][0]['rule'])
            self.assertNotIn('private-test-key', json.dumps(result))
            self.assertNotIn('token=secret', json.dumps(result))
            terminal = cli.format_terminal_result(result)
            for expected in ('asset1', 'imageAssetIds[0]', 'sizeBytes', 'Actual', 'Required'):
                self.assertIn(expected, terminal)

    def test_collection_errors_legacy_message_and_task_response_keep_details(self):
        response = {**REJECTION, 'errors': [REJECTION['errors'][0], {
            'code': 'TASK_ASSET_TOTAL_DURATION_EXCEEDED', 'scope': 'collection',
            'assetIds': ['video1', 'video2'], 'requestPath': 'videoAssetIds', 'field': 'durationSeconds',
            'actual': 20.01, 'rule': {'max': 15, 'maxInclusive': True, 'unit': 's'}, 'retryable': False}]}
        envelope = cli.public_task_response(response)
        self.assertEqual(envelope['errors'][1]['assetIds'], ['video1', 'video2'])
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.print_response(400, envelope), 1)
        self.assertEqual(json.loads(output.getvalue())['errors'][1]['actual'], 20.01)
        with self.assertRaisesRegex(SystemExit, 'old backend') as caught:
            cli.response_data(400, {'message': 'old backend'})
        self.assertEqual(caught.exception.result['httpStatus'], 400)
        self.assertNotIn('errors', caught.exception.result)

    def test_mcp_estimate_failure_is_structured_and_creates_no_attempt(self):
        with patch.object(cli._owner('send'), 'send', return_value=(400, REJECTION)) as send:
            response = cli.mcp_dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                'name': 'generation_estimate', 'arguments': {'kind': 'video', 'request': {
                    'model': BERRY[0], 'prompt': 'a crab'}}}})['result']
        self.assertTrue(response['isError'])
        self.assertEqual(response['structuredContent']['errors'][0]['actual'], 0)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(cli.attempt_records(), [])

    def test_cli_api_failure_prints_json_and_returns_nonzero(self):
        argv = ['holycrab', 'generate', 'estimate', '--kind', 'video', '--json', json.dumps({'model': BERRY[0], 'prompt': 'crab'})]
        output = io.StringIO()
        with patch('sys.argv', argv), patch.object(cli._owner('send'), 'send', return_value=(400, REJECTION)), \
                patch.object(cli._owner('startup_maintenance'), 'startup_maintenance'), redirect_stdout(output), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(), 1)
        self.assertEqual(json.loads(output.getvalue())['requestId'], 'request123')

    def test_rejected_submission_records_details_and_never_retries(self):
        request = {'model': BERRY[0], 'prompt': 'crab'}
        with patch.object(cli._owner('send'), 'send', side_effect=[(200, {'code': 200, 'data': {'frozenCredit': 1}}), (400, REJECTION)]) as send:
            result = cli.create_generation('video', request, confirmed=True, attempt_id='materialRejected')
        self.assertEqual(send.call_count, 2)
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['errors'][0]['actual'], 0)
        self.assertEqual(cli.attempt_record('materialRejected')['errors'], result['errors'])
        with patch.object(cli._owner('send'), 'send') as send:
            with self.assertRaises(SystemExit):
                cli.create_generation('video', request, confirmed=True, attempt_id='materialRejected')
            send.assert_not_called()

    def test_material_counts_combinations_and_mentions_are_backend_owned(self):
        for model in ('seedance-2-0', 'MiniMax-H3', BERRY[0]):
            request = {'model': model, 'prompt': 'crab', 'duration': 5,
                       'resolution': '768P' if model == 'MiniMax-H3' else '720p',
                       'imageAssetIds': ['same'] * 101, 'videoAssetIds': ['video'] * 101,
                       'audioAssetIds': ['audio'] * 101, 'lastFrameAssetId': 'tail'}
            self.assertEqual(cli.normalize_generation_request('video', request)['imageAssetIds'], request['imageAssetIds'])
        for prompt in ('@Image1', '@Audio9', 'no mentions'):
            request = {'textPrompt': prompt, 'references': [{'assetId': 'a', 'type': 'audio'}, {'image_url': 'https://cdn.example/a.png'}] * 10}
            self.assertEqual(cli.normalize_generation_request('audio', request), request)
        with self.assertRaises(SystemExit):
            cli.normalize_generation_request('video', {'model': BERRY[0], 'prompt': 'crab', 'imageAssetIds': [1]})

    def test_audio_new_sources_migration_and_estimate_body(self):
        for reference in ({'assetId': 'audio1', 'type': 'audio'}, {'assetId': 'image1', 'type': 'image'},
                          {'audio_url': 'https://cdn.example/a.wav'}, {'image_url': 'https://cdn.example/a.png'}):
            request = {'textPrompt': 'crab @Audio1', 'references': [reference]}
            with patch.object(cli._owner('send'), 'send', return_value=(200, {'code': 200, 'data': {'frozenCredit': 0}})) as send:
                cli.estimate_generation('audio', request)
                send.assert_called_once_with('POST', '/api/tasks/audio-generation/freeze-credit', payload=request)
        for field in ('speaker', 'audio_data', 'image_data'):
            with self.assertRaisesRegex(SystemExit, r'upload.*assetId.*URL'):
                cli.normalize_generation_request('audio', {'textPrompt': 'crab', 'references': [{field: 'legacy'}]})
        for reference in ({'assetId': 'a'}, {'assetId': 'a', 'type': 'video'}, {'audio_url': 'x', 'image_url': 'y'}):
            with self.assertRaises(SystemExit):
                cli.normalize_generation_request('audio', {'textPrompt': 'crab', 'references': [reference]})

    def test_upload_rejection_keeps_material_details_without_put(self):
        path = Path(self.temp.name) / 'image.jpg'
        path.write_bytes(b"\xff\xd8\xff" + b"fixture")
        item = cli.inspect_upload_file(str(path), real_human=False)
        with patch.object(cli._owner('send'), 'send', return_value=(400, REJECTION)) as send, \
                patch.object(cli._owner('open_presigned_upload'), 'open_presigned_upload') as put:
            result = cli.upload_prepared_file(item, None)
            self.assertEqual(result['errors'][0]['actual'], 0)
            self.assertEqual(send.call_count, 1)
            put.assert_not_called()
        terminal = cli.format_terminal_result({'uploaded': [], 'notAttempted': [], 'failedOrUnknown': result})
        self.assertIn('sizeBytes', terminal)
        self.assertIn('request123', terminal)

    def test_image_ids_and_urls_reach_backend_with_no_count_gate(self):
        request = {'model': 'seedream-5-0-pro-260628', 'prompt': 'crab',
                   'imageAssetIds': ['image1'] * 15, 'imageUrls': ['https://cdn.example/a.png'] * 15}
        with patch.object(cli._owner('send'), 'send', return_value=(200, {'code': 200, 'data': {'frozenCredit': 0}})) as send:
            cli.estimate_generation('image', request)
            self.assertEqual(send.call_args.kwargs['payload'], request)
        self.assertEqual(cli.find_model(request['model'])['referenceSelectionPriority'], 'nonempty imageAssetIds before imageUrls')

    def test_metadata_unknowns_are_uncoerced_and_processing_ready_is_independent(self):
        raw = {'uniqId': 'asset1', 'step': 'UPLOADED_TO_ARK', 'duration': 3, 'durationSeconds': 2.998,
               'mediaFormat': 'mov', 'width': None, 'height': 1080, 'sizeBytes': 0,
               'frameRate': 29.97, 'videoCodec': 'h264', 'audioCodec': None,
               'metadataStatus': 'FAILED', 'metadataErrorCode': 'PROBE_FAILED', 'metadataUpdatedAt': None,
               'arkAssetId': 'private'}
        projected = cli.public_asset(raw)
        self.assertTrue(projected['ready'])
        for key in set(raw) - {'arkAssetId'}:
            self.assertEqual(projected[key], raw[key])
        self.assertNotIn('arkAssetId', projected)
        text = cli.format_terminal_result(projected)
        self.assertIn('2.998', text)
        self.assertIn('FAILED', text)

    def test_metadata_retry_uncertain_responses_keep_query_id_at_mcp_and_cli_exits(self):
        cases = [
            (503, {'code': 503, 'message': 'read after scheduling failed'}),
            (408, {'code': 408}),
            (200, {'code': 503, 'message': 'read after scheduling failed'}),
            (200, {'code': 200, 'data': None}),
            (200, {'code': 200, 'data': []}),
            (200, {'code': 200, 'data': {'uniqId': 'different', 'metadataStatus': 'PENDING'}}),
            (200, {'data': {'uniqId': 'asset1', 'metadataStatus': 'PENDING'}}),
            (200, 'malformed response'),
            (200, {'code': True, 'data': {'uniqId': 'asset1', 'metadataStatus': 'PENDING'}}),
            (200, {'code': 200, 'data': {'uniqId': 'asset1'}}),
        ]
        for case in cases:
            with self.subTest(case=case, interface='mcp'), patch.object(cli._owner('send'), 'send', return_value=case) as send:
                response = cli.mcp_dispatch({'id': 3, 'method': 'tools/call', 'params': {
                    'name': 'asset_metadata_retry', 'arguments': {'assetId': 'asset1'}}})['result']
                self.assertTrue(response['isError'])
                result = response['structuredContent']
                self.assertEqual(result['state'], 'unknown')
                self.assertEqual(result['assetId'], 'asset1')
                self.assertEqual(result['nextAction']['command'], 'holycrab assets get asset1')
                send.assert_called_once_with('POST', '/api/user-assets/asset1/metadata/retry')
            output = io.StringIO()
            with self.subTest(case=case, interface='cli'), patch.object(cli._owner('send'), 'send', return_value=case) as send, \
                    patch.object(cli._owner('startup_maintenance'), 'startup_maintenance'), \
                    patch('sys.argv', ['holycrab', 'assets', 'metadata', 'retry', 'asset1']), \
                    redirect_stdout(output), redirect_stderr(io.StringIO()):
                self.assertNotEqual(cli.main(), 0)
                result = json.loads(output.getvalue())
                self.assertEqual(result['state'], 'unknown')
                self.assertEqual(result['assetId'], 'asset1')
                self.assertEqual(result['nextAction']['command'], 'holycrab assets get asset1')
                send.assert_called_once_with('POST', '/api/user-assets/asset1/metadata/retry')

    def test_metadata_retry_network_and_interruption_keep_recovery_without_retry(self):
        for error in (OSError('connection lost'), KeyboardInterrupt()):
            for interface in ('mcp', 'cli'):
                output = io.StringIO()
                with self.subTest(error=type(error).__name__, interface=interface), \
                        patch.object(cli._owner('send'), 'send', side_effect=error) as send, \
                        patch.object(cli._owner('startup_maintenance'), 'startup_maintenance'), \
                        patch('sys.argv', ['holycrab', 'assets', 'metadata', 'retry', 'asset1']), \
                        redirect_stdout(output), redirect_stderr(io.StringIO()):
                    if interface == 'mcp':
                        response = cli.mcp_dispatch({'id': 4, 'method': 'tools/call', 'params': {
                            'name': 'asset_metadata_retry', 'arguments': {'assetId': 'asset1'}}})['result']
                        self.assertTrue(response['isError'])
                        result = response['structuredContent']
                    else:
                        self.assertNotEqual(cli.main(), 0)
                        result = json.loads(output.getvalue())
                    self.assertEqual(result['state'], 'unknown')
                    self.assertEqual(result['assetId'], 'asset1')
                    self.assertEqual(result['nextAction']['command'], 'holycrab assets get asset1')
                    send.assert_called_once_with('POST', '/api/user-assets/asset1/metadata/retry')

    def test_metadata_retry_explicit_rejection_preserves_failed_details(self):
        for status in (400, 404, 429):
            with self.subTest(status=status), patch.object(cli._owner('send'), 'send', return_value=(status, REJECTION)) as send:
                response = cli.mcp_dispatch({'id': 5, 'method': 'tools/call', 'params': {
                    'name': 'asset_metadata_retry', 'arguments': {'assetId': 'asset1'}}})['result']
                self.assertTrue(response['isError'])
                self.assertEqual(response['structuredContent']['state'], 'failed')
                self.assertEqual(response['structuredContent']['errors'][0]['actual'], 0)
                self.assertEqual(response['structuredContent']['httpStatus'], status)
                send.assert_called_once_with('POST', '/api/user-assets/asset1/metadata/retry')

    def test_metadata_retry_cli_and_mcp_are_single_explicit_mutations(self):
        tool = next(t for t in cli.MCP_TOOLS if t['name'] == 'asset_metadata_retry')
        self.assertFalse(tool['annotations']['readOnlyHint'])
        raw = {'uniqId': 'asset1', 'metadataStatus': 'PENDING', 'metadataUpdatedAt': '2026-10-23T12:00:00'}
        with patch.object(cli._owner('send'), 'send', return_value=(200, {'code': 200, 'data': raw})) as send:
            result = cli.mcp_tool_call('asset_metadata_retry', {'assetId': 'asset1'})
            self.assertEqual(result['metadataStatus'], 'PENDING')
            send.assert_called_once_with('POST', '/api/user-assets/asset1/metadata/retry')
        args = cli.build_parser().parse_args(['assets', 'metadata', 'retry', 'asset1'])
        with patch.object(cli._owner('send'), 'send', return_value=(429, REJECTION)) as send:
            response = cli.mcp_dispatch({'id': 2, 'method': 'tools/call', 'params': {'name': 'asset_metadata_retry', 'arguments': {'assetId': 'asset1'}}})
            self.assertTrue(response['result']['isError']); self.assertEqual(send.call_count, 1)
        self.assertEqual(args.uniq_id, 'asset1')


class BerryTests(unittest.TestCase):
    setUp = MaterialSyncTests.setUp

    def test_each_model_estimate_confirm_create_query_and_download(self):
        class Remote(io.BytesIO):
            headers = {'Content-Length': '5'}
        for index, model in enumerate(BERRY):
            task_id = 'berryTask' + str(index)
            request = {'model': model, 'prompt': 'crab'}
            task = {'uniqId': task_id, 'step': 2, 'videoUrl': 'https://cdn.example/result.mp4'}
            calls = []
            def send(method, path, **kwargs):
                calls.append((method, path, kwargs))
                if path.endswith('/freeze-credit'): return 200, {'code': 200, 'data': {'frozenCredit': 7}}
                if method == 'POST': return 200, {'code': 200, 'data': {'uniqId': task_id}}
                self.assertEqual(path, '/api/tasks/' + task_id)
                return 200, {'code': 200, 'data': task}
            with self.subTest(model=model), patch.object(cli._owner('send'), 'send', side_effect=send), \
                    patch.object(cli.socket, 'getaddrinfo', side_effect=getaddrinfo), \
                    patch.object(cli._owner('open_download'), 'open_download', return_value=Remote(b'video')):
                estimate = cli.create_generation('video', request, confirmed=False)
                self.assertTrue(estimate['confirmationRequired'])
                self.assertEqual(len(calls), 1)
                created = cli.create_generation('video', request, confirmed=True, attempt_id='berryFlow' + str(index), approved_estimate=estimate)
                self.assertEqual(created['taskId'], task_id)
                self.assertEqual(created['state'], 'created')
                queried = cli.mcp_tool_call('generation_get', {'taskId': task_id})
                self.assertEqual(queried['videoUrl'], task['videoUrl'])
                destination = Path(self.temp.name) / (task_id + '.mp4')
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.command_download(argparse.Namespace(uniq_id=task_id, output=str(destination), index=0, force=False)), 0)
                self.assertEqual(destination.read_bytes(), b'video')
                self.assertEqual(sum(method == 'POST' and not path.endswith('/freeze-credit') for method, path, _ in calls), 1)
                self.assertEqual(sum(path.endswith('/freeze-credit') for _, path, _ in calls), 2)
                with self.assertRaises(SystemExit):
                    cli.create_generation('video', request, confirmed=True, attempt_id='berryFlow' + str(index))
                self.assertEqual(len(calls), 5)

    def test_catalog_defaults_routes(self):
        self.assertEqual(len(cli.all_models()), 16)
        for model in BERRY:
            result = cli.normalize_generation_request('video', {'model': model, 'prompt': 'crab'})
            self.assertEqual({k: result[k] for k in ('duration', 'resolution', 'ratio', 'audio')},
                             {'duration': 5, 'resolution': '1080p', 'ratio': 'adaptive', 'audio': True})
            self.assertEqual(cli.generation_endpoints('video', result), ('/api/tasks/mulerouter-generation/freeze-credit', '/api/tasks/mulerouter-generation'))
            self.assertEqual(cli.find_model(model)['capabilitySnapshotVersion'], '2026-10-23')

    def test_scalar_boundaries_and_utf16_prompt(self):
        for model in BERRY:
            base = {'model': model, 'prompt': 'crab'}
            for duration in (2, 30): cli.normalize_generation_request('video', {**base, 'duration': duration})
            for resolution in (('1080p', '2k', '4k') if '-pro' in model else ('480p', '720p', '1080p')):
                cli.normalize_generation_request('video', {**base, 'resolution': resolution})
            bad_res = '720p' if '-pro' in model else '4k'
            for field, value in [('duration', 1), ('duration', 31), ('duration', True), ('duration', 2.5),
                                 ('resolution', bad_res), ('ratio', '21:9'), ('seed', -1), ('seed', 2147483648),
                                 ('audio', 1), ('enableThinking', True), ('watermark', True), ('prompt', '   ')]:
                with self.subTest(model=model, field=field, value=value), self.assertRaises(SystemExit):
                    cli.normalize_generation_request('video', {**base, field: value})
            cli.normalize_generation_request('video', {**base, 'prompt': '😀' * 10000, 'seed': 2147483647})
            with self.assertRaises(SystemExit):
                cli.normalize_generation_request('video', {**base, 'prompt': '😀' * 10000 + 'a'})

if __name__ == '__main__': unittest.main()
