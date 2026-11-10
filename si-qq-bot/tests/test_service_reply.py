import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import service_reply
from onebot_adapter import OneBotAdapter


class ServiceTests(unittest.TestCase):
    def test_review_failure_never_returns_generated_bitmap(self):
        segment={'type':'image','data':{'file':'base64://synthetic'}}
        with patch('outbound_images.generate_segment',return_value=segment), patch('image_review.review_image',return_value=SimpleNamespace(matches=False)):
            result=service_reply.respond(Mock(),(),'\u002f画图 one red cup','unused')
        self.assertIsNone(result.segment)
        self.assertIn('核对没有通过',result)

    def test_definite_media_rejection_sends_one_text_notice(self):
        adapter=OneBotAdapter('ws://127.0.0.1:3001','synthetic-token',lambda _:None)
        sent=[]
        finalized=[]
        async def api(ws,pending,action,params):
            sent.append(params['message'])
            return {'status':'failed','retcode':1200} if len(sent)==1 else {'status':'ok','retcode':0,'data':{'message_id':1}}
        async def finalize(core,token,delivered):
            finalized.append(delivered); return True
        adapter._api=api; adapter._finalize_core=finalize
        segment={'type':'record','data':{'file':'base64://synthetic'}}
        reply=SimpleNamespace(text='测试',delivery_token='synthetic',segment=segment)
        asyncio.run(adapter._deliver_private_chunks(None,{},Mock(),reply,'456',['测试'],True))
        self.assertEqual(len(sent),2)
        self.assertEqual(sent[0],[segment])
        self.assertEqual(sent[1][0]['type'],'text')
        self.assertEqual(finalized,[False])

    def test_unknown_media_result_never_resends_or_sends_notice(self):
        adapter=OneBotAdapter('ws://127.0.0.1:3001','synthetic-token',lambda _:None)
        sent=[];core=Mock()
        async def api(ws,pending,action,params):
            sent.append(params['message']); raise TimeoutError()
        adapter._api=api
        reply=SimpleNamespace(text='测试',delivery_token='synthetic',segment={'type':'record','data':{'file':'base64://synthetic'}})
        asyncio.run(adapter._deliver_private_chunks(None,{},core,reply,'456',['测试'],True))
        self.assertEqual(len(sent),1)
        core.mark_delivery_unknown.assert_called_once_with('synthetic')

    def test_group_exact_voice_command_is_not_sent_to_model(self):
        model=Mock(side_effect=AssertionError('must bypass model'))
        segment={'type':'record','data':{'file':'base64://synthetic'}}
        with patch('voice_reply.prepare_reply',return_value=SimpleNamespace(voice_ready=True,segments=[segment])):
            reply=service_reply.respond(model,(),'/语音','unused')
        self.assertEqual(reply.segment,segment)
        model.assert_not_called()

    def test_natural_group_request_synthesizes_and_strips_placeholder(self):
        segment={'type':'record','data':{'file':'base64://synthetic'}}
        with patch('voice_reply.prepare_reply',return_value=SimpleNamespace(voice_ready=True,segments=[segment])) as prepare:
            reply=service_reply.respond(lambda:'【语音】你好。',(),'用语音回复我','unused')
        self.assertEqual(reply.segment,segment)
        prepare.assert_called_once_with('你好。',enabled=True)

    def test_image_request_uses_generated_bitmap_and_review(self):
        model=Mock(return_value='{"matches":true,"problems":[]}')
        segment={'type':'image','data':{'file':'base64://synthetic'}}
        with patch('outbound_images.generate_segment',return_value=segment), patch('image_review.review_image',return_value=__import__('types').SimpleNamespace(matches=True)):
            reply=service_reply.respond(model,(),'/画图 green mountains','unused')
        self.assertEqual(reply.segment,segment)
        model.assert_not_called()

    def test_voice_request_never_calls_text_model(self):
        segment={'type':'record','data':{'file':'base64://synthetic'}}
        with patch('voice_reply.prepare_reply',return_value=SimpleNamespace(voice_ready=True,segments=[segment])) as prepare:
            reply=service_reply.respond(Mock(side_effect=AssertionError()),(),'/朗读 管理员，早上好','unused')
        self.assertEqual(reply.segment,segment)
        prepare.assert_called_once_with('管理员，早上好',enabled=True)

    def test_generation_failure_is_not_fabricated_success(self):
        with patch('outbound_images.generate_segment',side_effect=TimeoutError):
            reply=service_reply.respond(Mock(),(),'/画图 green mountains','unused')
        self.assertIsNone(reply.segment)
        self.assertIn('没画出来',reply)

    def test_normal_text_not_misrouted(self):
        with tempfile.TemporaryDirectory() as temp:
            reply=service_reply.respond(lambda:'答复',(),'生成一段管理员介绍',temp,private=True)
        self.assertEqual(reply,'答复')

    def test_group_media_preserves_native_at_space_and_ack(self):
        adapter=OneBotAdapter('ws://127.0.0.1:3001','synthetic-token',lambda _:None)
        sent=[]
        core=Mock()
        async def api(ws,pending,action,params):
            sent.append((action,params)); return {'status':'ok','retcode':0,'data':{'message_id':99}}
        async def finalize(core,token,delivered):
            self.assertTrue(delivered); return True
        adapter._api=api; adapter._finalize_core=finalize
        segment={'type':'image','data':{'file':'base64://synthetic'}}
        reply=SimpleNamespace(text='画好了。',delivery_token='synthetic',segment=segment)
        asyncio.run(adapter._deliver_group_chunks(None,{},core,reply,'123','456',['画好了。'],True))
        self.assertEqual(sent[0][1]['message'],[{'type':'at','data':{'qq':'456'}},{'type':'text','data':{'text':' '}},segment])
