"""Public 0.7.2 requests exercise the runtime with only HTTP replaced."""
import os
import io
import json
from contextlib import redirect_stdout
import tempfile
import unittest
from unittest.mock import patch
from tests.test_cli import holycrab as cli
from holycrab_runtime import capabilities as caps, generation, transport, real_human, workflows

def ok(data): return 200, {'code':200,'data':data}
def kling(model='kling-4.0',**kw):return dict(model=model,prompt='A crab',duration=5,resolution='720p',ratio='16:9',**kw)
def image(model='MiniMax-H3-Image',**kw):return dict(model=model,prompt='A crab',size='auto',resolution='2k',**kw)

class Release072Tests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        env=patch.dict(os.environ,{'HOLYCRAB_CONFIG_DIR':tmp.name,'HOLYCRAB_API_KEY':'mock-072','HOLYCRAB_NO_UPDATE_CHECK':'1'})
        env.start();self.addCleanup(env.stop)

    def test_four_models_route_through_estimate_and_single_submission(self):
        for kind,req,route in [('video',kling(),'kling-generation'),('video',kling('kling-4.0-flash'),'kling-generation'),('image',image(),'image-generation'),('image',image('MiniMax-H3-Image-Highspeed'),'image-generation')]:
            with self.subTest(model=req['model']),patch.object(transport,'send',side_effect=[ok({'frozenCredit':9}),ok({'uniqId':'created','step':1})]) as send:
                result=generation.create_generation(kind,req,confirmed=True,approved_credits=9)
                self.assertEqual(result['taskId'],'created')
                self.assertEqual([c.args[1] for c in send.call_args_list],['/api/tasks/'+route+'/freeze-credit','/api/tasks/'+route])
                self.assertEqual(send.call_args_list[0].kwargs['payload'],send.call_args_list[1].kwargs['payload'])

    def test_kling_boundaries_and_voice_no_extra_endpoint(self):
        refs=[{'type':'voice','assetId':'a','id':'Voice1'}]
        caps.normalize_generation_request('video',kling(references=refs))
        for req in [kling('kling-4.0-flash',references=refs),kling(audio='original'),kling(references=refs*2),kling(references=[{'type':'first_frame','assetId':'a','id':'bad id'}])]:
            with self.assertRaises(SystemExit):caps.normalize_generation_request('video',req)
        for length in [26000]:caps.normalize_generation_request('video',{**kling(),'prompt':'a'*length})
        for prompt in ['a'*26001,'中'*11001,'😀'*11001]:
            with self.assertRaises(SystemExit):caps.normalize_generation_request('video',{**kling(),'prompt':prompt})
        with patch.object(transport,'send',side_effect=[ok({'frozenCredit':4}),ok({'uniqId':'voicevideo','step':1})]) as send:
            generation.create_generation('video',kling(references=refs),confirmed=True)
        self.assertEqual(send.call_count,2)

    def test_minimax_image_quote_create_differences(self):
        req=image();req.pop('prompt')
        with patch.object(transport,'send',return_value=ok({'frozenCredit':8})) as send:
            generation.estimate_generation('image',req)
            self.assertEqual(send.call_args.kwargs['payload']['prompt'],'')
            with self.assertRaises(SystemExit):generation.create_generation('image',req,confirmed=True)
            self.assertEqual(send.call_count,1)
        caps.normalize_generation_request('image',image(n=10,outputFormat='webp'))
        caps.normalize_generation_request('image',{**image(),'size':'1000x1000','area':'2K','aspectRatio':'16:9'})
        for extra in [{'n':11},{'size':'10x10'},{'imageAssetIds':['a'],'imageUrls':['https://example.com/a']},{'imageAssetIds':['a']*17},{'disablePromptEnhanced':True},{'aspectRatio':'4:1'}]:
            with self.assertRaises(SystemExit):caps.normalize_generation_request('image',{**image(),**extra})

    def test_draft_and_final_expected_credit(self):
        draft=dict(model='dreamina-seedance-2-5-260628',prompt='crab',duration=5,resolution='480p',ratio='16:9',draft=True)
        caps.normalize_generation_request('video',draft)
        with self.assertRaises(SystemExit):caps.normalize_generation_request('video',{**draft,'resolution':'720p'})
        req={'sourceDraftTaskUniqId':'draft1','resolution':'1080p'}
        with patch.object(transport,'send',side_effect=[ok({'uniqId':'draft1'}),ok({'uniqId':'draft1'}),ok({'frozenCredit':12}),ok({'uniqId':'final1','step':0,'resolution':'1080p','sourceDraftTaskUniqId':'draft1'})]) as send:
            result=generation.create_generation('video',req,confirmed=True,approved_credits=12)
            self.assertNotIn('expectedCredit',send.call_args_list[2].kwargs['payload'])
            self.assertEqual(send.call_args_list[3].kwargs['payload'],dict(req,expectedCredit=12))
            self.assertEqual(result['response']['sourceDraftTaskUniqId'],'draft1')
        with patch.object(transport,'send',side_effect=[ok({'uniqId':'draft1'}),ok({'uniqId':'draft1'}),ok({'frozenCredit':13})]) as send:
            with self.assertRaises(ValueError):generation.create_generation('video',req,confirmed=True,approved_credits=12)
            self.assertEqual(send.call_count,3)
        with self.assertRaises(SystemExit):caps.normalize_generation_request('video',dict(req,model='dreamina-seedance-2-5-260628'))

    def test_existing_final_is_reused_even_when_draft_expired(self):
        req={'sourceDraftTaskUniqId':'draft1','resolution':'1080p'}
        with patch.object(transport,'send',side_effect=[ok({'uniqId':'draft1','draftHdTaskUniqId':'final1','draftExpiresAt':'2000-01-01T00:00:00Z'}),ok({'uniqId':'final1','step':2,'resolution':'1080p'})]) as send:
            result=generation.create_generation('video',req,confirmed=True)
            self.assertTrue(result['reused'])
            self.assertTrue(all(c.args[0]=='GET' for c in send.call_args_list))

    def test_model_specific_ark_review_and_deleted_assets(self):
        asset={'uniqId':'asset1','step':'FAILED','url':'https://example.com/a','metadataStatus':'READY'}
        with patch.object(transport,'send',return_value=ok(asset)):
            self.assertFalse(real_human.get_asset('asset1')['ready'])
            self.assertFalse(real_human.get_asset('asset1',model='seedance-2-0')['ready'])
            self.assertTrue(real_human.get_asset('asset1',model='kling-4.0')['ready'])
            self.assertTrue(real_human.get_asset('asset1',model='MiniMax-H3-Image')['ready'])
        with patch.object(transport,'send',return_value=ok(dict(asset,step='DELETING'))):
            self.assertFalse(real_human.get_asset('asset1',model='kling-4.0')['ready'])

    def test_new_discovery_and_estimate_annotation(self):
        from holycrab_runtime.mcp import MCP_TOOLS
        self.assertEqual(len(caps.all_models()),25)
        self.assertFalse(next(t for t in MCP_TOOLS if t['name']=='generation_estimate')['annotations']['readOnlyHint'])

    def test_asset_recovery_actions_preserve_target_model_and_legacy_behavior(self):
        for model in ['kling-4.0', 'MiniMax-H3-Image', 'seedance-2-0', None]:
            for step in ['PROCESSING', 'FAILED']:
                with self.subTest(model=model, step=step), patch.object(transport, 'send', return_value=ok({
                        'uniqId':'asset1', 'step':step, 'error':'fixture reason'})):
                    action=real_human.get_asset('asset1',model=model)['nextAction']
                    self.assertEqual(action['code'], 'WAIT_FOR_ASSET' if step=='PROCESSING' else 'DO_NOT_REUPLOAD_AUTOMATICALLY')
                    suffix=f' --model {model}' if model else ''
                    expected=('holycrab assets wait asset1 --timeout 600' if step=='PROCESSING' else 'holycrab assets get asset1')+suffix
                    self.assertEqual(action['command'],expected)
        for model in ['kling-4.0', 'MiniMax-H3-Image']:
            for step in ['PROCESSING','FAILED']:
                with self.subTest(model=model,step=step),patch.object(transport,'send',return_value=ok({
                        'uniqId':'asset1','step':step,'url':'https://cdn.example/image.jpg','metadataStatus':'PENDING'})):
                    asset=real_human.get_asset('asset1',model=model)
                    self.assertTrue(asset['ready'])
                    self.assertEqual(asset['nextAction']['code'],'ESTIMATE_GENERATION')
            with self.subTest(model=model,step='UPLOADED_TO_ARK without URL'),patch.object(transport,'send',return_value=ok({
                    'uniqId':'asset1','step':'UPLOADED_TO_ARK'})):
                asset=real_human.get_asset('asset1',model=model)
                self.assertFalse(asset['ready'])
                self.assertEqual(asset['nextAction']['code'],'QUERY_ASSET')
                self.assertEqual(asset['nextAction']['command'],f'holycrab assets get asset1 --model {model}')

    def test_deleting_asset_is_terminal_for_model_aware_queries_and_waits(self):
        response=ok({'uniqId':'asset1','step':'DELETING','url':'https://cdn.example/image.jpg'})
        for model in ['kling-4.0','MiniMax-H3-Image','seedance-2-0']:
            with self.subTest(model=model),patch.object(transport,'send',return_value=response),patch.object(real_human.time,'sleep') as sleep:
                asset=real_human.get_asset('asset1',model=model)
                self.assertFalse(asset['ready'])
                self.assertEqual(asset['nextAction']['code'],'CHOOSE_ANOTHER_ASSET')
                self.assertIsNone(asset['nextAction']['command'])
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(real_human.poll_resource('asset1',1,1,authorization=False,model=model),1)
                sleep.assert_not_called()
        with patch.object(transport,'send',return_value=response):
            self.assertEqual(real_human.get_asset('asset1')['nextAction']['code'],'QUERY_ASSET')

    def test_asset_wait_timeout_preserves_model_in_followup(self):
        for model in ['kling-4.0','seedance-2-0',None]:
            output=io.StringIO()
            with self.subTest(model=model),patch.object(real_human,'get_asset',return_value={
                    'uniqId':'asset1','step':'PROCESSING','ready':False}),patch.object(real_human.time,'monotonic',side_effect=[0,2]),redirect_stdout(output):
                self.assertEqual(real_human.poll_resource('asset1',1,1,authorization=False,model=model),2)
            records=[];remaining=output.getvalue().strip();decoder=json.JSONDecoder()
            while remaining:
                value,end=decoder.raw_decode(remaining);records.append(value);remaining=remaining[end:].strip()
            self.assertEqual(records[-1]['nextAction']['command'],'holycrab assets get asset1'+(f' --model {model}' if model else ''))

    def test_failed_final_requires_new_estimate_and_confirmation(self):
        request={'sourceDraftTaskUniqId':'draft1','resolution':'1080p'}
        source=ok({'uniqId':'draft1','draftHdTaskUniqId':'final1'})
        failed=ok({'uniqId':'final1','step':3,'resolution':'1080p'})
        with patch.object(transport,'send',side_effect=[source,failed,source,failed,ok({'frozenCredit':12})]) as send:
            result=generation.create_generation('video',request,confirmed=False)
            self.assertTrue(result['confirmationRequired'])
            self.assertEqual(result['estimate']['frozenCredit'],12)
            self.assertNotIn('reused',result)
            self.assertEqual([c.args[1] for c in send.call_args_list if c.args[0]=='POST'],['/api/tasks/generation/freeze-credit'])

    def test_nonfailed_finals_are_reused_and_kling_sound_modes_are_distinct(self):
        for step in [0,1,2]:
            with self.subTest(step=step),patch.object(transport,'send',side_effect=[
                    ok({'uniqId':'draft1','draftHdTaskUniqId':'final1'}),ok({'uniqId':'final1','step':step,'resolution':'1080p'})]) as send:
                self.assertTrue(generation.create_generation('video',{'sourceDraftTaskUniqId':'draft1','resolution':'1080p'},confirmed=True)['reused'])
                self.assertTrue(all(c.args[0]=='GET' for c in send.call_args_list))
        image_reference={'type':'refer_image','assetId':'image1','id':'Image1'}
        caps.normalize_generation_request('video',kling('kling-4.0-flash',audio='native',references=[image_reference]))
        video={'type':'feature_video','assetId':'video1','id':'Video1'}
        caps.normalize_generation_request('video',kling(audio='original',references=[video]))
        with self.assertRaises(SystemExit):
            caps.normalize_generation_request('video',kling(audio='original',references=[video,{'type':'voice','assetId':'audio1','id':'Voice1'}]))

if __name__=='__main__':unittest.main()
