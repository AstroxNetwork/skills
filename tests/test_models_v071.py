"""MiniMax release paths use real runtime code and mocked transport only."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tests.test_cli import holycrab as cli
from holycrab_runtime import workflows
from tools.validate_generate_contract import validate_minimax_v071

MODELS = ('MiniMax-H3-Highspeed', 'MiniMax-H3-Lite')
def request(model):
    return dict(model=model, prompt='A crab walks across a beach', resolution='768P', duration=4, ratio='16:9')
def ok(data):
    return 200, {'code': 200, 'data': data}

class MiniMax071Tests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {'HOLYCRAB_CONFIG_DIR': temp.name, 'HOLYCRAB_NO_UPDATE_CHECK': '1', 'HOLYCRAB_API_KEY': 'mock-only-071'})
        env.start(); self.addCleanup(env.stop)

    def test_release_version_matches_installers_and_runtime_inventory(self):
        root = Path(__file__).resolve().parents[1]
        from holycrab_runtime.settings import VERSION
        self.assertEqual(VERSION, '0.7.1')
        self.assertEqual(json.loads((root/'runtime-files.json').read_text())['version'], VERSION)
        self.assertIn('VERSION=v'+VERSION, (root/'install.sh').read_text())
        self.assertIn('$Version = "v'+VERSION+'"', (root/'install.ps1').read_text())

    def test_discovery_routes_and_parameter_boundaries(self):
        for model in MODELS:
            with self.subTest(model=model):
                self.assertEqual(cli.mcp_tool_call('capability_get', {'model':model})['id'], model)
                self.assertIn(model, {r['id'] for r in cli.mcp_tool_call('capabilities_list', {})['models']})
                self.assertEqual(cli.generation_endpoints('video', request(model)),
                    ('/api/tasks/minimax-generation/freeze-credit', '/api/tasks/minimax-generation'))
                for duration in (4, 15):
                    for resolution in (['768P'] if model.endswith('Highspeed') else ['480P', '768P']):
                        cli.normalize_generation_request('video', {**request(model), 'duration':duration, 'resolution':resolution})
                for changes in ({'duration':3}, {'duration':16}, {'duration':4.5}, {'duration':True},
                                {'resolution':'2K'}, {'generateAudio':True}, {'prompt':'a'*7001}, {'prompt':'😀'*3501}):
                    with self.assertRaises(SystemExit):
                        cli.normalize_generation_request('video', {**request(model), **changes})
                for prompt in ('a'*7000, '😀'*3500):
                    cli.normalize_generation_request('video', {**request(model), 'prompt':prompt})
        with self.assertRaises(SystemExit):
            cli.normalize_generation_request('video', {**request(MODELS[0]), 'resolution':'480P'})

    def test_ratio_defaults_frames_and_lite_reference_mode(self):
        for model in MODELS:
            for ratio in (None, 'adaptive'):
                r=request(model);r.pop('ratio')
                if ratio: r['ratio']=ratio
                with self.assertRaisesRegex(SystemExit, 'concrete ratio'):
                    cli.normalize_generation_request('video', r)
                for frame in ('firstFrameAssetId', 'lastFrameAssetId'):
                    self.assertEqual(cli.normalize_generation_request('video', {**r,frame:'frame'})['ratio'], 'adaptive')
                r['imageAssetIds']=['image']
                if model.endswith('Lite'):
                    with self.assertRaisesRegex(SystemExit, 'concrete ratio'): cli.normalize_generation_request('video', r)
                else:
                    self.assertEqual(cli.normalize_generation_request('video', r)['ratio'], 'adaptive')
            cli.normalize_generation_request('video', {**request(model), 'imageAssetIds':['image']})

    def test_material_limits_remain_backend_owned_and_errors_stop_submission(self):
        for model in MODELS:
            for change in ({'audioAssetIds':['audio']}, {'imageAssetIds':['i'], 'lastFrameAssetId':'f'},
                           {'imageAssetIds':[str(i) for i in range(10)]}):
                r={**request(model), **change}
                # Request shapes reach service validation; no duplicate local material policy.
                cli.normalize_generation_request('video', r)
                with patch.object(cli._owner('send'), 'send', return_value=(400, {'code':400,'message':'invalid material combination'})) as send:
                    with self.assertRaisesRegex(SystemExit, 'invalid material combination'):
                        cli.create_generation('video', r, confirmed=True)
                    self.assertEqual(send.call_count, 1)

    def test_cli_and_mcp_submission_recheck_prices_and_use_native_payload(self):
        for model in MODELS:
            for via_mcp in (False, True):
                r=request(model)
                with patch.object(cli._owner('send'), 'send', side_effect=[ok({'frozenCredit':7}),ok({'uniqId':'task'})]) as send:
                    if via_mcp:
                        result=cli.mcp_tool_call('generation_create', {'kind':'video','request':r,'confirmed':True,
                            'approvedCredits':7,'attemptId':'mcp-'+model})
                    else:
                        result=cli.create_generation('video',r,confirmed=True,approved_credits=7)
                    self.assertEqual(result['taskId'],'task')
                    self.assertEqual([c.kwargs['payload'] for c in send.call_args_list], [r,r])
                    self.assertEqual([c.args[1] for c in send.call_args_list],list(cli.generation_endpoints('video',r)))
            with patch.object(cli._owner('send'), 'send', return_value=ok({'frozenCredit':8})) as send:
                with self.assertRaisesRegex(ValueError, 'estimate changed'): cli.create_generation('video',request(model),confirmed=True,approved_credits=7)
                self.assertEqual(send.call_count,1)

    def test_confirmation_permissions_and_uncertain_submission(self):
        for model in MODELS:
            r=request(model)
            with patch.object(cli._owner('send'), 'send', return_value=ok({'frozenCredit':7})) as send:
                self.assertTrue(cli.create_generation('video',r,confirmed=False)['confirmationRequired'])
                self.assertEqual(send.call_count,1)
            self.assertEqual(cli.public_account_data({'disabled_models':[model]})['disabled_models'],[model])
            with patch.object(cli._owner('send'), 'send', return_value=(403,{'message':'Model disabled'})) as send:
                with self.assertRaisesRegex(SystemExit,'Model disabled'): cli.create_generation('video',r,confirmed=True)
                self.assertEqual(send.call_count,1)
            with patch.object(cli._owner('send'), 'send', side_effect=[ok({'frozenCredit':7}),TimeoutError()]) as send:
                result=cli.create_generation('video',r,confirmed=True,attempt_id='unknown-'+model)
                self.assertEqual(result['state'],'unknown')
                with self.assertRaisesRegex(SystemExit,'already exists'):
                    cli.create_generation('video',r,confirmed=True,attempt_id='unknown-'+model)
                self.assertEqual(send.call_count,2)

    def test_workflow_estimate_create_refresh_and_resume(self):
        for model in MODELS:
            key=workflows.prepare({'title':model,'steps':[{'id':'shot','kind':'video','request':request(model)}]})['workflowId']
            with patch.object(cli._owner('send'), 'send', side_effect=[ok({'frozenCredit':7}),ok({'frozenCredit':7}),ok({'uniqId':'task'})]) as send:
                quote=workflows.estimate(key)
                self.assertEqual(quote['readyCredits'],7)
                workflows.run_next(key,approval_id=quote['approvalId'],confirmed=True)
                self.assertEqual(send.call_count,3)
            with patch.object(cli._owner('send'), 'send',return_value=ok({'step':2})) as send:
                result=workflows.get(key,refresh=True)
                self.assertEqual(result['steps'][0]['outputs']['taskId'],'task')
                self.assertTrue(all(c.args[0]=='GET' for c in send.call_args_list))
            self.assertEqual(workflows.get(key)['steps'][0]['outputs']['taskId'],'task')

class Contract071Tests(unittest.TestCase):
    def test_manifest_mutations_are_detected(self):
        root=Path(__file__).resolve().parents[1]
        manifest=json.loads((root/'holycrab/references/capabilities.json').read_text())
        # Backend source fixture independently encodes the relevant contract.
        source='''Set<String> RATIOS = Set.of("adaptive", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9");
        getPrompt().length() > 7000;
        "MiniMax-H3-Highspeed".equals(model) ? Set.of("768P") : max || lite ? Set.of("480P", "768P");
        getDuration() < (max ? 5 : 4) || request.getDuration() > 15;
        if (frames) ratio = "adaptive";
        !frames && (!references || lite) && "adaptive";
        boolean lite = "MiniMax-H3-Lite".equals(model);'''
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'server/user/src/main/java/com/astrox/seedance/user/service/MinimaxGenerationRequestValidator.java'
            path.parent.mkdir(parents=True);path.write_text(source)
            self.assertEqual(validate_minimax_v071(Path(tmp),manifest),[])
            for field,value in [('duration',{'type':'integer','minimum':3,'maximum':15}),('resolution',{'enum':['2K']}),('prompt',{'type':'string'})]:
                changed=copy.deepcopy(manifest);changed['requestSchemas']['minimaxLiteVideo']['properties'][field]=value
                self.assertTrue(validate_minimax_v071(Path(tmp),changed))
            path.write_text(source.replace('> 15','> 16'))
            self.assertTrue(validate_minimax_v071(Path(tmp),manifest))
