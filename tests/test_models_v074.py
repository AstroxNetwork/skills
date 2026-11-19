"""New models and resolution conflicts through the shared runtime boundary."""
import os
import tempfile
import unittest
from unittest.mock import patch

from tests.test_cli import holycrab as cli
from holycrab_runtime import capabilities as caps, generation, real_human, results, state, transport, workflows

FLASH = 'dreamina-seedance-2-5-flash-261115'
SEEDANCE = 'dreamina-seedance-2-5-260628'
IMAGE = 'dola-seedream-5-5-261028'


def ok(data):
    return 200, {'code': 200, 'data': data}


class Release074Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        env = patch.dict(os.environ, {'HOLYCRAB_CONFIG_DIR': temporary.name,
            'HOLYCRAB_API_KEY': 'fixture-074', 'HOLYCRAB_NO_UPDATE_CHECK': '1'})
        env.start()
        self.addCleanup(env.stop)

    def test_new_models_estimate_and_create_exact_same_request(self):
        for kind, request, normalized, route in [
            ('video', {'model': FLASH, 'prompt': 'crab', 'duration': 30, 'resolution': '720p'},
             {'model': FLASH, 'prompt': 'crab', 'duration': 30, 'resolution': '720p', 'generateAudio': True}, 'generation'),
            ('image', {'model': IMAGE, 'prompt': 'crab', 'size': '1.5k'},
             {'model': IMAGE, 'prompt': 'crab', 'size': '1.5K', 'outputFormat': 'png'}, 'image-generation'),
            ('video', {'model': SEEDANCE, 'prompt': 'crab', 'duration': 5, 'resolution': '4k'},
             {'model': SEEDANCE, 'prompt': 'crab', 'duration': 5, 'resolution': '4k', 'generateAudio': True}, 'generation'),
        ]:
            with self.subTest(model=request['model']), patch.object(transport, 'send', side_effect=[
                    ok({'frozenCredit': 9}), ok({'uniqId': 'created', 'step': 1})]) as send:
                result = generation.create_generation(kind, request, confirmed=True, approved_credits=9)
                self.assertEqual(result['taskId'], 'created')
                self.assertEqual([(c.args[:2], c.kwargs['payload']) for c in send.call_args_list], [
                    (('POST', '/api/tasks/' + route + '/freeze-credit'), normalized),
                    (('POST', '/api/tasks/' + route), normalized)])

    def test_flash_modes_limits_and_ark_readiness(self):
        model = caps.find_model(FLASH)
        self.assertEqual(model['referenceLimits'], dict(caps.find_model(SEEDANCE)['referenceLimits'],
            images=10, videos=5, audio=5, total=20))
        for mode in ('reference', 'frames', 'edit', 'extend'):
            request = {'model': FLASH, 'prompt': 'crab', 'duration': -1 if mode == 'edit' else 4,
                       'resolution': '480p', 'videoTaskType': mode, 'generateAudio': False}
            if mode == 'frames': request['firstFrameAssetId'] = 'image1'
            if mode in ('edit', 'extend'): request['sourceVideoAssetId'] = 'video1'
            self.assertFalse(caps.normalize_generation_request('video', request)['generateAudio'])
        for extra in ({'resolution': '1080p'}, {'resolution': '4k'}, {'draft': True},
                      {'duration': 31}, {'duration': 3}, {'videoTaskType': 'edit', 'duration': 4}):
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                caps.normalize_generation_request('video', {'model': FLASH, 'prompt': 'crab', 'duration': 5,
                    'resolution': '480p', **extra})
        with patch.object(transport, 'send', return_value=ok({'uniqId': 'asset1', 'step': 'FAILED',
                'url': 'https://example.invalid/a.png'})):
            self.assertFalse(real_human.get_asset('asset1', model=FLASH)['ready'])
            self.assertTrue(real_human.get_asset('asset1', model=IMAGE)['ready'])

    def test_seedream55_size_format_and_reference_combinations(self):
        base = {'model': IMAGE, 'prompt': 'crab'}
        for size in ('1K', '1.5K', '2K', '960x960', '1025x1025', '2150x2150', '3840x240'):
            caps.normalize_generation_request('image', dict(base, size=size))
        for extra in ({'size': '4K'}, {'size': '959x960'}, {'size': '2151x2151'},
                      {'size': '3841x239'}, {'outputFormat': 'webp'}, {'n': 2},
                      {'imageAssetIds': ['a'], 'imageUrls': ['https://example.invalid/a']},
                      {'rgba': True}):
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                caps.normalize_generation_request('image', dict(base, **extra))
        request = {'model': IMAGE, 'size': '1.5K'}
        with patch.object(transport, 'send', return_value=ok({'frozenCredit': 3})) as send:
            generation.estimate_generation('image', request)
            self.assertNotIn('prompt', send.call_args.kwargs['payload'])
        with self.assertRaises(SystemExit): caps.normalize_generation_request('image', request)

    def test_final_4k_credit_binding_and_request_hash(self):
        request = {'sourceDraftTaskUniqId': 'draft1', 'resolution': '4k'}
        self.assertNotEqual(generation.canonical_request_hash('video', request),
            generation.canonical_request_hash('video', dict(request, resolution='1080p')))
        with patch.object(transport, 'send', side_effect=[ok({'uniqId': 'draft1'}),
                ok({'uniqId': 'draft1'}), ok({'frozenCredit': 12}),
                ok({'uniqId': 'final1', 'step': 0, 'resolution': '4k'})]) as send:
            outcome = generation.create_generation('video', request, confirmed=True, approved_credits=12)
            self.assertEqual(outcome.outcome, 'success')
            self.assertEqual(send.call_args.kwargs['payload'], dict(request, expectedCredit=12))
        with patch.object(transport, 'send', side_effect=[ok({'uniqId': 'draft1'}),
                ok({'uniqId': 'draft1'}), ok({'frozenCredit': 13})]) as send:
            with self.assertRaises(ValueError):
                generation.create_generation('video', request, confirmed=True, approved_credits=12)
            self.assertEqual(send.call_count, 3)

    def test_existing_final_resolution_conflict_blocks_quote_and_create(self):
        for operation in ('estimate', 'create'):
            for actual in ('1080p', None):
                with self.subTest(operation=operation, actual=actual), patch.object(transport, 'send', side_effect=[
                    ok({'uniqId': 'draft1', 'draftHdTaskUniqId': 'final1'}),
                    ok({'uniqId': 'final1', 'step': 2, 'resolution': actual})]) as send:
                    request = {'sourceDraftTaskUniqId': 'draft1', 'resolution': '4k'}
                    with self.assertRaises(results.ApiError) as caught:
                        if operation == 'estimate': generation.estimate_generation('video', request)
                        else: generation.create_generation('video', request, confirmed=True)
                    self.assertEqual(caught.exception.result['taskId'], 'final1')
                    self.assertEqual(caught.exception.result['errorCode'], 'DRAFT_FINAL_RESOLUTION_MISMATCH')
                    self.assertTrue(all(c.args[0] == 'GET' for c in send.call_args_list))

    def test_same_resolution_reuses_without_writes_even_when_expired(self):
        with patch.object(transport, 'send', side_effect=[ok({'uniqId': 'draft1',
                'draftHdTaskUniqId': 'final1', 'draftExpiresAt': '2000-01-01T00:00:00Z'}),
                ok({'uniqId': 'final1', 'step': 2, 'resolution': '4k'})]) as send:
            result = generation.create_generation('video', {'sourceDraftTaskUniqId': 'draft1',
                'resolution': '4k'}, confirmed=True)
            self.assertTrue(result['reused'])
            self.assertEqual(send.call_count, 2)

    def test_concurrent_final_conflict_preserves_created_attempt_and_task(self):
        request = {'sourceDraftTaskUniqId': 'draft1', 'resolution': '4k'}
        with patch.object(transport, 'send', side_effect=[ok({'uniqId': 'draft1'}),
                ok({'uniqId': 'draft1'}), ok({'frozenCredit': 12}),
                ok({'uniqId': 'final1', 'step': 1, 'resolution': '1080p'})]) as send:
            result = generation.create_generation('video', request, confirmed=True,
                approved_credits=12, attempt_id='concurrent')
            self.assertEqual(result.outcome, 'failed')
            self.assertEqual(result['state'], 'created')
            self.assertEqual(result['taskId'], 'final1')
            self.assertEqual(result['actualResolution'], '1080p')
            self.assertEqual(state.attempt_records()[0]['taskId'], 'final1')
            self.assertEqual(send.call_count, 4)

    def test_discovery_has_24_models_without_deferred_features(self):
        models = caps.all_models()
        self.assertEqual(len(models), 25)
        self.assertEqual({FLASH, IMAGE} & {m['id'] for m in models}, {FLASH, IMAGE})
        self.assertNotIn('seed-audio-1.5', {m['id'] for m in models})

    def test_backend_material_count_rejection_stops_flash_before_creation(self):
        # Metadata describes each model's boundary; the backend owns material
        # counts, combinations and file validation, including an edit source.
        for field, limit in [('imageAssetIds', 10), ('videoAssetIds', 5), ('audioAssetIds', 5)]:
            request = {'model': FLASH, 'prompt': 'crab', 'duration': 5, 'resolution': '720p',
                'videoTaskType': 'reference', field: ['asset' + str(i) for i in range(limit + 1)]}
            with self.subTest(field=field), patch.object(transport, 'send', return_value=(400,
                    {'code': 400, 'errorCode': 'INVALID_GENERATION_CONFIG', 'message': 'Too many references'})) as send:
                with self.assertRaises(results.ApiError):
                    generation.create_generation('video', request, confirmed=True)
                self.assertEqual(send.call_count, 1)
                self.assertTrue(send.call_args.args[1].endswith('/freeze-credit'))

    def test_workflow_stops_on_concurrent_resolution_conflict_and_keeps_task(self):
        request = {'sourceDraftTaskUniqId': 'draft1', 'resolution': '4k'}
        key = workflows.prepare({'title': '4K final', 'steps': [
            {'id': 'final', 'kind': 'video', 'request': request}]})['workflowId']
        with patch.object(generation, 'estimate_generation', return_value={'estimate': {'frozenCredit': 12}}):
            quote = workflows.estimate(key)
        with patch.object(transport, 'send', side_effect=[ok({'uniqId': 'draft1'}),
                ok({'uniqId': 'draft1'}), ok({'frozenCredit': 12}),
                ok({'uniqId': 'final1', 'step': 1, 'resolution': '1080p'})]):
            outcome = workflows.run_next(key, approval_id=quote['approvalId'], confirmed=True)
        self.assertEqual(outcome.outcome, 'failed')
        self.assertEqual(outcome['steps'][0]['state'], 'failed')
        self.assertEqual(outcome['steps'][0]['taskId'], 'final1')
        with patch.object(transport, 'send') as send:
            self.assertEqual(workflows.get(key, refresh=True)['steps'][0]['state'], 'failed')
            send.assert_not_called()
        # Simulate interruption after the attempt was saved but before the
        # workflow's final save. Reconciliation must not accept a 1080p result.
        interrupted = workflows.load(key)
        row = interrupted['steps']['final']
        row['state'] = 'running'
        row.pop('taskId')
        row.pop('result')
        workflows.save(interrupted)
        with patch.object(transport, 'send', return_value=ok({'uniqId': 'final1', 'step': 2,
                'resolution': '1080p'})):
            resumed = workflows.get(key, refresh=True)
        self.assertEqual(resumed['steps'][0]['state'], 'failed')
        self.assertEqual(resumed['steps'][0]['result']['actualResolution'], '1080p')


if __name__ == '__main__':
    unittest.main()
