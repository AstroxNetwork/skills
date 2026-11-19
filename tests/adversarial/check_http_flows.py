"""Installed subprocesses + actual HTTP bytes; no high-level send() replacement."""
from __future__ import annotations
import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from tests.adversarial.http_fixture import ROOT, Service, environment


# Independent protocol vectors: do not compute expectations using runtime schemas/routes.
VECTORS = []
for model in ('seedance-2-0', 'seedance-2-0-fast', 'seedance-2-0-mini', 'dreamina-seedance-2-5-260628', 'dreamina-seedance-2-5-flash-261115'):
    request = {'model': model, 'prompt': '中文 🦀', 'duration': 5, 'resolution': '720p'}
    VECTORS.append(('video', request, '/api/tasks/generation', {**request, 'generateAudio': True}))
for model in ('MiniMax-H3', 'MiniMax-H3-Max', 'MiniMax-H3-Highspeed', 'MiniMax-H3-Lite'):
    request = {'model': model, 'prompt': '中文 🦀', 'duration': 5, 'resolution': '768P', 'ratio': '16:9'}
    VECTORS.append(('video', request, '/api/tasks/minimax-generation', request))
for kind, model, fields in (('video', 'agnes-video-2.5', {'size': '720P', 'seconds': 4, 'ratio': '16:9'}),
                            ('image', 'agnes-image-2.5-flash', {'size': '1K', 'ratio': '1:1'})):
    request = {'model': model, 'prompt': '中文 🦀', **fields}
    VECTORS.append((kind, request, '/api/tasks/agnes-generation', request))
WAN_MODELS = {
    'berry-1.0': 'wan3-stand',
    'berry-1.0-turbo': 'wan3-prime',
    'berry-1.0-pro': 'wan3-stand-pro',
    'berry-1.0-pro-turbo': 'wan3-prime-pro',
}
WAN_DEFAULTS = {'duration': 5, 'resolution': '1080p', 'ratio': 'adaptive', 'audio': True}
for model in WAN_MODELS.values():
    request = {'model': model, 'prompt': '中文 🦀'}
    VECTORS.append(('video', request, '/api/tasks/wan',
                    {**request, **WAN_DEFAULTS}))
for model in ('seedream-5-0-pro-260628', 'seedream-5-0-lite-260128', 'seedream-4-5-251128'):
    request = {'model': model, 'prompt': '中文 🦀', 'imageAssetIds': ['asset1'], 'imageUrls': ['https://example.invalid/image']}
    VECTORS.append(('image', request, '/api/tasks/image-generation', request))
VECTORS.append(('audio', {'textPrompt': '中文 🦀', 'references': [{'assetId': 'asset1', 'type': 'audio'}]},
                '/api/tasks/audio-generation', {'textPrompt': '中文 🦀', 'references': [{'assetId': 'asset1', 'type': 'audio'}]}))

for model in ('kling-4.0', 'kling-4.0-flash'):
    request = {'model':model, 'prompt':'中文 🦀', 'duration':5, 'resolution':'720p', 'ratio':'16:9'}
    VECTORS.append(('video', request, '/api/tasks/kling-generation', request))
for model in ('MiniMax-H3-Image', 'MiniMax-H3-Image-Highspeed'):
    request = {'model':model, 'prompt':'中文 🦀', 'size':'auto', 'resolution':'2k'}
    VECTORS.append(('image', request, '/api/tasks/image-generation', {**request,'n':1,'outputFormat':'jpeg'}))


VECTORS.append(('image', {'model':'dola-seedream-5-5-261028','prompt':'中文 🦀','size':'1.5k'}, '/api/tasks/image-generation', {'model':'dola-seedream-5-5-261028','prompt':'中文 🦀','size':'1.5K','outputFormat':'png'}))


KLING_IMAGE = {'model': 'kling-image-4.0', 'prompt': '中文 🦀'}
KLING_DEFAULTS = {'resultType': 'single', 'n': 1, 'resolution': '2k', 'aspectRatio': 'auto'}
VECTORS.append(('image', KLING_IMAGE, '/api/tasks/image-generation', {**KLING_IMAGE, **KLING_DEFAULTS}))


class HttpWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.install_temp = tempfile.TemporaryDirectory(prefix='holycrab-wire-install-')
        cls.addClassCleanup(cls.install_temp.cleanup)
        root = Path(cls.install_temp.name)
        prefix = root / 'prefix'
        env = {k: v for k, v in os.environ.items() if not k.startswith(('HOLYCRAB_', 'CODEX_', 'CLAUDE_'))}
        if os.name == 'nt':
            # PS7 -> Python -> powershell.exe otherwise preserves incompatible
            # PS7 system modules; let Windows PowerShell construct its own path.
            env = {key: value for key, value in env.items() if key.upper() != 'PSMODULEPATH'}
        env.update({'HOME': str(root / 'home'), 'HOLYCRAB_INSTALL_SOURCE_DIR': str(ROOT),
                    'HOLYCRAB_INSTALL_PREFIX': str(prefix), 'HOLYCRAB_CONFIG_DIR': str(root / 'config'),
                    'HOLYCRAB_INSTALL_AGENTS': 'none', 'HOLYCRAB_INSTALL_MCP': '0', 'HOLYCRAB_NO_UPDATE_CHECK': '1'})
        (root / 'home').mkdir()
        command = (['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'install.ps1')]
                   if os.name == 'nt' else ['sh', str(ROOT / 'install.sh')])
        completed = subprocess.run(command, env=env, text=True, encoding='utf-8', capture_output=True, timeout=90)
        if completed.returncode:
            raise AssertionError('Isolated installation failed: ' + completed.stdout + completed.stderr)
        cls.launcher = prefix / 'bin' / ('holycrab.cmd' if os.name == 'nt' else 'holycrab')
        cls.library = prefix / 'lib/holycrab'
        # Windows PATH lives in the user registry, independently of USERPROFILE.
        # Exercise the owned uninstall so an opt-in local run also restores PATH.
        def uninstall():
            result = subprocess.run([str(cls.launcher), 'uninstall', '--yes'], env=env,
                                    text=True, encoding='utf-8', capture_output=True, timeout=45)
            if result.returncode:
                raise AssertionError('Fixture uninstall failed: ' + result.stdout + result.stderr)
            deadline = time.monotonic() + 25
            while cls.launcher.exists() or (cls.library / 'installation.json').exists():
                if time.monotonic() >= deadline:
                    raise AssertionError('Fixture uninstall did not finish removing its owned files')
                time.sleep(.05)
        cls.addClassCleanup(uninstall)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='holycrab-wire-case-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.service = Service()
        self.addCleanup(self.service.close)
        self.env = environment(self.directory, self.library, self.service.endpoint)

    def cli(self, *args, input=None):
        return subprocess.run([str(self.launcher), *args], env=self.env, input=input,
                              text=True, encoding='utf-8', capture_output=True, timeout=25)

    def request_file(self, request):
        path = self.directory / '请求 🦀.json'
        path.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
        return '@' + str(path)

    def mcp(self, name, arguments, *, error=False):
        message = {'jsonrpc': '2.0', 'id': 42, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}}
        result = self.cli('mcp', 'serve', input=json.dumps(message, ensure_ascii=False) + '\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1, result.stdout)
        response = json.loads(lines[0])
        self.assertEqual(response['id'], 42)
        self.assertEqual(response['result']['isError'], error, response)
        self.assertNotIn('fixture-api-key', result.stdout + result.stderr)
        return response['result'].get('structuredContent', {})

    def test_all_models_real_http_estimate_create_query_download(self):
        self.assertEqual(len(VECTORS), 25)
        for index, (kind, request, route, normalized) in enumerate(VECTORS):
            with self.subTest(model=request.get('model', 'seed-audio-1.0')):
                self.service.calls.clear()
                result = self.cli('generate', 'create', '--kind', kind, '--json', self.request_file(request),
                                  '--yes', '--attempt-id', 'model' + str(index))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)['taskId'], 'task1')
                self.assertEqual([(c['method'], c['path'], c['body']) for c in self.service.calls],
                                 [('POST', route + '/freeze-credit', normalized), ('POST', route, normalized)])
                self.assertTrue(all(c['token'] == 'fixture-api-key' for c in self.service.calls))
                queried = self.cli('tasks', 'get', 'task1')
                self.assertEqual(queried.returncode, 0, queried.stderr)
                self.assertIn('task1', queried.stdout)
                output = self.directory / ('output' + str(index) + '.mp4')
                downloaded = self.cli('download', 'task1', '--output', str(output))
                self.assertEqual(downloaded.returncode, 0, downloaded.stderr)
                self.assertEqual(output.read_bytes(), b'complete video bytes')
                self.assertIsNone(self.service.calls[-1]['token'], 'API credential must not be sent to output storage')

    def test_wan_legacy_model_ids_canonicalize_in_cli_and_mcp(self):
        self.service.task_response = {'uniqId': 'task1', 'step': 2, 'model': 'berry-1.0',
                                      'videoUrl': self.service.endpoint + '/output/video'}
        for index, (old, canonical) in enumerate(WAN_MODELS.items()):
            with self.subTest(model=old):
                self.service.calls.clear()
                alias = ' ' + old.upper() + ' '
                result = self.cli('models', 'show', alias)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)['id'], canonical)
                self.assertEqual(self.mcp('capability_get', {'model': old})['id'], canonical)
                self.assertEqual(self.service.calls, [])
                request = {'model': alias, 'prompt': 'crab', 'audio': False}
                normalized = {**WAN_DEFAULTS, **request, 'model': canonical}
                result = self.cli('generate', 'estimate', '--kind', 'video', '--json', self.request_file(request))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                generated = self.mcp('generation_create', {'kind': 'video', 'request': request,
                    'confirmed': True, 'approvedCredits': 7, 'attemptId': 'wanalias' + str(index)})
                self.assertEqual(generated['taskId'], 'task1')
                self.assertEqual([(call['method'], call['path'], call['body']) for call in self.service.calls], [
                    ('POST', '/api/tasks/wan/freeze-credit', normalized),
                    ('POST', '/api/tasks/wan/freeze-credit', normalized),
                    ('POST', '/api/tasks/wan', normalized),
                ])
        result = self.mcp('generation_get', {'taskId': 'task1'})
        self.assertEqual(result['videoUrl'], self.service.task_response['videoUrl'])
        self.assertEqual(self.service.calls[-1]['path'], '/api/tasks/task1')

    def test_legacy_wan_workflow_requotes_and_preserves_submitted_tasks(self):
        old = {'model': 'berry-1.0-pro-turbo', 'prompt': 'crab'}
        plan = {'title': 'From v0.7.4', 'steps': [
            {'id': 'already', 'kind': 'video', 'request': old},
            {'id': 'next', 'kind': 'video', 'request': old, 'dependsOn': ['already']},
        ]}
        workflow = self.mcp('workflow_prepare', {'plan': plan})['workflowId']
        path = self.directory / 'config' / 'workflows' / (workflow + '.json')
        record = json.loads(path.read_text(encoding='utf-8'))
        record['steps']['already'].update(state='submitted', taskId='task1')
        old_request = {**old, **WAN_DEFAULTS}
        old_hash = hashlib.sha256(json.dumps(old_request, sort_keys=True, ensure_ascii=False,
                                             separators=(',', ':')).encode()).hexdigest()
        record['approval'] = {
            'approvalId': 'oldQuote', 'revision': 1, 'createdAt': time.time(), 'consumed': [],
            'entries': [{'stepId': 'next', 'kind': 'video', 'request': old_request,
                         'requestHash': old_hash, 'credits': 7}],
        }
        path.write_text(json.dumps(record), encoding='utf-8')
        refreshed = self.mcp('workflow_get', {'workflowId': workflow, 'refresh': True})
        self.assertEqual(refreshed['steps'][0]['outputs'], {'taskId': 'task1'})
        self.assertEqual([(c['method'], c['path']) for c in self.service.calls], [('GET', '/api/tasks/task1')])
        self.service.calls.clear()
        self.mcp('workflow_run_next', {'workflowId': workflow, 'approvalId': 'oldQuote', 'confirmed': True}, error=True)
        self.assertEqual(self.service.calls, [])
        fresh = self.mcp('workflow_estimate', {'workflowId': workflow})
        self.assertNotEqual(fresh['approvalId'], 'oldQuote')
        self.assertEqual(len(fresh['steps']), 1)
        canonical = {**old_request, 'model': 'wan3-prime-pro'}
        self.assertEqual(fresh['steps'][0]['request'], canonical)
        self.service.created_task_id = 'next1'
        executed = self.mcp('workflow_run_next', {'workflowId': workflow,
            'approvalId': fresh['approvalId'], 'confirmed': True})
        self.assertEqual(executed['steps'][0]['outputs'], {'taskId': 'task1'})
        self.assertEqual(executed['steps'][1]['taskId'], 'next1')
        self.assertEqual([(c['method'], c['path'], c['body']) for c in self.service.calls], [
            ('POST', '/api/tasks/wan/freeze-credit', canonical),
            ('POST', '/api/tasks/wan/freeze-credit', canonical),
            ('POST', '/api/tasks/wan', canonical),
        ])

    def test_kling_image_quotes_optional_prompt_and_normalizes_series(self):
        for index, amount in enumerate((None, '2', '10', 'auto')):
            with self.subTest(amount=amount):
                self.service.calls.clear()
                request = {'model': 'kling-image-4.0', 'resultType': 'series', 'resolution': '8k',
                           'aspectRatio': '21:9'}
                defaults = {**request, 'seriesAmount': '4' if amount is None else amount}
                if amount is not None:
                    request['seriesAmount'] = amount
                quote = self.mcp('generation_estimate', {'kind': 'image', 'request': request})
                self.assertEqual(quote['estimate'], {'frozenCredit': 7})
                self.assertEqual([(c['method'], c['path'], c['body']) for c in self.service.calls],
                                 [('POST', '/api/tasks/image-generation/freeze-credit', defaults)])
                self.mcp('generation_create', {'kind': 'image', 'request': request, 'confirmed': True,
                    'attemptId': 'noPrompt' + str(index)}, error=True)
                self.assertEqual(len(self.service.calls), 1)
                request = {**request, 'prompt': KLING_IMAGE['prompt'],
                           'references': [{'type': 'refer_image', 'assetId': 'asset1', 'id': 'cat_ref'}]}
                generated = self.mcp('generation_create', {'kind': 'image', 'request': request, 'confirmed': True,
                    'approvedCredits': 7, 'attemptId': 'klingSeries' + str(index)})
                self.assertEqual(generated['taskId'], 'task1')
                self.assertEqual([(c['method'], c['path'], c['body']) for c in self.service.calls[1:]], [
                    ('POST', '/api/tasks/image-generation/freeze-credit', {**defaults, **request}),
                    ('POST', '/api/tasks/image-generation', {**defaults, **request}),
                ])

    def test_kling_image_validation_and_requote_avoid_billable_posts(self):
        reference = {'type': 'refer_image', 'assetId': 'asset1', 'id': 'cat'}
        invalid = (
            {'n': True}, {'n': 10}, {'seriesAmount': '2'}, {'resultType': 'series', 'n': 1},
            {'resultType': 'series', 'seriesAmount': 2}, {'resultType': 'series', 'seriesAmount': '11'},
            {'prompt': 'a' * 50001}, {'prompt': '😀' * 21001}, {'prompt': '   '}, {'resolution': '1k'},
            {'references': [{**reference, 'type': 'element'}]}, {'references': [{**reference, 'id': '1cat'}]},
            {'references': [{**reference, 'assetId': '  '}]}, {'references': [reference, reference]},
            {'references': [{**reference, 'url': 'https://example.invalid/private.png'}]},
        )
        for index, fields in enumerate(invalid):
            with self.subTest(fields=list(fields)):
                self.mcp('generation_create', {'kind': 'image', 'request': {**KLING_IMAGE, **fields},
                    'confirmed': True, 'attemptId': 'invalidKling' + str(index)}, error=True)
        self.assertEqual(self.service.calls, [])
        # Boundary prompts exceed the video-model limits and still reach the image quote endpoint.
        for prompt in ('a' * 50000, '😀' * 21000, 'a' * 40000 + '中' * 4200):
            quote = self.mcp('generation_estimate', {'kind': 'image', 'request': {**KLING_IMAGE, 'prompt': prompt}})
            self.assertEqual(quote['estimate']['frozenCredit'], 7)
        self.service.calls.clear()
        self.service.quote_credits = 9
        result = self.cli('generate', 'create', '--kind', 'image', '--json', self.request_file(KLING_IMAGE),
                          '--yes', '--approved-credits', '7', '--attempt-id', 'changedQuote')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('estimate changed', (result.stdout + result.stderr).lower())
        self.assertEqual([(c['method'], c['path']) for c in self.service.calls],
                         [('POST', '/api/tasks/image-generation/freeze-credit')])
        self.assertFalse((self.directory / 'config' / 'attempts.json').exists())

    def test_kling_image_sends_reference_collections_for_backend_validation(self):
        refs = [{'type': 'refer_image', 'assetId': 'asset' + str(i), 'id': 'ref' + str(i)} for i in range(11)]
        request = {**KLING_IMAGE, 'n': 9, 'references': refs[:10]}
        quote = self.mcp('generation_estimate', {'kind': 'image', 'request': request})
        self.assertEqual(quote['estimate']['frozenCredit'], 7)
        self.assertEqual(self.service.calls[-1]['body']['references'], refs[:10])
        self.service.calls.clear()
        self.service.mode = 'reject'
        rejection = self.mcp('generation_create', {'kind': 'image', 'request': {**request, 'references': refs},
            'confirmed': True, 'attemptId': 'tooManyKlingRefs'}, error=True)
        self.assertEqual(rejection['errorCode'], 'ASSET_RULE')
        self.assertEqual([(c['method'], c['path'], c['body']['references']) for c in self.service.calls],
                         [('POST', '/api/tasks/image-generation/freeze-credit', refs)])

    def test_kling_image_download_preserves_backend_output_order(self):
        self.service.task_response = {'uniqId': 'task1', 'step': 2, 'model': 'kling-image-4.0',
            'taskType': 'IMAGE', 'imageUrls': [self.service.endpoint + '/output/image-second',
                                               self.service.endpoint + '/output/image-first']}
        for index, name in enumerate(('image-second', 'image-first')):
            destination = self.directory / (name + '.png')
            result = self.cli('download', 'task1', '--index', str(index), '--output', str(destination))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(destination.read_bytes(), ('/output/' + name).encode())
            self.assertEqual(self.service.calls[-2]['path'], '/api/tasks/task1')
            self.assertIsNone(self.service.calls[-1]['token'])

    def test_4k_draft_final_and_conflicts_across_cli_and_mcp(self):
        request = {'sourceDraftTaskUniqId': 'draft1', 'resolution': '4k'}
        result = self.cli('generate', 'create', '--kind', 'video', '--json', self.request_file(request),
            '--yes', '--approved-credits', '7', '--attempt-id', 'final4k')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['response']['resolution'], '4k')
        creates = [c for c in self.service.calls if c['path'] == '/api/tasks/generation']
        self.assertEqual([c['body'] for c in creates], [dict(request, expectedCredit=7)])
        self.service.calls.clear()
        self.service.linked_final_resolution = '1080p'
        conflict = self.mcp('generation_estimate', {'kind': 'video', 'request': request}, error=True)
        self.assertEqual(conflict['errorCode'], 'DRAFT_FINAL_RESOLUTION_MISMATCH')
        self.assertEqual(conflict['taskId'], 'final1')
        self.assertTrue(all(c['method'] == 'GET' for c in self.service.calls))
        self.service.calls.clear()
        self.service.linked_final_resolution = None
        self.service.final_resolution_override = '1080p'
        conflict = self.mcp('generation_create', {'kind': 'video', 'request': request,
            'confirmed': True, 'approvedCredits': 7, 'attemptId': 'concurrent4k'}, error=True)
        self.assertEqual(conflict['state'], 'created')
        self.assertEqual(conflict['taskId'], 'task1')
        self.assertEqual(conflict['actualResolution'], '1080p')
        self.assertEqual(sum(c['path'] == '/api/tasks/generation' for c in self.service.calls), 1)

    def test_normal_4k_transports_exact_resolution(self):
        request = {'model':'dreamina-seedance-2-5-260628','prompt':'crab','duration':5,'resolution':'4k'}
        self.mcp('generation_create', {'kind':'video','request':request,'confirmed':True,
            'approvedCredits':7,'attemptId':'normal4k'})
        self.assertEqual([c['body'] for c in self.service.calls], [dict(request,generateAudio=True)] * 2)

    def test_refused_estimate_exits_nonzero_and_never_creates(self):
        self.service.mode = 'reject'
        for index, (kind, request, route, _) in enumerate(VECTORS):
            with self.subTest(model=request.get('model')):
                self.service.calls.clear()
                result = self.cli('generate', 'create', '--kind', kind, '--json', self.request_file(request),
                                  '--yes', '--attempt-id', 'reject' + str(index))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout)['errorCode'], 'ASSET_RULE')
                self.assertEqual(len(self.service.calls), 1)
                self.assertEqual(self.service.calls[0]['path'], route + '/freeze-credit')

    def test_disconnected_submit_is_unknown_and_cannot_be_submitted_again(self):
        self.service.mode = 'disconnect'
        request = self.request_file({'model': 'berry-1.0', 'prompt': 'fixture'})
        argv = ('generate', 'create', '--kind', 'video', '--json', request, '--yes', '--attempt-id', 'ambiguous')
        result = self.cli(*argv)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['state'], 'unknown')
        again = self.cli(*argv)
        self.assertNotEqual(again.returncode, 0)
        self.assertEqual(len(self.service.calls), 2)
        ledger = json.loads((self.directory / 'config/attempts.json').read_text())
        self.assertEqual(ledger['ambiguous']['state'], 'unknown')

    def test_real_socket_timeout_is_unknown_and_never_retried(self):
        # Shorten only the test socket deadline; urllib and HTTP remain real.
        self.env['HOLYCRAB_TEST_HTTP_TIMEOUT'] = '0.25'
        self.service.mode = 'slow-create'
        argv = ('generate', 'create', '--kind', 'video', '--json',
                self.request_file({'model': 'berry-1.0', 'prompt': 'fixture'}), '--yes', '--attempt-id', 'slow')
        result = self.cli(*argv)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['state'], 'unknown')
        self.assertNotEqual(self.cli(*argv).returncode, 0)
        self.assertEqual(len(self.service.calls), 2)

    def test_invalid_json_estimate_never_reserves_or_creates(self):
        self.service.mode = 'invalid-json'
        result = self.cli('generate', 'create', '--kind', 'video', '--json',
                          self.request_file({'model': 'berry-1.0', 'prompt': 'fixture'}), '--yes', '--attempt-id', 'invalid')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse((self.directory / 'config/attempts.json').exists())
        self.assertEqual(len(self.service.calls), 1)

    def test_two_processes_same_attempt_only_create_one_task(self):
        self.service.barrier = threading.Barrier(2)
        request = self.request_file({'model': 'berry-1.0', 'prompt': 'fixture'})
        argv = ('generate', 'create', '--kind', 'video', '--json', request, '--yes', '--attempt-id', 'concurrent')
        with concurrent.futures.ThreadPoolExecutor(2) as executor:
            results = list(executor.map(lambda _: self.cli(*argv), range(2)))
        self.assertEqual(sorted(r.returncode == 0 for r in results), [False, True])
        self.assertEqual(sum(c['path'] == '/api/tasks/wan' for c in self.service.calls), 1)

    def prepare_upload(self):
        image = self.directory / 'concurrent.jpg'
        image.write_bytes(b'\xff\xd8\xff\xe0local fixture')
        return self.mcp('asset_upload_prepare', {'files': [str(image)]})['uploadPlanId']

    def test_concurrent_upload_plan_sends_only_one_file(self):
        plan = self.prepare_upload()
        args = {'uploadPlanId': plan, 'confirmed': True}
        self.service.mode = 'upload-block'
        with concurrent.futures.ThreadPoolExecutor(1) as executor:
            future = executor.submit(self.mcp, 'asset_upload_execute', args)
            self.assertTrue(self.service.entered.wait(10))
            try:
                self.mcp('asset_upload_execute', args, error=True)
            finally:
                self.service.release.set()
            self.assertEqual(future.result()['uploaded'][0]['assetUniqId'], 'asset1')
        self.assertEqual(sum(c['method'] == 'PUT' for c in self.service.calls), 1)
        self.assertEqual(sum(c['path'] == '/api/user-assets/upload' for c in self.service.calls), 1)

    def test_ambiguous_registration_preserves_asset_id_and_never_reuploads(self):
        plan = self.prepare_upload()
        args = {'uploadPlanId': plan, 'confirmed': True}
        self.service.mode = 'register-disconnect'
        value = self.mcp('asset_upload_execute', args, error=True)
        self.assertEqual(value['failedOrUnknown']['state'], 'unknown')
        self.assertEqual(value['failedOrUnknown']['assetUniqId'], 'asset1')
        self.mcp('asset_upload_execute', args, error=True)
        self.assertEqual(sum(c['method'] == 'PUT' for c in self.service.calls), 1)
        self.assertEqual(sum(c['path'] == '/api/user-assets/upload' for c in self.service.calls), 1)
        saved = self.mcp('asset_upload_plan_get', {'uploadPlanId': plan})
        self.assertEqual(saved['failedOrUnknown']['assetUniqId'], 'asset1')

    def test_active_submission_prevents_purge_and_keeps_reservation(self):
        self.service.mode = 'block'
        request = self.request_file({'model': 'berry-1.0', 'prompt': 'fixture'})
        with concurrent.futures.ThreadPoolExecutor(1) as executor:
            future = executor.submit(self.cli, 'generate', 'create', '--kind', 'video', '--json', request,
                                     '--yes', '--attempt-id', 'active')
            self.assertTrue(self.service.entered.wait(timeout=10))
            try:
                result = subprocess.run([sys.executable, '-c',
                    'import json; from holycrab_runtime.lifecycle import purge_known_local_state; print(json.dumps(purge_known_local_state()))'],
                    env=self.env, text=True, encoding='utf-8', capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('Refused to purge', result.stdout)
                ledger = json.loads((self.directory / 'config/attempts.json').read_text())
                self.assertIn('active', ledger)
            finally:
                self.service.release.set()
            self.assertNotEqual(future.result().returncode, 0)

    def test_partial_and_truncated_http_download_preserve_old_file(self):
        destination = self.directory / 'old.mp4'
        for mode in ('partial', 'truncated'):
            with self.subTest(mode=mode):
                self.service.mode = mode
                destination.write_bytes(b'keep previous file')
                result = self.cli('download', 'task1', '--output', str(destination), '--force')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(destination.read_bytes(), b'keep previous file')
                self.assertEqual(list(self.directory.glob('*.part')), [])

    def test_redirected_http_download_finishes_without_api_token(self):
        self.service.mode = 'redirect'
        result = self.cli('download', 'task1', '--output', str(self.directory / 'redirect.mp4'))
        self.assertEqual(result.returncode, 0, result.stderr)
        outputs = [c for c in self.service.calls if c['path'].startswith('/output')]
        self.assertEqual(len(outputs), 2)
        self.assertTrue(all(c['token'] is None for c in outputs))

    def test_mcp_backend_rejection_preserves_false_zero_and_is_error(self):
        self.service.mode = 'reject'
        value = self.mcp('generation_estimate', {'kind': 'audio', 'request': {'textPrompt': 'fixture'}}, error=True)
        self.assertEqual(value['errorCode'], 'ASSET_RULE')
        self.assertEqual(value['requestId'], 'request1')
        self.assertEqual(value['errors'][0]['actual'], 0)
        self.assertIs(value['errors'][0]['retryable'], False)

    def test_twenty_two_tools_have_concrete_results_and_remote_rejections(self):
        image = self.directory / '素材.jpg'
        image.write_bytes(b'\xff\xd8\xff\xe0local fixture')
        plan = self.mcp('asset_upload_prepare', {'files': [str(image)]})
        plan_id = plan['uploadPlanId']
        self.assertTrue(plan['confirmationRequired'])
        self.assertEqual(plan['files'][0]['size'], image.stat().st_size)
        request = {'model': 'berry-1.0', 'prompt': 'fixture'}
        # Expected fields and HTTP methods are independent of tool implementation.
        rows = [
            ('cli_status', {}, 'runtimeOk', True, None),
            ('account_get', {}, 'credit', 100, ('GET', '/api/user/me')),
            ('capabilities_list', {}, 'models', None, None),
            ('capability_get', {'model': 'berry-1.0'}, 'id', 'wan3-stand', None),
            ('generation_estimate', {'kind': 'video', 'request': request}, 'estimate', {'frozenCredit': 7}, ('POST', '/api/tasks/wan/freeze-credit')),
            ('generation_create', {'kind': 'video', 'request': request, 'confirmed': True, 'attemptId': 'tools'}, 'taskId', 'task1', ('POST', '/api/tasks/wan')),
            ('generation_get', {'taskId': 'task1'}, 'uniqId', 'task1', ('GET', '/api/tasks/task1')),
            ('generation_list', {'page': 1, 'pageSize': 20}, 'total', 1, ('GET', '/api/tasks')),
            ('generation_attempt_list', {}, 'attempts', None, None),
            ('generation_attempt_get', {'attemptId': 'tools'}, 'state', 'created', None),
            ('real_human_authorization_start', {'name': 'Fixture'}, 'authorizationId', 'auth1', ('POST', '/api/real-human-authorizations/sessions')),
            ('real_human_authorization_get', {'authorizationId': 'auth1'}, 'status', 'SUCCEEDED', ('GET', '/api/real-human-authorizations/auth1')),
            ('real_human_groups_list', {}, 'total', 1, ('GET', '/api/real-human-groups')),
            ('real_human_group_rename', {'groupUniqId': 'group1', 'name': 'New name'}, 'name', 'New name', ('PATCH', '/api/real-human-groups/group1')),
            ('real_human_group_delete', {'groupUniqId': 'group1', 'confirmed': True}, 'deleted', True, ('DELETE', '/api/real-human-groups/group1')),
            ('real_human_assets_list', {'groupUniqId': 'group1'}, 'total', 1, ('GET', '/api/real-human-groups/group1/assets')),
            ('real_human_asset_delete', {'groupUniqId': 'group1', 'assetId': 'asset1', 'confirmed': True}, 'deleted', True, ('DELETE', '/api/real-human-groups/group1/assets/asset1')),
            ('asset_get', {'assetId': 'asset1'}, 'metadataStatus', 'PENDING', ('GET', '/api/user-assets/asset1')),
            ('asset_metadata_retry', {'assetId': 'asset1'}, 'metadataStatus', 'PENDING', ('POST', '/api/user-assets/asset1/metadata/retry')),
            ('asset_upload_execute', {'uploadPlanId': plan_id, 'confirmed': True}, 'uploaded', None, ('POST', '/api/user-assets/upload')),
            ('asset_upload_plan_get', {'uploadPlanId': plan_id}, 'uploadPlanId', plan_id, None),
        ]
        self.assertEqual(len(rows) + 1, 22)
        for name, args, field, expected, remote in rows:
            with self.subTest(tool=name):
                self.service.calls.clear()
                value = self.mcp(name, args)
                self.assertIn(field, value, value)
                if expected is not None: self.assertEqual(value[field], expected, value)
                if name == 'generation_attempt_list': self.assertEqual(value[field][0]['attemptId'], 'tools')
                if name == 'capabilities_list': self.assertEqual(len(value[field]), 25)
                if name == 'asset_upload_execute':
                    self.assertEqual(value[field][0]['assetUniqId'], 'asset1')
                    self.assertIsNone(value['failedOrUnknown'])
                    self.assertEqual(sum(c['method'] == 'PUT' for c in self.service.calls), 1)
                if remote:
                    self.assertEqual(sum((c['method'], c['path'].split('?', 1)[0]) == remote for c in self.service.calls), 1)
                else:
                    self.assertEqual(self.service.calls, [])
                self.mcp(name, {'unexpectedField': 'invalid'}, error=True)
        self.service.mode = 'reject'
        for name, args, _, _, remote in rows:
            if remote is None: continue
            if name == 'generation_create': args = {**args, 'attemptId': 'rejectedTools'}
            if name == 'asset_upload_execute':
                replacement = self.mcp('asset_upload_prepare', {'files': [str(image)]})
                args = {**args, 'uploadPlanId': replacement['uploadPlanId']}
            with self.subTest(rejectedTool=name):
                self.service.calls.clear()
                self.mcp(name, args, error=True)
                self.assertEqual(len(self.service.calls), 1, 'No mutating retry after refusal')
