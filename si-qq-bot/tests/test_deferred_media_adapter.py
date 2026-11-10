"""Synthetic complete reader -> authorized core -> media resolver checks."""
import asyncio
import base64
import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from onebot_adapter import OneBotAdapter
from media_support import attach_pending

BOT='10001'; GROUP='30001'; ADMIN='20001'; OTHER='20002'


def event(mid, sender=ADMIN, group=GROUP, segments=None, private=False):
    return {'post_type':'message','message_type':'private' if private else 'group',
            'sub_type':'friend' if private else 'normal','self_id':BOT,'group_id':group,
            'user_id':sender,'message_id':mid,'message':segments or []}


def image(token): return {'type':'image','data':{'file':token}}
def mention(): return {'type':'at','data':{'qq':BOT}}


class Feed:
    def __init__(self, packets):self.packets=iter(packets)
    def __aiter__(self):return self
    async def __anext__(self):
        try:return json.dumps(next(self.packets))
        except StopIteration:raise StopAsyncIteration


class Core:
    def __init__(self):self.calls=[];self.finalized=[]
    def is_administrator(self, sender):return sender==ADMIN
    def _call(self, text, kind):
        messages=attach_pending([{'role':'system','content':'synthetic persona'},
                                 {'role':'user','content':text}],local_enabled=True)
        self.calls.append(messages)
        return SimpleNamespace(kind=kind,text='已收到。',delivery_token='synthetic-token')
    def handle_group(self, group,sender,mid,text):return self._call(text,'group_chat')
    def handle_private(self,sender,mid,text):return self._call(text,'chat')
    def finalize_group(self, token, delivered):self.finalized.append(delivered);return True
    def finalize_reply(self, token, delivered):self.finalized.append(delivered);return True


class DeferredMediaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from PIL import Image
        buffer=io.BytesIO();Image.new('RGB',(32,32),'red').save(buffer,format='PNG')
        self.image_bytes=buffer.getvalue();self.api_calls=[]
        self.private=Core();self.group=Core();self.enabled=True
        self.adapter=OneBotAdapter('ws://127.0.0.1:3001/','synthetic',lambda bot:self.private,
            group_factory=lambda *args:self.group,group_enabled=lambda:self.enabled,send_pacing=0)
        self.adapter._bot_id=BOT;self.adapter._core=self.private;self.adapter._group_core=self.group;self.adapter._running=True
        async def api(ws,pending,action,params):
            self.api_calls.append((action,params))
            if action=='get_file':
                is_text=params['file']=='file-1'
                body=b'only synthetic document text' if is_text else self.image_bytes
                return {'status':'ok','retcode':0,'data':{
                    'base64':base64.b64encode(body).decode(),'file_name':'sample.txt' if is_text else 'sample.png'}}
            return {'status':'ok','retcode':0,'data':{'message_id':123}}
        self.adapter._api=api

    async def read(self,packets,queue=None):
        queue=queue if queue is not None else asyncio.Queue(16)
        await self.adapter._reader(Feed(packets),{},asyncio.Queue(64),queue)
        return queue

    async def handle(self,queue):
        await self.adapter._handle_group_event(None,{},queue.get_nowait(),self.group)

    async def test_image_waits_silently_until_same_sender_mentions_then_resolves(self):
        queue=await self.read([event(-1,segments=[image('a.png')])])
        self.assertTrue(queue.empty());self.assertEqual(self.api_calls,[]);self.assertEqual(self.group.calls,[])
        queue=await self.read([event(2,segments=[mention()])])
        await self.handle(queue)
        self.assertTrue(any(p.get('type')=='image_url' for p in self.group.calls[0][-1]['content']))
        self.assertEqual([a for a,p in self.api_calls],['get_file','send_group_msg'])
        self.assertEqual(self.group.finalized,[True])

    async def test_cross_sender_and_later_media_are_not_attached_to_earlier_mention(self):
        queue=await self.read([event(1,segments=[image('a.png')]),
            event(2,sender=OTHER,segments=[image('other.png')]),
            event(3,segments=[mention()]),event(4,segments=[image('later.png')])])
        await self.handle(queue)
        self.assertEqual([p['file'] for a,p in self.api_calls if a=='get_file'],['a.png'])
        self.assertEqual(self.adapter._pending_group_media.take(GROUP,OTHER)[0]['message_id'],'2')
        self.assertEqual(self.adapter._pending_group_media.take(GROUP,ADMIN)[0]['message_id'],'4')

    async def test_duplicate_mention_cannot_consume_newer_attachment(self):
        queue=await self.read([event(1,segments=[image('a.png')]),event(2,segments=[mention()]),
            event(3,segments=[image('new.png')]),event(2,segments=[mention()])])
        self.assertEqual(queue.qsize(),1)
        self.assertEqual(self.adapter._pending_group_media.take(GROUP,ADMIN)[0]['message_id'],'3')

    async def test_queue_full_restores_media_for_later_mention(self):
        queue=asyncio.Queue(1);queue.put_nowait('occupied')
        await self.read([event(1,segments=[image('a.png')]),event(2,segments=[mention()])],queue)
        self.assertEqual(self.adapter._pending_group_media.stats()['items'],1)
        self.assertEqual(self.api_calls,[])

    async def test_group_upload_notice_is_staged_and_text_file_read_on_mention(self):
        notice={'post_type':'notice','notice_type':'group_upload','self_id':BOT,'group_id':GROUP,
                'user_id':ADMIN,'file':{'id':'file-1','name':'sample.txt','size':28,'busid':102}}
        queue=await self.read([notice,event(2,segments=[mention()])])
        self.assertEqual(self.api_calls,[])
        await self.handle(queue)
        self.assertIn('only synthetic document text',str(self.group.calls))

    async def test_group_off_discards_buffer_without_any_api_calls(self):
        await self.read([event(1,segments=[image('a.png')])])
        self.enabled=False
        await self.read([event(2,segments=[mention()])])
        self.assertEqual(self.adapter._pending_group_media.stats()['items'],0)
        self.assertEqual(self.api_calls,[])

    async def test_private_media_is_admin_only_and_does_not_take_group_media(self):
        await self.read([event(1,segments=[image('group.png')])])
        await self.adapter._handle_event(None,{},event(2,sender=OTHER,segments=[image('other.png')],private=True),set())
        self.assertEqual(self.api_calls,[])
        await self.adapter._handle_event(None,{},event(3,segments=[image('private.png')],private=True),set())
        self.assertEqual([p['file'] for a,p in self.api_calls if a=='get_file'],['private.png'])
        self.assertEqual(self.adapter._pending_group_media.stats()['items'],1)

    async def test_face_then_mention_uses_name_without_file_request(self):
        queue=await self.read([event(1,segments=[{'type':'face','data':{'id':'14'}}]),event(2,segments=[mention()])])
        await self.handle(queue)
        self.assertIn('微笑',str(self.group.calls))
        self.assertFalse(any(a=='get_file' for a,p in self.api_calls))


if __name__=='__main__':unittest.main()
