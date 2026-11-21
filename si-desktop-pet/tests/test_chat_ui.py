"""Unit and lifecycle tests for MultimodalDialog in offscreen Qt mode."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

try:
    from PySide6.QtWidgets import QApplication
    from si_pet.chat_ui import MultimodalDialog
    HAS_PYSIDE6 = True
except ImportError:
    HAS_PYSIDE6 = False

_APP = None

def get_app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication(['test', '-platform', 'offscreen'])
    return _APP


class DummyCtrl:
    def __init__(self):
        self.say_calls = []

    def say(self, text, flag):
        self.say_calls.append((text, flag))


class TestMultimodalDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAS_PYSIDE6:
            raise unittest.SkipTest("PySide6 not installed in current Python environment")
        get_app()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.conv_path = Path(self.temp_dir.name) / 'multimodal_conversation.json'
        self.ctrl = DummyCtrl()
        self.dialog = MultimodalDialog(self.ctrl, self.conv_path)

    def tearDown(self):
        try:
            self.dialog.close()
        except Exception:
            pass
        self.temp_dir.cleanup()

    def test_initial_state(self):
        self.assertEqual(self.dialog.windowTitle(), '和祀聊聊 · 多模态聊聊')
        self.assertFalse(self.dialog.voice_mode)
        self.assertEqual(len(self.dialog.pending_images), 0)
        self.assertIn('0 张', self.dialog.attach_label.text())

    def test_voice_mode_toggle(self):
        self.assertFalse(self.dialog.voice_mode)
        self.dialog.voice_mode = True
        self.assertTrue(self.dialog.voice_mode)
        self.dialog.voice_mode = False
        self.assertFalse(self.dialog.voice_mode)

    def test_clear_images(self):
        self.dialog.pending_images = [b'fake-image-1', b'fake-image-2']
        self.dialog._update_attach_label()
        self.assertIn('2 张', self.dialog.attach_label.text())

        self.dialog.clear_images()
        self.assertEqual(len(self.dialog.pending_images), 0)
        self.assertIn('0 张', self.dialog.attach_label.text())

    def test_clear_turns(self):
        self.dialog.session.turns = [{'user': '你好', 'assistant': '在呢'}]
        self.dialog.session.save()
        self.dialog.render()
        self.assertIn('你好', self.dialog.transcript.toPlainText())

        self.dialog.clear_turns()
        self.assertEqual(len(self.dialog.session.turns), 0)
        self.assertEqual(self.dialog.transcript.toPlainText(), '')

    def test_submit_empty_rejected(self):
        self.dialog.input.setPlainText('')
        self.dialog.pending_images = []
        self.dialog.submit()
        self.assertIn('请输入文字或附加图片', self.dialog.status.text())
        self.assertFalse(self.dialog.busy)

    def test_render_escapes_or_plain_text(self):
        self.dialog.session.turns = [{'user': '<b>粗体</b>', 'assistant': '<i>斜体</i>'}]
        self.dialog.render()
        text = self.dialog.transcript.toPlainText()
        self.assertIn('管理员：<b>粗体</b>', text)
        self.assertIn('祀：<i>斜体</i>', text)

    def test_voice_profile_id_from_store_and_fallback(self):
        self.assertEqual(self.dialog._voice_profile_id(), '06332768')
        self.ctrl.store = {'voice_profile_id': 'custom_profile'}
        self.assertEqual(self.dialog._voice_profile_id(), 'custom_profile')
        self.ctrl.store = {'voice_profile_id': '   '}
        self.assertEqual(self.dialog._voice_profile_id(), '06332768')

    def test_default_lora_models_and_image_routing(self):
        from si_pet.chat_ui import (
            DEFAULT_OLLAMA_MODEL,
            DEFAULT_OLLAMA_VISION_MODEL,
            resolve_local_chat_model,
        )

        self.assertEqual(self.dialog.session.model, DEFAULT_OLLAMA_MODEL)
        self.assertEqual(
            resolve_local_chat_model('local', 'http://127.0.0.1:11434/v1', DEFAULT_OLLAMA_MODEL, False),
            DEFAULT_OLLAMA_MODEL,
        )
        self.assertEqual(
            resolve_local_chat_model('local', 'http://127.0.0.1:11434/v1', DEFAULT_OLLAMA_MODEL, True),
            DEFAULT_OLLAMA_VISION_MODEL,
        )
        self.assertEqual(
            resolve_local_chat_model('local', 'http://127.0.0.1:11434/v1', 'si-qwen3.5:9b', True),
            'si-qwen3.5:9b',
        )
        self.assertEqual(
            resolve_local_chat_model('genai', 'http://127.0.0.1:8000/v1', 'DeepSeek-V4.1-Flash', True),
            'DeepSeek-V4.1-Flash',
        )

    def test_deepseek_preset_switch_and_image_guard(self):
        from si_pet.chat_ui import DEFAULT_OLLAMA_ENDPOINT, DEFAULT_OLLAMA_MODEL
        from si_pet.conversation import DEEPSEEK_DEFAULT_ENDPOINT, DEEPSEEK_DEFAULT_MODEL

        self.assertEqual(self.dialog.service.currentData(), 'local')
        self.assertEqual(self.dialog.endpoint.text(), DEFAULT_OLLAMA_ENDPOINT)
        self.assertEqual(self.dialog.model.text(), DEFAULT_OLLAMA_MODEL)

        ds_idx = self.dialog.service.findData('deepseek')
        self.assertGreaterEqual(ds_idx, 0)
        self.dialog.service.setCurrentIndex(ds_idx)
        get_app().processEvents()

        self.assertEqual(self.dialog.provider, 'deepseek')
        self.assertEqual(self.dialog.endpoint.text(), DEEPSEEK_DEFAULT_ENDPOINT)
        self.assertEqual(self.dialog.model.text(), DEEPSEEK_DEFAULT_MODEL)
        self.assertEqual(self.dialog.api_key.text(), '')

        # DeepSeek text-only API rejects attached images cleanly before networking
        self.dialog.api_key.setText('sk-test')
        self.dialog.input.setPlainText('看看这张图')
        self.dialog.pending_images = [b'fake-jpeg']
        self.dialog.submit()
        self.assertFalse(self.dialog.busy)
        self.assertIn('纯文字', self.dialog.status.text())

    def test_remote_host_preset_and_vision_routing(self):
        from si_pet.chat_ui import (
            DEFAULT_OLLAMA_MODEL,
            DEFAULT_OLLAMA_VISION_MODEL,
            resolve_local_chat_model,
        )
        from si_pet.conversation import REMOTE_DEFAULT_ENDPOINT

        remote_idx = self.dialog.service.findData('remote')
        self.assertGreaterEqual(remote_idx, 0)
        self.dialog.service.setCurrentIndex(remote_idx)
        get_app().processEvents()

        self.assertEqual(self.dialog.provider, 'remote')
        self.assertEqual(self.dialog.endpoint.text(), REMOTE_DEFAULT_ENDPOINT)
        self.assertEqual(self.dialog.model.text(), DEFAULT_OLLAMA_MODEL)
        self.assertEqual(
            resolve_local_chat_model('lan', 'http://10.19.73.160:11434/v1', DEFAULT_OLLAMA_MODEL, True),
            DEFAULT_OLLAMA_VISION_MODEL,
        )
        self.assertEqual(
            resolve_local_chat_model('remote', 'https://abc-gpu.trycloudflare.com/v1', DEFAULT_OLLAMA_MODEL, True),
            DEFAULT_OLLAMA_VISION_MODEL,
        )

    def test_custom_school_api_preset_and_adaptive_theme(self):
        from si_pet.theme import (
            DARK_COLORS,
            LIGHT_COLORS,
            dialog_stylesheet,
            menu_stylesheet,
            panel_stylesheet,
        )

        custom_idx = self.dialog.service.findData('custom')
        self.assertGreaterEqual(custom_idx, 0)
        self.dialog.service.setCurrentIndex(custom_idx)
        get_app().processEvents()

        self.assertEqual(self.dialog.provider, 'custom')
        self.assertEqual(self.dialog.endpoint.text(), '')
        self.assertEqual(self.dialog.model.text(), '')

        dark_panel = panel_stylesheet(True)
        light_panel = panel_stylesheet(False)
        self.assertIn(DARK_COLORS['text'], dark_panel)
        self.assertIn(LIGHT_COLORS['text'], light_panel)
        self.assertNotEqual(DARK_COLORS['text'], LIGHT_COLORS['text'])
        self.assertIn(DARK_COLORS['input_bg'], dialog_stylesheet(True))
        self.assertIn(LIGHT_COLORS['input_bg'], dialog_stylesheet(False))
        self.assertIn(DARK_COLORS['menu_text'], menu_stylesheet(True))
        self.assertIn(LIGHT_COLORS['menu_text'], menu_stylesheet(False))


if __name__ == '__main__':
    unittest.main()

