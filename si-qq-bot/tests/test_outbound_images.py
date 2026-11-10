import io
import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image
import outbound_images as images


def png():
    output = io.BytesIO()
    Image.new('RGB', (32, 32), 'green').save(output, format='PNG')
    return output.getvalue()


class ImageTests(unittest.TestCase):
    def test_plain_object_and_explicit_anime_requests_use_different_models(self):
        self.assertEqual(images.select_checkpoint('一个红杯子在蓝桌上'),images.GENERAL_CHECKPOINT)
        self.assertEqual(images.select_checkpoint('动漫风格的红杯子'),images.CHECKPOINT)

    def test_model_cannot_supply_arbitrary_checkpoint_paths(self):
        with self.assertRaises(ValueError):
            images.generate_segment('a cup',checkpoint='C:/untrusted.safetensors',request=lambda *args:self.fail('must not submit'))

    def test_native_image_not_file_path_or_text(self):
        segment = images.image_segment(png())
        self.assertEqual(segment['type'], 'image')
        self.assertTrue(segment['data']['file'].startswith('base64://'))

    def test_invalid_bytes_rejected(self):
        for value in (b'', b'fake png' * 20, 'C:/secret.png'):
            with self.assertRaises(Exception):
                images.image_segment(value)

    def test_explicit_requests_only(self):
        self.assertEqual(images.request_kind('/画图 青绿色山水'), ('generate', '青绿色山水'))
        self.assertEqual(images.request_kind('发个表情包 开心'), ('reaction', '开心'))
        for text in ('我刚才画了一张图', '你能看懂图片吗', '这张图片是什么'):
            self.assertIsNone(images.request_kind(text))

    def test_curated_reactions_reject_traversal_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'ok.png').write_bytes(png())
            (root/'manifest.json').write_text(json.dumps({'ok':'ok.png', 'bad':'../secret.png'}))
            self.assertEqual(images.reaction_segment(root,'ok')['type'], 'image')
            for name in ('bad', 'missing'):
                with self.assertRaises(ValueError):
                    images.reaction_segment(root,name)

    def test_generation_fetches_only_submitted_job(self):
        calls=[]
        def request(route, payload=None):
            calls.append(route)
            if route == 'prompt':
                self.assertEqual(payload['prompt']['4']['inputs']['batch_size'], 1)
                return b'{"prompt_id":"synthetic-job"}'
            if route == 'history/synthetic-job':
                return json.dumps({'synthetic-job':{'outputs':{'7':{'images':[{'filename':'x.png','subfolder':'','type':'output'}]}}}}).encode()
            if route.startswith('view?'): return png()
            self.fail(route)
        self.assertEqual(images.generate_segment('青绿山水', request=request)['type'], 'image')
        self.assertEqual(len(calls), 3)

    def test_generation_failure_never_fetches_image(self):
        def request(route,payload=None):
            if route=='prompt': return b'{"prompt_id":"failed-job"}'
            self.assertEqual(route,'history/failed-job')
            return b'{"failed-job":{"status":{"status_str":"error"}}}'
        with self.assertRaises(ValueError):
            images.generate_segment('青绿山水',request=request)

    def test_timeout_does_not_interrupt_other_jobs(self):
        ticks=iter([0,0,2])
        calls=[]
        def request(route,payload=None):
            calls.append(route)
            return b'{"prompt_id":"slow-job"}' if route=='prompt' else b'{}'
        with self.assertRaises(TimeoutError):
            images.generate_segment('青绿山水',timeout=1,request=request,clock=lambda:next(ticks),pause=lambda _:None)
        self.assertEqual(calls,['prompt','history/slow-job'])
