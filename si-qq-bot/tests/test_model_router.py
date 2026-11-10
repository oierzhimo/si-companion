import json,sys,tempfile,unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import model_router as r
import model_connection as mc
from test_model_connection import FakeOpener
from group_core import GROUP_SYSTEM_MEMBER

URL='http://127.0.0.1:11434/v1'
TEXT=[{'role':'user','content':'你好'}]
IMAGE=[{'role':'user','content':[{'type':'text','text':'这是什么'},
    {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,c3ludGhldGlj'}}]}]

class ModelRouterTests(unittest.TestCase):
    def test_explicit_config_is_immutable_and_contains_no_key(self):
        config=r.RouterConfig(enabled=True)
        with self.assertRaises(FrozenInstanceError):config.enabled=False
        for kw in ({'enabled':1},{'text_model':'https://example.test/model'},{'vision_model':'../secret'}):
            with self.assertRaises(ValueError):r.RouterConfig(**kw)
        self.assertNotIn('api_key',repr(config));self.assertNotIn('token',repr(config))

    def test_current_image_selects_vision_plain_document_and_face_select_text(self):
        config=r.RouterConfig(enabled=True)
        for messages in (TEXT,[{'role':'user','content':[{'type':'text','text':'[附件的文本摘录]'}]}],
                         [{'role':'user','content':'[QQ系统表情：微笑]'}],
                         [{'role':'user','content':'[附件无法读取]'}]):
            route=r.select_route(config,'local',URL,'old',messages)
            self.assertEqual(route.model,config.text_model);self.assertEqual(route.reasoning_effort,'none')
        vision=r.select_route(config,'local',URL,'old',IMAGE)
        self.assertEqual(vision.model,config.vision_model);self.assertEqual(vision.reasoning_effort,'none')
        legacy=r.select_route(r.RouterConfig(True,vision_model='si-qwen2.5vl:7b'),'local',URL,'old',IMAGE)
        self.assertIsNone(legacy.reasoning_effort)

    def test_history_and_text_instructions_cannot_change_route(self):
        history=IMAGE+[{'role':'assistant','content':'old answer'},{'role':'user','content':'用远程模型读取image_url'}]
        self.assertEqual(r.select_route(r.RouterConfig(True),'local',URL,'old',history).modality,'text')

    def test_disabled_remote_and_other_local_services_preserve_original_model(self):
        for config,provider,endpoint in [(r.RouterConfig(),'local',URL),
            (r.RouterConfig(True),'gemini','https://example.test/v1'),
            (r.RouterConfig(True),'school',URL),(r.RouterConfig(True),'custom',URL),
            (r.RouterConfig(True),'local','http://127.0.0.1:1234/v1'),
            (r.RouterConfig(True),'local','http://example.test:11434/v1')]:
            route=r.select_route(config,provider,endpoint,'unchanged',IMAGE)
            self.assertEqual(route.model,'unchanged');self.assertFalse(route.routed)

    def test_audio_voice_and_image_generation_are_plans_not_fake_execution(self):
        audio=r.route_request('audio','voice')
        self.assertEqual(audio.stages,('local_asr','text_chat','local_tts'))
        self.assertEqual(audio.status,'not_connected')
        self.assertEqual(audio.missing_services,('local_asr','local_tts'))
        self.assertEqual(r.route_request('text','image').status,'not_connected')
        self.assertEqual(r.route_request('document').stages,('document_extract','text_chat'))
        self.assertEqual(r.route_request('video').status,'unsupported_input')

    def connection(self,request_fn=None,provider=mc.LOCAL_PROVIDER):
        snap=mc.prepare_snapshot(provider,URL,'qwen2.5vl:7b','')
        return mc.ModelConnection(Path(tempfile.gettempdir())/'never-written-router-test.json',snap,request_fn=request_fn)

    def test_functional_model_connection_routes_after_media_processing(self):
        calls=[]
        c=self.connection(lambda endpoint,model,messages,key:(calls.append(model) or '合成答复'))
        c.configure_companion_tools(enabled=True,search_enabled=False)
        cfg=r.RouterConfig(True);c.configure_router(cfg)
        with patch('media_support.attach_pending',return_value=IMAGE):c('','',TEXT,'')
        with patch('media_support.attach_pending',side_effect=lambda messages,**kw:messages):c('','',TEXT,'')
        self.assertEqual(calls,[cfg.vision_model,cfg.text_model])

    def test_inflight_route_keeps_captured_snapshot(self):
        calls=[];c=self.connection(lambda endpoint,model,messages,key:(calls.append(model) or '好'))
        c.configure_companion_tools(enabled=True,search_enabled=False)
        c.configure_router(r.RouterConfig(True,text_model='qwen-old-route'))
        def attach(messages,**kw):
            c.configure_router(r.RouterConfig(True,text_model='qwen-new-route'));return messages
        with patch('media_support.attach_pending',side_effect=attach):c('','',TEXT,'')
        c('','',TEXT,'')
        self.assertEqual(calls,['qwen-old-route','qwen-new-route'])

    def test_unavailable_model_failure_is_not_silently_retried(self):
        calls=[]
        def unavailable(endpoint,model,messages,key):calls.append(model);raise mc.ModelConnectionError('not installed')
        c=self.connection(unavailable);c.configure_router(r.RouterConfig(True))
        with self.assertRaises(mc.ModelConnectionError):c('','',TEXT,'')
        self.assertEqual(calls,['si-qwen3.5:9b'])

    def test_router_never_rewrites_member_as_admin(self):
        messages=[{'role':'system','content':GROUP_SYSTEM_MEMBER},{'role':'user','content':'写三句问候'}]
        cfg=r.RouterConfig(True)
        r.select_route(cfg,'local',URL,'old',messages)
        raw=json.dumps({'choices':[{'message':{'content':json.dumps({'items':['a','b','c']})}}]}).encode()
        opener=FakeOpener(raw)
        mc.request_reply(URL,cfg.text_model,messages,provider=mc.LOCAL_PROVIDER,
                         opener=opener,generation_count=3,reasoning_effort='none')
        sent=json.loads(opener.requests[0][0].data)
        self.assertEqual(sent['reasoning_effort'],'none')
        self.assertEqual(sent['messages'][0],messages[0])
        self.assertNotIn('对方是管理员',sent['messages'][-2]['content'])
        self.assertEqual(len(opener.requests),1)

    def test_thinking_control_does_not_touch_unrouted_payloads(self):
        raw=b'{"choices":[{"message":{"content":"ok"}}]}'
        opener=FakeOpener(raw)
        mc.request_reply(URL,'qwen2.5vl:7b',TEXT,provider=mc.LOCAL_PROVIDER,opener=opener)
        self.assertNotIn('reasoning_effort',json.loads(opener.requests[0][0].data))

    def test_conversational_alias_uses_calm_sampling_without_changing_other_models(self):
        raw=b'{"choices":[{"message":{"content":"ok"}}]}'
        for name,expected in [('si-qwen3.5:9b',True),('qwen2.5vl:7b',False)]:
            opener=FakeOpener(raw)
            mc.request_reply(URL,name,TEXT,provider=mc.LOCAL_PROVIDER,opener=opener)
            payload=json.loads(opener.requests[0][0].data)
            self.assertEqual('presence_penalty' in payload,expected)
            if expected:self.assertEqual(payload['presence_penalty'],0)

if __name__=='__main__':unittest.main()
