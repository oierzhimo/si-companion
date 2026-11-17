"""Tests for si_pet.chat_backend.

All tests are unittests that do not depend on real network or VoiceStudio.
"""
import base64
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from si_pet import chat_backend
from si_pet.chat_backend import (
    ChatError,
    ChatSession,
    MAX_IMAGE_BYTES,
    attach_images,
    check_voice_service,
    prepare_image_attachment,
)


def _make_jpeg_bytes(width=10, height=10, color='red') -> bytes:
    buf = io.BytesIO()
    im = Image.new('RGB', (width, height), color=color)
    im.save(buf, format='JPEG')
    return buf.getvalue()


def _make_png_alpha_bytes(width=10, height=10) -> bytes:
    buf = io.BytesIO()
    im = Image.new('RGBA', (width, height), color=(255, 0, 0, 128))
    im.save(buf, format='PNG')
    return buf.getvalue()


class ChatBackendTests(unittest.TestCase):
    def test_prepare_image_attachment_jpeg(self):
        raw = _make_jpeg_bytes(10, 10, 'red')
        data_url = prepare_image_attachment(raw)
        prefix = 'data:image/jpeg;base64,'
        self.assertTrue(data_url.startswith(prefix))
        b64_part = data_url[len(prefix):]
        decoded = base64.b64decode(b64_part)
        self.assertTrue(len(decoded) > 0)
        # Verify decoded payload can be reopened as an image
        with Image.open(io.BytesIO(decoded)) as im:
            self.assertEqual(im.format, 'JPEG')

    def test_prepare_image_attachment_with_alpha(self):
        raw = _make_png_alpha_bytes(10, 10)
        data_url = prepare_image_attachment(raw)
        prefix = 'data:image/jpeg;base64,'
        self.assertTrue(data_url.startswith(prefix))
        b64_part = data_url[len(prefix):]
        decoded = base64.b64decode(b64_part)
        with Image.open(io.BytesIO(decoded)) as im:
            self.assertEqual(im.format, 'JPEG')
            self.assertEqual(im.mode, 'RGB')

    def test_prepare_image_attachment_too_large(self):
        fake_bytes = b'x' * (MAX_IMAGE_BYTES + 1)
        with self.assertRaises(ChatError):
            prepare_image_attachment(fake_bytes)

    def test_prepare_image_attachment_invalid(self):
        with self.assertRaises(ChatError):
            prepare_image_attachment(b'not an image')

    def test_attach_images_adds_parts(self):
        messages = [
            {'role': 'system', 'content': '你是一位伙伴。'},
            {'role': 'user', 'content': '你好'},
        ]
        img_url = 'data:image/jpeg;base64,abc'
        result = attach_images(messages, [img_url])
        last = result[-1]
        self.assertEqual(last['role'], 'user')
        self.assertIsInstance(last['content'], list)
        self.assertEqual(
            last['content'],
            [
                {'type': 'text', 'text': '你好'},
                {'type': 'image_url', 'image_url': {'url': img_url}},
            ],
        )

    def test_attach_images_does_not_mutate(self):
        original = [
            {'role': 'system', 'content': '系统提示'},
            {'role': 'user', 'content': '原始用户文字'},
        ]
        img_url = 'data:image/jpeg;base64,abc'
        _ = attach_images(original, [img_url])
        self.assertIsInstance(original[-1]['content'], str)
        self.assertEqual(original[-1]['content'], '原始用户文字')

    def test_attach_images_multiple(self):
        messages = [
            {'role': 'system', 'content': '系统提示'},
            {'role': 'user', 'content': '看这几张图'},
        ]
        urls = [
            'data:image/jpeg;base64,img1',
            'data:image/jpeg;base64,img2',
            'data:image/jpeg;base64,img3',
        ]
        result = attach_images(messages, urls)
        content_parts = result[-1]['content']
        self.assertIsInstance(content_parts, list)
        self.assertEqual(len(content_parts), 4)
        self.assertEqual(content_parts[0], {'type': 'text', 'text': '看这几张图'})
        for i, url in enumerate(urls, start=1):
            self.assertEqual(
                content_parts[i],
                {'type': 'image_url', 'image_url': {'url': url}},
            )

    def test_chat_session_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'multimodal_conversation.json'
            session = ChatSession(path)
            session.commit('你好', '嗯')

            restored = ChatSession(path)
            self.assertEqual(len(restored.turns), 1)
            self.assertEqual(
                restored.turns[0],
                {'user': '你好', 'assistant': '嗯'},
            )

    def test_chat_session_messages_with_text(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'session.json'
            session = ChatSession(path)
            msgs = session.messages('测试')
            self.assertEqual(msgs[0]['role'], 'system')
            self.assertEqual(msgs[-1]['role'], 'user')
            self.assertEqual(msgs[-1]['content'], '测试')

    def test_chat_session_messages_with_images(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'session.json'
            session = ChatSession(path)
            msgs = session.messages('看图', ['data:image/jpeg;base64,abc'])
            last = msgs[-1]
            self.assertEqual(last['role'], 'user')
            self.assertIsInstance(last['content'], list)
            self.assertEqual(last['content'][0], {'type': 'text', 'text': '看图'})
            self.assertEqual(
                last['content'][1],
                {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,abc'}},
            )

    def test_check_voice_service_unavailable(self):
        with patch('http.client.HTTPConnection', side_effect=ConnectionRefusedError):
            status = check_voice_service()
            self.assertEqual(status, 'unavailable')


if __name__ == '__main__':
    unittest.main()
