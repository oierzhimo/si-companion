"""Qt interaction tests for explicit quiet requests in the free-chat dialog.

Only complete, affirmative phrasings from the closed local list ("安静陪我",
"安静陪我吧", "先安静陪我一会儿", ...) may act; negated, questioning,
multi-request and multi-sentence messages stay ordinary chat.

These tests drive the real ``ConversationDialog`` and the real ``Controller``
through a real Qt event loop, offscreen.  The controller uses a temporary
Store and a tiny sprite stand-in, so nothing here reads or writes user data.
Network entry points are replaced by recorders: any accidental service call is
visible in ``self.network`` and fails the "no service" assertions.

The quiet action itself is the existing local ``Controller.contact('quiet')``
path.  No real desktop, model, audio device or desktop-pet animation is used or
validated here.
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtCore import QEventLoop, Qt
from PySide6.QtGui import QGuiApplication, QRegion
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from si_pet.companion import CONTACT_OPTIONS, LINES
from si_pet.controller import Controller
from si_pet.conversation import ConversationError
from si_pet.conversation_ui import (
    QUIET_COMMANDS,
    ConversationDialog,
    is_quiet_command,
)
from si_pet.storage import Store

ROOT = Path(__file__).resolve().parents[1]
MOBILE_LINES = (
    ROOT / 'mobile/android/app/src/main/assets/lines.json',
    ROOT / 'mobile/harmony/entry/src/main/resources/rawfile/lines.json',
)
QUIET_LINE = '好，我不说话。你忙你的，回头看我一眼就好。'
OLD_HEARING_LINE = '好，不说话。有事就敲一下桌面，我听得见。'
NORMAL_REPLY = '普通回应'

_QT_APP = None


def qt_application():
    """One QApplication for the whole test process (Qt cannot recreate it)."""
    global _QT_APP
    if _QT_APP is None:
        _QT_APP = QApplication.instance() or QApplication([])
    return _QT_APP


class FakeSprites:
    """Only the pieces Controller's startup/clamp needs."""

    def __init__(self):
        names = (
            'idle', 'jumping', 'walk-right', 'walk-left', 'sitting',
            'sleeping', 'waving', 'running', 'review', 'special',
        )
        self.frames = {name: [0] for name in names}
        self.durations = {name: [100] for name in names}
        self._mask = QRegion(0, 0, 192, 208)

    def mask(self, state, index, scale):
        return QRegion(self._mask)

    def index(self, state, elapsed_ms):
        return 0


class ConversationQuietTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_application()

    def setUp(self):
        qt_application()
        self.tmp = tempfile.TemporaryDirectory(prefix='si-quiet-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'settings.json')
        self.store['scale'] = 1.0
        self.store['bottom_offset'] = 0
        self.store['bubbles'] = True
        self.store['quiet_mode'] = False
        self.store['wander'] = True
        with patch('si_pet.controller.native.window_platforms', return_value=[]):
            self.ctrl = Controller(self.store, FakeSprites())
        # Keep simulated frames from writing settings while these tests run.
        self.ctrl.last_save = time.monotonic() + 3600

        self.speech = []
        self.voice = []
        self.replies = []
        self.ctrl.speech.connect(self.speech.append)
        self.ctrl.voice_speech.connect(self.voice.append)
        self.ctrl.contact_reply.connect(self.replies.append)

        self.network = []
        self.reply_patcher = patch(
            'si_pet.conversation_ui.request_reply',
            side_effect=self.fake_reply,
        )
        self.reply_patcher.start()
        self.addCleanup(self.reply_patcher.stop)
        self.models_patcher = patch(
            'si_pet.conversation_ui.fetch_models_request',
            side_effect=self.fake_models,
        )
        self.models_patcher.start()
        self.addCleanup(self.models_patcher.stop)

        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            try:
                dialog.completed.disconnect()
            except (RuntimeError, TypeError):
                pass
            try:
                dialog.close()
            except RuntimeError:
                pass
            import shiboken6
            try:
                shiboken6.delete(dialog)
            except RuntimeError:
                pass
        self.dialogs.clear()
        self.pump(0.05)

    # -- stubs and event-loop helpers --------------------------------------

    def fake_reply(self, endpoint, model, messages, api_key=None):
        self.network.append(('reply', endpoint, model, list(messages)))
        return NORMAL_REPLY

    def fake_models(self, endpoint, api_key=None):
        self.network.append(('models', endpoint))
        return []

    def pump(self, seconds=0.0):
        deadline = time.monotonic() + seconds
        while True:
            self.app.processEvents(QEventLoop.AllEvents, 20)
            if time.monotonic() >= deadline:
                break
            time.sleep(0.005)

    @property
    def app(self):
        return qt_application()

    def wait_until(self, predicate, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents(QEventLoop.AllEvents, 20)
            if predicate():
                return True
            time.sleep(0.005)
        self.app.processEvents(QEventLoop.AllEvents, 20)
        return bool(predicate())

    def make_dialog(self, filename='conversation.json'):
        dialog = ConversationDialog(self.ctrl, self.root / filename)
        # Ordinary loopback preset: model/endpoint fields stay usable, but the
        # patched request_reply recorder means no real connection is possible.
        dialog.service.setCurrentIndex(0)
        dialog.endpoint.setText('http://127.0.0.1:1234/v1')
        dialog.model.setText('quiet-stub-model')
        self.dialogs.append(dialog)
        return dialog

    def reset_action_records(self):
        self.speech.clear()
        self.voice.clear()
        self.replies.clear()

    def assert_quiet_dock_state(self):
        self.assertTrue(self.store['quiet_mode'])
        self.assertTrue(self.ctrl.docked)
        self.assertTrue(self.ctrl.quiet_dock)
        self.assertEqual(self.ctrl.action, 'sit')
        self.assertFalse(self.ctrl.sleeping)
        self.assertEqual(self.ctrl.override_until, float('inf'))
        screen = QGuiApplication.primaryScreen().availableGeometry()
        body = self.ctrl.physics.body
        self.assertAlmostEqual(body.x, screen.right() - 96 * 1.0 - 8, places=3)
        self.assertAlmostEqual(
            body.y,
            screen.bottom() - self.store['bottom_offset'] - 6 * 1.0,
            places=3,
        )

    def assert_single_announcement(self):
        # contact('quiet') itself provides one reply and one speech request.
        # A dialog-level extra ctrl.say would make these counts two.
        self.assertEqual(len(self.replies), 1)
        self.assertEqual(len(self.speech), 1)
        self.assertEqual(len(self.voice), 1)
        self.assertIn(self.replies[0], LINES['quiet'])
        self.assertEqual(self.speech[0], self.replies[0])
        self.assertEqual(self.voice[0], self.replies[0])

    # -- in-flight reply helpers ------------------------------------------

    def held_reply(self, started, release, calls):
        """A request_reply stub that stays "on the network" until released."""
        def fake(endpoint, model, messages, api_key=None):
            calls.append(list(messages))
            started.set()
            if not release.wait(5.0):
                raise ConversationError('测试没有释放等待中的回复')
            return NORMAL_REPLY
        return fake

    def start_held_request(self, dialog, text):
        started = threading.Event()
        release = threading.Event()
        calls = []
        patcher = patch(
            'si_pet.conversation_ui.request_reply',
            side_effect=self.held_reply(started, release, calls),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        # Unblock the worker before its patcher is removed, even if an
        # assertion fails before the test releases it itself.
        self.addCleanup(release.set)
        dialog.input.setPlainText(text)
        dialog.send.click()
        self.assertTrue(started.wait(2.0), '对话请求没有开始')
        self.pump(0.02)
        return release, calls

    def wait_for_chat_workers(self, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents(QEventLoop.AllEvents, 20)
            if not any(t.name == 'si-local-chat' for t in threading.enumerate()):
                return True
            time.sleep(0.005)
        return False

    # -- explicit button ---------------------------------------------------

    def test_real_button_click_enters_quiet_dock_without_network(self):
        dialog = self.make_dialog()
        dialog.show()
        self.pump(0.03)
        self.assertEqual(dialog.quiet_button.text(), '安静陪我')
        # Enter in the input must not silently trigger the quiet action.
        self.assertFalse(dialog.quiet_button.autoDefault())

        QTest.mouseClick(dialog.quiet_button, Qt.MouseButton.LeftButton)
        self.pump(0.05)

        self.assertEqual(self.network, [], 'the quiet button contacted a service')
        self.assert_quiet_dock_state()
        self.assert_single_announcement()
        self.assertIn('本机操作', dialog.status.text())
        self.assertIn('没有联网', dialog.status.text())
        self.assertFalse(dialog.busy)
        self.assertTrue(dialog.send.isEnabled())
        self.assertEqual(dialog.session.turns, [])

    def test_button_leaves_an_unrelated_draft_untouched(self):
        dialog = self.make_dialog()
        dialog.input.setPlainText('这是一段还没发送的草稿')

        dialog.quiet_button.click()
        self.pump(0.03)

        self.assertEqual(dialog.input.toPlainText(), '这是一段还没发送的草稿')
        self.assertEqual(self.network, [])
        self.assert_quiet_dock_state()
        self.assert_single_announcement()

    # -- exact commands ----------------------------------------------------

    def test_exact_affirmative_commands_run_locally(self):
        accepted = (
            '安静陪我',
            '先安静陪我',
            '  先 安静 陪我。 ',
            '安静\t陪我',
            '安静陪我吧',
            '先安静陪我一会儿',
            '安静陪我一会',
            '安静陪我一会儿吧',
            '  先 安静 陪我 一会儿。 ',
        )
        for text in accepted:
            with self.subTest(text=text):
                self.assertTrue(is_quiet_command(text))
                # Fresh action state for each variant.
                self.store['quiet_mode'] = False
                self.ctrl.docked = False
                self.ctrl.quiet_dock = False
                self.ctrl.override_until = 0
                self.reset_action_records()
                self.network.clear()

                dialog = self.make_dialog()
                dialog.input.setPlainText(text)
                dialog.send.click()
                self.pump(0.03)

                self.assertEqual(
                    self.network, [], f'{text!r} unexpectedly used a service'
                )
                self.assert_quiet_dock_state()
                self.assert_single_announcement()
                self.assertEqual(dialog.input.toPlainText(), '')
                self.assertEqual(dialog.session.turns, [])
                self.assertIn('已安静陪你', dialog.status.text())

    def test_everyday_quiet_phrasings_run_from_the_real_send_button(self):
        accepted = (
            '安静陪我吧',
            '先安静陪我一会儿',
            '安静陪我一会',
            '安静陪我一会儿吧',
            '先安静陪我吧',
            '先安静陪我一会',
            '安静陪我一会吧',
            '先安静陪我一会吧',
            '安静陪我一会儿',
            '先安静陪我一会儿吧',
            ' 安静 陪我 ！ ',
            '先安静陪我一会儿。',
        )
        for text in accepted:
            with self.subTest(text=text):
                self.store['quiet_mode'] = False
                self.ctrl.docked = False
                self.ctrl.quiet_dock = False
                self.ctrl.override_until = 0
                self.reset_action_records()
                self.network.clear()

                dialog = self.make_dialog()
                dialog.show()
                self.pump(0.02)
                dialog.input.setPlainText(text)
                QTest.mouseClick(dialog.send, Qt.MouseButton.LeftButton)
                self.pump(0.03)

                self.assertEqual(
                    self.network, [], f'{text!r} unexpectedly used a service'
                )
                self.assert_quiet_dock_state()
                self.assert_single_announcement()
                self.assertEqual(dialog.input.toPlainText(), '')
                self.assertEqual(dialog.session.turns, [])
                self.assertIn('已安静陪你', dialog.status.text())

    def test_exact_command_needs_no_usable_service_settings(self):
        for text in ('安静陪我', '安静陪我吧', '先安静陪我一会儿！'):
            with self.subTest(text=text):
                self.store['quiet_mode'] = False
                self.ctrl.docked = False
                self.ctrl.quiet_dock = False
                self.ctrl.override_until = 0
                self.reset_action_records()
                self.network.clear()

                dialog = self.make_dialog()
                # Deliberately unusable connection fields: a chat send would
                # be rejected before starting, while the local quiet action
                # must still run.
                dialog.endpoint.setText('')
                dialog.model.clear()
                dialog.input.setPlainText(text)
                dialog.send.click()
                self.pump(0.03)

                self.assertEqual(self.network, [])
                self.assert_quiet_dock_state()
                self.assert_single_announcement()
                self.assertIn('已安静陪你', dialog.status.text())
                self.assertIn('没有联网', dialog.status.text())

    # -- a quiet action supersedes an in-flight reply -----------------------

    def test_explicit_command_abandons_in_flight_reply_without_blocking_input(self):
        dialog = self.make_dialog()
        release, calls = self.start_held_request(dialog, '先说一句普通的话')
        self.assertTrue(dialog.busy)
        self.assertFalse(dialog.send.isEnabled())
        self.assertEqual(dialog.session.turns, [])
        self.assertEqual(len(calls), 1)

        # The explicit command is handled even though a reply is pending.
        dialog.input.setPlainText('安静陪我')
        dialog.submit()
        self.pump(0.03)

        self.assert_quiet_dock_state()
        self.assert_single_announcement()
        self.assertFalse(dialog.busy)
        self.assertTrue(dialog.send.isEnabled())
        self.assertEqual(dialog.input.toPlainText(), '')
        self.assertEqual(dialog.session.turns, [])
        self.assertEqual(len(calls), 1, 'the quiet action contacted the service')
        self.assertIn('已安静陪你', dialog.status.text())
        self.assertIn('作废', dialog.status.text())
        quiet_replies = list(self.replies)

        # Send is usable again at once: a new request can be in flight while
        # the abandoned one is still on the network.
        dialog.input.setPlainText('安静之后的新一句')
        dialog.send.click()
        self.pump(0.02)
        self.assertTrue(dialog.busy)
        self.assertTrue(self.wait_until(lambda: len(calls) == 2, 2.0),
                        'the second request never started')
        dialog.input.setPlainText('等待新回复时保留的草稿')

        release.set()
        self.assertTrue(self.wait_for_chat_workers(), '迟到的回复没有返回')
        self.assertTrue(self.wait_until(lambda: not dialog.busy, 2.0))
        self.pump(0.05)

        # Only the new request is committed; the abandoned reply is inert.
        self.assertEqual(len(dialog.session.turns), 1)
        self.assertEqual(dialog.session.turns[0]['user'], '安静之后的新一句')
        self.assertEqual(dialog.session.turns[0]['assistant'], NORMAL_REPLY)
        self.assertEqual(self.replies, quiet_replies)
        self.assertEqual(self.speech.count(NORMAL_REPLY), 1)
        self.assertEqual(self.voice.count(NORMAL_REPLY), 1)
        self.assertEqual(dialog.input.toPlainText(), '等待新回复时保留的草稿')
        self.assertTrue(dialog.send.isEnabled())

    def test_new_phrasing_abandons_in_flight_reply_and_keeps_later_draft(self):
        dialog = self.make_dialog()
        release, calls = self.start_held_request(dialog, '先说一句普通的话')
        self.assertTrue(dialog.busy)
        self.assertFalse(dialog.send.isEnabled())

        dialog.input.setPlainText('安静陪我吧')
        # Send is disabled by the pending request, so exercise the same slot
        # directly; this also proves the quiet branch runs before the busy guard.
        dialog.submit()
        self.pump(0.03)

        # Still the one local action: no service call, one announcement, and
        # the older reply is abandoned rather than committed later.
        self.assertEqual(self.network, [])
        self.assertEqual(len(calls), 1, 'the quiet action contacted the service')
        self.assert_quiet_dock_state()
        self.assert_single_announcement()
        self.assertFalse(dialog.busy)
        self.assertTrue(dialog.send.isEnabled())
        self.assertEqual(dialog.input.toPlainText(), '')
        self.assertEqual(dialog.session.turns, [])
        self.assertIn('作废', dialog.status.text())
        status_before = dialog.status.text()
        replies_before = list(self.replies)
        speech_before = list(self.speech)
        voice_before = list(self.voice)

        # A draft typed after the local action must survive the late reply.
        dialog.input.setPlainText('动作之后新写的未发送草稿')
        release.set()
        self.assertTrue(self.wait_for_chat_workers(), '迟到的回复没有返回')
        self.pump(0.05)

        self.assertEqual(dialog.input.toPlainText(), '动作之后新写的未发送草稿')
        self.assertEqual(dialog.session.turns, [])
        self.assertEqual(self.replies, replies_before)
        self.assertEqual(self.speech, speech_before)
        self.assertEqual(self.voice, voice_before)
        self.assertEqual(dialog.status.text(), status_before)
        self.assertFalse(dialog.busy)
        self.assertTrue(dialog.send.isEnabled())

    def test_quiet_button_abandons_in_flight_reply_and_keeps_draft(self):
        dialog = self.make_dialog()
        release, calls = self.start_held_request(dialog, '已经发出去的一句话')
        dialog.input.setPlainText('还没发送的草稿')

        dialog.quiet_button.click()
        self.pump(0.03)

        self.assert_quiet_dock_state()
        self.assert_single_announcement()
        self.assertFalse(dialog.busy)
        self.assertTrue(dialog.send.isEnabled())
        self.assertEqual(dialog.input.toPlainText(), '还没发送的草稿')
        self.assertEqual(dialog.session.turns, [])
        self.assertEqual(len(calls), 1, 'the quiet action contacted the service')
        self.assertIn('作废', dialog.status.text())
        status_before = dialog.status.text()
        replies_before = list(self.replies)
        speech_before = list(self.speech)
        voice_before = list(self.voice)

        # The result is still on the network.  When it arrives it must not
        # write history, speak, change the status or clear the draft.
        release.set()
        self.assertTrue(self.wait_for_chat_workers(), '迟到的回复没有返回')
        self.pump(0.05)

        self.assertEqual(dialog.session.turns, [])
        self.assertEqual(self.replies, replies_before)
        self.assertEqual(self.speech, speech_before)
        self.assertEqual(self.voice, voice_before)
        self.assertEqual(dialog.status.text(), status_before)
        self.assertEqual(dialog.input.toPlainText(), '还没发送的草稿')
        self.assertFalse(dialog.busy)
        self.assertTrue(dialog.send.isEnabled())

    def test_failed_quiet_action_leaves_the_pending_reply_alive(self):
        dialog = self.make_dialog()
        release, calls = self.start_held_request(dialog, '这句话还在等回复')
        dialog.input.setPlainText('另一个还没发送的草稿')

        with patch.object(
            self.ctrl, 'contact', side_effect=RuntimeError('模拟安静失败'),
        ):
            dialog.quiet_button.click()
            self.pump(0.03)

        # A failed action must not abandon the request that is already out.
        self.assertFalse(self.store['quiet_mode'])
        self.assertFalse(self.ctrl.docked)
        self.assertFalse(self.ctrl.quiet_dock)
        self.assertTrue(dialog.busy)
        self.assertFalse(dialog.send.isEnabled())
        self.assertIn('未能生效', dialog.status.text())
        self.assertEqual(dialog.session.turns, [])
        self.assertEqual(dialog.input.toPlainText(), '另一个还没发送的草稿')
        self.assertEqual(len(calls), 1)

        release.set()
        self.assertTrue(self.wait_for_chat_workers(), '原请求的回复没有返回')
        self.assertTrue(self.wait_until(lambda: not dialog.busy, 2.0))
        self.pump(0.05)

        # The original request still lands normally.
        self.assertEqual(len(dialog.session.turns), 1)
        self.assertEqual(dialog.session.turns[0]['user'], '这句话还在等回复')
        self.assertEqual(dialog.session.turns[0]['assistant'], NORMAL_REPLY)
        self.assertEqual(self.speech, [NORMAL_REPLY])
        self.assertEqual(self.voice, [NORMAL_REPLY])
        self.assertEqual(dialog.input.toPlainText(), '另一个还没发送的草稿')
        self.assertTrue(dialog.send.isEnabled())

    def test_closed_command_set_tolerates_only_space_and_one_ending(self):
        self.assertEqual(
            QUIET_COMMANDS,
            (
                '安静陪我',
                '先安静陪我',
                '安静陪我吧',
                '先安静陪我吧',
                '安静陪我一会',
                '先安静陪我一会',
                '安静陪我一会吧',
                '先安静陪我一会吧',
                '安静陪我一会儿',
                '先安静陪我一会儿',
                '安静陪我一会儿吧',
                '先安静陪我一会儿吧',
            ),
        )
        self.assertEqual(len(QUIET_COMMANDS), len(set(QUIET_COMMANDS)))
        self.assertTrue(all(isinstance(command, str) and command
                            for command in QUIET_COMMANDS))
        for text in (
            '安静陪我',
            '安静陪我。',
            '安静陪我.',
            '安静陪我．',
            ' 安静 陪我 。 ',
            '先安静陪我。',
            '先　安静\t陪我',
            '安静陪我！',
            '安静陪我!',
            '安静陪我吧。',
            '安静陪我吧！',
            '先安静陪我一会儿。',
            '安静陪我一会！',
            '安静陪我一会儿吧. ',
            '先安静陪我一会儿吧！',
        ):
            with self.subTest(text=text):
                self.assertTrue(is_quiet_command(text))

    def test_negated_questioning_or_qualified_text_never_acts(self):
        rejected = (
            '',
            '不要安静陪我',
            '别安静陪我',
            '先别安静陪我',
            '不，安静陪我',
            '不想安静陪我',
            '安静陪我吗',
            '安静陪我吗？',
            '安静陪我？',
            '安静陪我。？',
            '安静陪我吧？',
            '安静陪我一会？',
            '安静陪我一会儿吗',
            '你能不能安静陪我',
            '可以安静陪我吗',
            '安静陪我。好吗',
            '安静陪我吧。好吗',
            '安静陪我。。',
            '安静陪我。！',
            '安静陪我！！',
            '安静陪我！？',
            '安静陪我！。',
            '安静陪我 。 ！ ',
            '安静陪我。谢谢',
            '安静陪我吧，谢谢',
            '安静陪我吧。然后帮我开程序',
            '安静陪我然后帮我开程序',
            '安静陪我吧然后帮我开程序',
            '先安静陪我一会儿然后开程序',
            '先安静陪我一会儿。谢谢',
            '好，安静陪我',
            '先安静陪我一下',
            '安静陪伴我一会',
            '安静陪我u3000',  # no fuzzy stripping of unrelated characters
        )
        for text in rejected:
            with self.subTest(text=text):
                self.assertFalse(is_quiet_command(text))

    def test_negated_and_questioning_messages_use_normal_chat_path(self):
        dialog = self.make_dialog()
        dialog.show()
        self.pump(0.02)
        cases = (
            '不要安静陪我',
            '别安静陪我',
            '安静陪我吗？',
            '安静陪我吧？',
            '安静陪我然后帮我开程序',
            '安静陪我。好吗',
            '安静陪我。。',
            '安静陪伴我一会',
        )
        for text in cases:
            with self.subTest(text=text):
                self.store['quiet_mode'] = False
                self.ctrl.docked = False
                self.ctrl.quiet_dock = False
                self.reset_action_records()
                self.network.clear()

                dialog.input.setPlainText(text)
                QTest.mouseClick(dialog.send, Qt.MouseButton.LeftButton)
                self.assertTrue(
                    self.wait_until(lambda: not dialog.busy, 2.0),
                    'ordinary reply did not finish',
                )
                self.pump(0.03)

                self.assertEqual(len(self.network), 1)
                self.assertFalse(self.store['quiet_mode'])
                self.assertFalse(self.ctrl.docked)
                self.assertFalse(self.ctrl.quiet_dock)
                self.assertEqual(self.replies, [])
                # The normal path still announces its own reply once.
                self.assertEqual(self.speech, [NORMAL_REPLY])
                self.assertEqual(self.voice, [NORMAL_REPLY])
                self.assertEqual(dialog.session.turns[-1]['assistant'], NORMAL_REPLY)

    # -- construction / no network ----------------------------------------

    def test_constructing_dialog_and_quiet_action_start_no_worker(self):
        before = {t.name for t in threading.enumerate()}
        dialog = self.make_dialog()
        self.pump(0.05)
        self.assertEqual(self.network, [])
        self.assertEqual(dialog.session.turns, [])
        self.assertFalse(
            any(t.name in {'si-local-chat', 'si-models'} for t in threading.enumerate()
                if t.name not in before),
            'constructing the dialog started a network worker',
        )

        dialog.quiet_button.click()
        self.pump(0.05)
        self.assertEqual(self.network, [])
        self.assertFalse(
            any(t.name in {'si-local-chat', 'si-models'}
                for t in threading.enumerate() if t.name not in before),
            'the quiet action started a network worker',
        )
        self.assert_single_announcement()

    # -- preserved behavior ------------------------------------------------

    def test_normal_message_still_uses_network_and_failure_keeps_input(self):
        dialog = self.make_dialog()
        dialog.input.setPlainText('一句普通的话')
        dialog.send.click()
        self.assertTrue(self.wait_until(lambda: not dialog.busy, 2.0))
        self.pump(0.03)

        self.assertEqual(len(self.network), 1)
        kind, endpoint, model, messages = self.network[0]
        self.assertEqual(kind, 'reply')
        self.assertEqual(endpoint, 'http://127.0.0.1:1234/v1')
        self.assertEqual(model, 'quiet-stub-model')
        self.assertEqual(messages[-1], {'role': 'user', 'content': '一句普通的话'})
        self.assertEqual(dialog.session.turns[-1]['assistant'], NORMAL_REPLY)
        self.assertFalse(self.store['quiet_mode'])
        self.assertFalse(self.ctrl.docked)

        failed = self.make_dialog('failed-conversation.json')
        failed.input.setPlainText('失败时也要留住这句话')
        with patch(
            'si_pet.conversation_ui.request_reply',
            side_effect=ConversationError('模拟服务不可用'),
        ):
            failed.send.click()
            self.assertTrue(self.wait_until(lambda: not failed.busy, 2.0))
            self.pump(0.03)
        self.assertEqual(failed.input.toPlainText(), '失败时也要留住这句话')
        self.assertEqual(failed.session.turns, [])
        self.assertFalse(self.store['quiet_mode'])
        self.assertIn('模拟服务不可用', failed.status.text())

    def test_model_reply_that_looks_like_the_command_cannot_act(self):
        for index, phrase in enumerate((
            '安静陪我。',
            '安静陪我吧',
            '先安静陪我一会儿！',
        )):
            with self.subTest(phrase=phrase):
                dialog = self.make_dialog(f'reply-{index}.json')
                dialog.input.setPlainText('随便聊一句')
                with patch(
                    'si_pet.conversation_ui.request_reply',
                    return_value=phrase,
                ):
                    dialog.send.click()
                    self.assertTrue(self.wait_until(lambda: not dialog.busy, 2.0))
                    self.pump(0.03)

                # The reply is only displayed/saved; never parsed for actions.
                self.assertFalse(self.store['quiet_mode'])
                self.assertFalse(self.ctrl.docked)
                self.assertFalse(self.ctrl.quiet_dock)
                self.assertEqual(self.replies, [])
                self.assertEqual(dialog.session.turns[-1]['assistant'], phrase)

    def test_existing_contact_quiet_and_other_topics_still_work(self):
        self.reset_action_records()
        reply = self.ctrl.contact('quiet')
        self.assertIn(reply, LINES['quiet'])
        self.assert_quiet_dock_state()
        self.assert_single_announcement()
        self.assertTrue(any(topic == 'quiet' for _, topic in CONTACT_OPTIONS))

        # A different explicit topic keeps its original non-quiet behavior.
        self.store['quiet_mode'] = False
        self.ctrl.docked = False
        self.ctrl.quiet_dock = False
        self.ctrl.override_until = 0
        self.reset_action_records()
        tired = self.ctrl.contact('tired')
        self.assertIn(tired, LINES['tired'])
        self.assertFalse(self.store['quiet_mode'])
        self.assertFalse(self.ctrl.docked)
        self.assertFalse(self.ctrl.quiet_dock)
        self.assertEqual(self.replies, [tired])
        self.assertEqual(self.speech, [tired])

    # -- three-platform text ----------------------------------------------

    def test_quiet_line_is_identical_on_windows_android_and_harmony(self):
        self.assertIn(QUIET_LINE, LINES['quiet'])
        self.assertNotIn(OLD_HEARING_LINE, LINES['quiet'])
        self.assertNotIn('听得见', QUIET_LINE)
        for path in MOBILE_LINES:
            with self.subTest(path=str(path)):
                data = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(data['quiet'], LINES['quiet'])
                self.assertEqual(data['quiet'].count(QUIET_LINE), 1)
                self.assertNotIn(OLD_HEARING_LINE, data['quiet'])


if __name__ == '__main__':
    unittest.main()
