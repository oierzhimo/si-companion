"""Cross-device multimodal chat session sync tests (P1: sync_v1 + ChatSession + Store + UI)."""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PIL import Image
from si_pet.sync_v1 import Ledger, SyncError, validate_event
from si_pet.chat_backend import ChatSession, prepare_image_thumbnail
from si_pet.storage import Store

try:
    from PySide6.QtWidgets import QApplication
    from si_pet.chat_ui import MultimodalDialog
    HAS_PYSIDE6 = True
except ImportError:
    HAS_PYSIDE6 = False


class TestChatSync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _make_png_bytes(self, color=(40, 120, 220)):
        im = Image.new('RGB', (200, 150), color)
        buf = io.BytesIO()
        im.save(buf, format='PNG')
        return buf.getvalue()

    def test_chat_turn_event_validation_and_causal_order(self):
        pc = Ledger(self.root / 'pc.json', 'pc-device-1')
        phone = Ledger(self.root / 'phone.json', 'android-phone-1')

        thumb = prepare_image_thumbnail(self._make_png_bytes())
        self.assertTrue(thumb.startswith('data:image/jpeg;base64,'))
        self.assertLessEqual(len(thumb), 20000)

        ev1 = phone.add_chat_turn(
            '看看我在手机上拍的画稿',
            '构图很稳，墨色层次也清楚。',
            attachments=[{'kind': 'image', 'name': 'draft.jpg', 'data_url': thumb}],
        )
        validate_event(ev1)

        # Sync phone -> PC
        added = pc.import_bundle(phone.export())
        self.assertEqual(added, 1)

        # PC continues the conversation with voice reply
        ev2 = pc.add_chat_turn(
            '那右下角的留白要补一点吗？',
            '先留着吧，呼吸感刚刚好。',
            attachments=[{'kind': 'voice', 'name': 'reply.wav', 'data_url': ''}],
        )
        validate_event(ev2)

        # Sync PC -> phone
        added_back = phone.import_bundle(pc.export())
        self.assertEqual(added_back, 1)

        pc_turns = pc.chat_turns()
        phone_turns = phone.chat_turns()
        self.assertEqual(pc_turns, phone_turns)
        self.assertEqual(len(pc_turns), 2)
        self.assertEqual(pc_turns[0]['user'], '看看我在手机上拍的画稿')
        self.assertEqual(pc_turns[0]['attachments'][0]['kind'], 'image')
        self.assertEqual(pc_turns[1]['user'], '那右下角的留白要补一点吗？')
        self.assertEqual(pc_turns[1]['attachments'][0]['kind'], 'voice')

        # Existing summary() keys remain untouched
        self.assertEqual(
            set(pc.summary().keys()),
            {'pats', 'snacks', 'focus_sessions', 'affinity', 'journal', 'preferences'},
        )

    def test_chat_turn_rejects_invalid_payloads(self):
        ledger = Ledger(self.root / 'invalid.json', 'pc-device-2')
        with self.assertRaises(SyncError):
            ledger.add_chat_turn('', '回复')
        with self.assertRaises(SyncError):
            ledger.add_chat_turn('你好', '')
        with self.assertRaises(SyncError):
            ledger.add_chat_turn(
                '你好', '在呢',
                attachments=[{'kind': 'video', 'name': 'bad.mp4', 'data_url': ''}],
            )

    def test_store_import_syncs_multimodal_session_and_context(self):
        phone = Ledger(self.root / 'phone-ledger.json', 'android-phone-2')
        thumb = prepare_image_thumbnail(self._make_png_bytes((220, 60, 60)))
        phone.add_chat_turn(
            '手机端发来一张红色色卡',
            '收到这张红色色卡了。',
            attachments=[{'kind': 'image', 'name': 'red-card.jpg', 'data_url': thumb}],
        )

        store = Store(self.root / 'settings.json')
        store.enable_sync_preview()
        added = store.import_sync_bundle(phone.export())
        self.assertEqual(added, 1)

        mm_path = self.root / 'multimodal_conversation.json'
        self.assertTrue(mm_path.exists())
        session = ChatSession(mm_path)
        self.assertEqual(len(session.turns), 1)
        self.assertEqual(session.turns[0]['user'], '手机端发来一张红色色卡')
        self.assertEqual(session.turns[0]['attachments'][0]['name'], 'red-card.jpg')

        # Continuing the conversation includes the synced attachment badge in history
        msgs = session.messages('刚才那张色卡偏暖还是偏冷？')
        user_history_msgs = [m for m in msgs if m['role'] == 'user']
        self.assertIn('[image:red-card.jpg]', user_history_msgs[0]['content'])

        # Idempotent re-sync does not duplicate turns
        self.assertEqual(session.sync_with_ledger(store.sync_ledger), 0)
        self.assertEqual(len(session.turns), 1)

    def test_ui_renders_synced_attachments_and_records_turns(self):
        if not HAS_PYSIDE6:
            self.skipTest('PySide6 not installed')
        app = QApplication.instance() or QApplication(['test', '-platform', 'offscreen'])
        store = Store(self.root / 'settings-ui.json')
        store.enable_sync_preview()
        phone = Ledger(self.root / 'phone-ui.json', 'android-phone-3')
        phone.add_chat_turn(
            '我在路上拍了张晚霞',
            '晚霞的颜色很柔和。',
            attachments=[{'kind': 'image', 'name': 'sunset.jpg', 'data_url': ''}],
        )
        store.import_sync_bundle(phone.export())

        class CtrlStub:
            def __init__(self, s):
                self.store = s
            def say(self, text, flag):
                pass

        dialog = MultimodalDialog(CtrlStub(store), self.root / 'multimodal_conversation.json')
        try:
            text = dialog.transcript.toPlainText()
            self.assertIn('我在路上拍了张晚霞 [image:sunset.jpg]', text)
            self.assertIn('晚霞的颜色很柔和。', text)
        finally:
            dialog.close()


if __name__ == '__main__':
    unittest.main()
