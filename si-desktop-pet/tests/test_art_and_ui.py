"""Regression tests for art, portraits, UI polish, and daily pet behaviors."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

try:
    from PySide6.QtWidgets import QApplication
    from si_pet.chat_ui import MultimodalDialog
    from si_pet.controller import Controller
    from si_pet.panel import Panel
    from si_pet.pet_window import PetWindow
    from si_pet.sprites import Sprites
    from si_pet.storage import Store
    from si_pet.theme import DARK_COLORS, LIGHT_COLORS, panel_stylesheet
    HAS_PYSIDE6 = True
except ImportError:
    HAS_PYSIDE6 = False

_APP = None


def get_app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication(['test', '-platform', 'offscreen'])
    return _APP


class ArtAndBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAS_PYSIDE6:
            raise unittest.SkipTest('PySide6 not installed')
        get_app()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'settings.json')
        self.sprites = Sprites()
        self.ctrl = Controller(self.store, self.sprites)
        self.pet = PetWindow(self.ctrl, lambda: None)
        self.pet.timer.stop()

    def tearDown(self):
        try:
            self.pet.bubble.hide()
            self.pet.hide()
        except Exception:
            pass
        self.tmp.cleanup()

    def test_portraits_and_icon_badge_exist_and_load(self):
        for mood in ('neutral', 'happy', 'blush', 'thinking', 'sleepy', 'troubled'):
            p = self.sprites.portrait_path(mood)
            self.assertIsNotNone(p, mood)
            self.assertTrue(p.is_file(), str(p))
            pix = self.sprites.portrait(mood, 48)
            self.assertFalse(pix.isNull())
        self.assertFalse(self.sprites.icon().isNull())

    def test_idle_breathe_clip_uses_frames_4_and_5(self):
        self.ctrl.state = 'idle'
        self.ctrl.idle_clip = 'breathe'
        self.ctrl.idle_started = 100.0
        self.ctrl.idle_until = 102.0
        seen = set()
        for dt in (0.05, 0.30, 0.56, 0.82, 1.08):
            with patch('si_pet.controller.time.monotonic', return_value=100.0 + dt):
                st, idx = self.ctrl.animation()
                self.assertEqual(st, 'idle')
                seen.add(idx)
        self.assertEqual(seen, {0, 4, 5})

    def test_contact_topics_drive_distinct_poses(self):
        self.ctrl.contact('create')
        self.assertEqual(self.ctrl.action, 'special')
        self.ctrl.contact('stuck')
        self.assertEqual(self.ctrl.action, 'review')
        self.ctrl.contact('tired')
        self.assertEqual(self.ctrl.action, 'sit')

    def test_panel_version_seal_and_presets(self):
        panel = Panel(self.ctrl, self.pet)
        try:
            panel.stack.setCurrentIndex(3)
            self.assertNotIn('互动预览', '\n'.join(w.text() for w in panel.findChildren(type(panel.state_text))))
            floor = next(p for p in self.ctrl.physics.platforms if p.floor)
            self.ctrl.physics.body.x = (floor.left + floor.right) / 2
            self.ctrl.physics.body.y = floor.top
            self.ctrl.physics.body.support = floor.key
            self.ctrl.perform('failed', announce=False)
            self.ctrl.update(0.016)
            self.assertEqual(self.ctrl.state, 'failed')
            self.assertIn(DARK_COLORS['seal_bg'], panel_stylesheet(True))
            self.assertIn(LIGHT_COLORS['gold_accent'], panel_stylesheet(False))
        finally:
            panel.close()

    def test_chat_error_triggers_failed_pose_and_keeps_drawer(self):
        dlg = MultimodalDialog(self.ctrl, self.root / 'mm.json')
        try:
            self.assertTrue(hasattr(dlg, '_drawer'))
            dlg.busy = True
            dlg.active_generation = dlg.generation
            dlg.pending_text = 'test'
            dlg.apply_reply(dlg.generation, '', '模拟异常', None)
            self.assertEqual(self.ctrl.action, 'failed')
        finally:
            dlg.close()


if __name__ == '__main__':
    unittest.main()
