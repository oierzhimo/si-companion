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
        self.assertIn('0 张', self.dialog.image_count_label.text())

    def test_voice_mode_toggle(self):
        self.assertFalse(self.dialog.voice_mode)
        self.dialog.voice_mode = True
        self.assertTrue(self.dialog.voice_mode)
        self.dialog.voice_mode = False
        self.assertFalse(self.dialog.voice_mode)

    def test_clear_images(self):
        self.dialog.pending_images = [b'fake-image-1', b'fake-image-2']
        self.dialog._update_image_label()
        self.assertIn('2 张', self.dialog.image_count_label.text())

        self.dialog.clear_images()
        self.assertEqual(len(self.dialog.pending_images), 0)
        self.assertIn('0 张', self.dialog.image_count_label.text())

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
        self.assertIn('请输入文字或添加图片', self.dialog.status.text())
        self.assertFalse(self.dialog.busy)

    def test_render_escapes_or_plain_text(self):
        self.dialog.session.turns = [{'user': '<b>粗体</b>', 'assistant': '<i>斜体</i>'}]
        self.dialog.render()
        text = self.dialog.transcript.toPlainText()
        self.assertIn('管理员：<b>粗体</b>', text)
        self.assertIn('祀：<i>斜体</i>', text)


if __name__ == '__main__':
    unittest.main()
