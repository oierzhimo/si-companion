import io,json,sys,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import media_support as ms
from test_deferred_media_adapter import DeferredMediaTests,event,image,ADMIN,OTHER

class CacheTests(unittest.TestCase):
    def test_only_authenticated_named_file_under_explicit_cache_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);cache=root/'cache';cache.mkdir();file=cache/'sample.txt';file.write_bytes(b'synthetic')
            packet=dict(status='ok',retcode=0,data=dict(file=str(file),file_name=file.name))
            self.assertEqual(ms.decode_download_reply(packet,cache_roots=[cache]),b'synthetic')
            with self.assertRaises(ValueError):ms.decode_download_reply(packet)
            packet['data']['file_name']='different.txt'
            with self.assertRaises(ValueError):ms.decode_download_reply(packet,cache_roots=[cache])
            packet['data']['file_name']=file.name
            with self.assertRaises(ValueError):ms.decode_download_reply(packet,cache_roots=[root/'other'])
            packet['status']='failed'
            with self.assertRaises(ValueError):ms.decode_download_reply(packet,cache_roots=[cache])

class PrivateFollowupTests(DeferredMediaTests):
    async def test_followup_reuses_only_latest_bound_private_attachment(self):
        seen=set()
        await self.adapter._handle_event(None,{},event(50,segments=[image('private.png')],private=True),seen)
        await self.adapter._handle_event(None,{},event(51,segments=[{'type':'text','data':{'text':'这是什么'}}],private=True),seen)
        self.assertEqual([p['file'] for a,p in self.api_calls if a=='get_file'],['private.png','private.png'])
        await self.adapter._handle_event(None,{},event(52,segments=[{'type':'file','data':{'file':'file-1'}}],private=True),seen)
        await self.adapter._handle_event(None,{},event(53,segments=[{'type':'text','data':{'text':'概括一下文件内容'}}],private=True),seen)
        self.assertIn('only synthetic document text',str(self.private.calls[-1]))
        self.assertNotIn('image_url',str(self.private.calls[-1]))
        await self.adapter._handle_event(None,{},event(54,sender=OTHER,segments=[{'type':'text','data':{'text':'概括一下文件'}}],private=True),seen)
        self.assertNotIn('only synthetic document text',str(self.private.calls[-1]))

    async def test_private_followup_expiry_and_stop_clear(self):
        seen=set()
        await self.adapter._handle_event(None,{},event(60,segments=[image('private.png')],private=True),seen)
        stamp,items=self.adapter._private_recent_media[ADMIN]
        self.adapter._private_recent_media[ADMIN]=(stamp-601,items)
        await self.adapter._handle_event(None,{},event(61,segments=[{'type':'text','data':{'text':'看看这张图片'}}],private=True),seen)
        self.assertNotIn('image_url',str(self.private.calls[-1]))
        self.adapter.clear_group_media();self.assertEqual(self.adapter._private_recent_media,{})

if __name__=='__main__':unittest.main()
