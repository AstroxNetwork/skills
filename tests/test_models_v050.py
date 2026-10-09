from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from tests.test_cli import holycrab as cli


MAX = {'model': 'MiniMax-H3-Max', 'prompt': 'A red crab on a beach', 'resolution': '480P', 'duration': 5, 'ratio': '16:9'}
VIDEO = {'model': 'agnes-video-2.5', 'prompt': 'A red crab on a beach', 'size': '720P', 'seconds': 4, 'ratio': '16:9'}
IMAGE = {'model': 'agnes-image-2.5-flash', 'prompt': 'A red crab on a beach', 'size': '1K', 'ratio': '1:1'}


class NewModelsTests(unittest.TestCase):
    def test_directory_is_complete_and_excludes_retired_models(self):
        models = cli.all_models()
        self.assertEqual(len(models), 12)
        self.assertEqual([sum(m['kind'] == k for m in models) for k in ('video', 'image', 'audio')], [7, 4, 1])
        self.assertFalse({'agnes-video-v2.0', 'agnes-video-2.5-flash'} & {m['id'] for m in models})

    def test_correct_kind_and_exact_routes(self):
        for kind, request, route in [('video', MAX, 'minimax'), ('video', VIDEO, 'agnes'), ('image', IMAGE, 'agnes')]:
            with self.subTest(request=request):
                self.assertEqual(cli.generation_endpoints(kind, request), (f'/api/tasks/{route}-generation/freeze-credit', f'/api/tasks/{route}-generation'))
                wrong = 'image' if kind == 'video' else 'video'
                with self.assertRaises(SystemExit):
                    cli.generation_endpoints(wrong, request)
                self.assertNotIn('generateAudio', cli.normalize_generation_request(kind, request))

    def test_duration_and_size_boundaries(self):
        for base, field, good, bad, size_field, good_sizes, bad_size in [
            (MAX, 'duration', [5, 15], [4, 16, True, 5.5], 'resolution', ['480P', '768P'], '2K'),
            (VIDEO, 'seconds', [4, 12], [3, 13, True, 4.5], 'size', ['720P', '1080P', '1K', '2K'], '480P'),
        ]:
            for value in good:
                cli.normalize_generation_request('video', {**base, field: value})
            for value in bad:
                with self.subTest(value=value, model=base['model']), self.assertRaises(SystemExit):
                    cli.normalize_generation_request('video', {**base, field: value})
            for size in good_sizes:
                cli.normalize_generation_request('video', {**base, size_field: size, 'ratio': '1:1'})
            with self.assertRaises(SystemExit):
                cli.normalize_generation_request('video', {**base, size_field: bad_size})
        with self.assertRaisesRegex(SystemExit, '1K requires ratio 1:1'):
            cli.normalize_generation_request('video', {**VIDEO, 'size': '1K'})

    def test_frames_references_and_audio_rules(self):
        for base in (MAX, VIDEO):
            last = cli.normalize_generation_request('video', {**base, 'lastFrameAssetId': 'tail'})
            self.assertEqual(last['ratio'], 'adaptive' if base is MAX else '16:9')
            for field in ('imageAssetIds', 'videoAssetIds', 'audioAssetIds'):
                with self.subTest(model=base['model'], field=field), self.assertRaises(SystemExit):
                    cli.normalize_generation_request('video', {**base, 'lastFrameAssetId': 'tail', field: ['ref']})
        with self.assertRaises(SystemExit):
            cli.normalize_generation_request('video', {**MAX, 'audioAssetIds': ['audio']})
        cli.normalize_generation_request('video', {**VIDEO, 'audioAssetIds': ['audio']})
        mixed = {**MAX, 'audioAssetIds': ['audio'], 'imageAssetIds': ['image']}
        mixed.pop('ratio')
        self.assertEqual(cli.normalize_generation_request('video', mixed)['ratio'], 'adaptive')
        for ratio in (None, 'adaptive'):
            request = dict(MAX)
            if ratio is None:
                request.pop('ratio')
            else:
                request['ratio'] = ratio
            with self.assertRaises(SystemExit):
                cli.normalize_generation_request('video', request)

    def test_reference_counts_and_unsupported_fields(self):
        for kind, base, limits in [('video', MAX, {'imageAssetIds':9, 'videoAssetIds':3, 'audioAssetIds':3}), ('video', VIDEO, {'imageAssetIds':8, 'videoAssetIds':1, 'audioAssetIds':3}), ('image', IMAGE, {'imageAssetIds':14})]:
            for field, maximum in limits.items():
                request = {**base, field: [f'a{i}' for i in range(maximum)]}
                if base is MAX and field == 'audioAssetIds':
                    request['imageAssetIds'] = ['image']
                cli.normalize_generation_request(kind, request)
                with self.assertRaises(SystemExit):
                    cli.normalize_generation_request(kind, {**request, field:[f'a{i}' for i in range(maximum + 1)]})
            for field in ('generateAudio', 'outputFormat', 'imageUrls', 'videoTaskType'):
                with self.subTest(model=base['model'], field=field), self.assertRaises(SystemExit):
                    cli.normalize_generation_request(kind, {**base, field: True})
        for field in ('seconds', 'resolution', 'duration', 'firstFrameAssetId', 'audioAssetIds', 'videoAssetIds'):
            with self.subTest(field=field), self.assertRaises(SystemExit):
                cli.normalize_generation_request('image', {**IMAGE, field: 1})
        for size in ['1K', '2K', '3K', '4K']:
            cli.normalize_generation_request('image', {**IMAGE, 'size':size})
        with self.assertRaises(SystemExit):
            cli.normalize_generation_request('image', {**IMAGE, 'size':'1024x1024'})

    def test_account_permissions_are_validated_not_assumed(self):
        account = {'credit':10, 'disabled_models':[' MiniMax-H3-Max ', 'MiniMax-H3-Max'], 'isMaxEnabled':True, 'apiKey':'secret'}
        self.assertEqual(cli.public_account_data(account), {'credit':10, 'disabled_models':['MiniMax-H3-Max']})
        self.assertEqual(cli.public_account_data({'credit':10, 'disabled_models':[]})['disabled_models'], [])
        for bad in (None, False, {}, 'all', [''], ['ok', 5], ['  ']):
            self.assertNotIn('disabled_models', cli.public_account_data({'credit':10, 'disabled_models':bad}))
        self.assertNotIn('disabled_models', cli.public_account_data({'credit':10}))

    def test_mcp_discovery_and_account_share_cli_projection(self):
        result = cli.mcp_tool_call('capabilities_list', {})
        self.assertEqual(len(result['models']), 12)
        self.assertEqual(cli.mcp_tool_call('capability_get', {'model':MAX['model']})['id'], MAX['model'])
        with patch.object(cli, 'send', return_value=(200, {'code':200, 'data':{'credit':10,'disabled_models':[MAX['model']]}})):
            self.assertEqual(cli.mcp_tool_call('account_get', {})['disabled_models'], [MAX['model']])


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        env = patch.dict(os.environ, {'HOLYCRAB_CONFIG_DIR':temporary.name})
        env.start()
        self.addCleanup(env.stop)

    def test_estimate_and_create_use_same_route_and_native_payload(self):
        for kind, request in [('video', MAX), ('video', VIDEO), ('image', IMAGE)]:
            with self.subTest(model=request['model']), patch.object(cli, 'send', side_effect=[(200, {'code':200,'data':{'frozenCredit':2}}), (200, {'code':200,'data':{'uniqId':'task'}})]) as send:
                result = cli.create_generation(kind, request, confirmed=True)
                self.assertEqual(result['state'], 'created')
                self.assertEqual([c.args[1] for c in send.call_args_list], list(cli.generation_endpoints(kind, request)))
                self.assertEqual([c.kwargs['payload'] for c in send.call_args_list], [request, request])

    def test_failed_estimate_and_permission_denial_never_create(self):
        for kind, request in [('video', MAX), ('video', VIDEO), ('image', IMAGE)]:
            with self.subTest(model=request['model']), patch.object(cli, 'send', return_value=(403, {'message':'Model disabled'})) as send:
                with self.assertRaisesRegex(SystemExit, 'Model disabled'):
                    cli.create_generation(kind, request, confirmed=True)
                self.assertEqual(send.call_count, 1)
                self.assertTrue(send.call_args.args[1].endswith('/freeze-credit'))

    def test_uncertain_submission_keeps_attempt_and_does_not_retry(self):
        for kind, request in [('video', MAX), ('video', VIDEO), ('image', IMAGE)]:
            attempt = 'test-' + request['model'].replace('.', '-')
            with self.subTest(model=request['model']), patch.object(cli, 'send', side_effect=[(200, {'code':200,'data':{'frozenCredit':2}}), TimeoutError()]) as send:
                result = cli.create_generation(kind, request, confirmed=True, attempt_id=attempt)
                self.assertEqual(result['state'], 'unknown')
                self.assertEqual(send.call_count, 2)
                with self.assertRaisesRegex(SystemExit, 'already exists'):
                    cli.create_generation(kind, request, confirmed=True, attempt_id=attempt)
                self.assertEqual(send.call_count, 2)
