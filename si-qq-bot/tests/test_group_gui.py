"""Offscreen tests for the persisted group switch and local group recovery.

No real adapter, model, QQ process, credential, or user data is used.
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QEvent, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import main  # noqa: E402
from core import QQCore  # noqa: E402
from group_core import GroupChat  # noqa: E402


class FakeLease:
    def __init__(self):
        self.released = False

    def release(self):
        self.released = True


class FakeGroupCore:
    def __init__(self, pending=True):
        self.pending = bool(pending)
        self.discard_calls = 0
        self.discard_result = True
        self.discard_error = None

    def local_status(self):
        return {'pending_recovery_required': self.pending}

    def discard_uncertain_group_pending(self):
        self.discard_calls += 1
        if self.discard_error is not None:
            raise self.discard_error
        if not self.discard_result:
            return False
        self.pending = False
        return True


class QuietAdapter:
    def __init__(self):
        self.stopped = False

    def is_quiescent(self):
        return True

    def stop(self, *args, **kwargs):
        self.stopped = True
        return True


class GroupGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp_root = ROOT / 'qa-test-temp'
        temp_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=str(temp_root))
        self.addCleanup(self.temp.cleanup)
        self.env = mock.patch.dict(
            os.environ, {'LOCALAPPDATA': self.temp.name}
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.window = self.make_window()

    def make_window(self):
        window = main.SiQQWindow()
        window.show()
        self.app.processEvents()
        self.addCleanup(self.dispose, window)
        return window

    def dispose(self, window):
        window._adapter = None
        window._adapter_generation = None
        window._connected = False
        window._bound = False
        window._recovery = False
        window._close_requested = False
        window.shutdown(timeout=1.0)
        window.close()
        self.app.processEvents()
        window.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def switch_path(self):
        return (
            Path(self.temp.name) / 'SiCompanion' / 'QQPrivateChat'
            / main.GROUP_SWITCH_FILENAME
        )

    def write_switch_bytes(self, raw):
        path = self.switch_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def install_offline_group_recovery(self, *, pending=True):
        group = FakeGroupCore(pending)
        lease = FakeLease()
        window = self.window
        window._adapter = None
        window._adapter_generation = None
        window._stop_thread = None
        window._stopping_adapter = None
        window._core = None
        window._group_core = group
        window._connected = False
        window._recovering = False
        window._data_lease = lease
        window.group_recovery_button.setText('恢复群聊未确认回复（已断开）')
        window.group_recovery_button.setEnabled(True)
        return group, lease

    # -- switch --------------------------------------------------------

    def test_window_identity_and_private_group_scope_are_visible(self):
        self.assertEqual(self.window.windowTitle(), '小祀 · QQ 陪伴')
        self.assertEqual(main.APP_TITLE, '小祀 · QQ 陪伴')
        self.assertIn('0.2.5', self.window.version_label.text())
        intro = self.window.intro_label.text()
        self.assertIn('私聊', intro)
        self.assertIn('群聊', intro)
        self.assertIn('明确 @', intro)
        self.assertIn('分别独立保存', intro)
        privacy = self.window.privacy_label.text()
        self.assertIn('私聊记录与群聊记录也分别独立保存', privacy)
        self.assertIn('管理员偏好、私聊内容和私聊记录', privacy)
        self.assertIn('仅在群聊中被明确 @', self.window.group_switch.text())

    def test_switch_defaults_on_and_creates_no_file_before_a_choice(self):
        self.assertTrue(self.window.group_switch.isChecked())
        self.assertTrue(self.window._group_reply_enabled)
        self.assertFalse(self.switch_path().exists())
        self.assertIn('默认开启', self.window.group_switch_status.text())
        self.assertIn('尚未保存选择', self.window.group_switch_status.text())
        self.assertFalse(
            (Path(self.temp.name) / 'SiCompanion' / 'QQPrivateChat').exists()
        )

    def test_small_screen_controls_remain_reachable_by_scrolling(self):
        self.window.resize(560, 440)
        self.window.show()
        self.app.processEvents()
        scroll = self.window.content_scroll
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        scroll.ensureWidgetVisible(self.window.group_switch)
        self.app.processEvents()
        pos = self.window.group_switch.mapTo(scroll.viewport(), self.window.group_switch.rect().center())
        self.assertTrue(scroll.viewport().rect().contains(pos))

    def test_switch_choice_is_persisted_and_reloaded(self):
        self.window.group_switch.setChecked(False)
        self.assertFalse(self.window._group_reply_enabled)
        self.assertIn('已保存为关闭', self.window.group_switch_status.text())
        saved = json.loads(self.switch_path().read_text(encoding='utf-8'))
        self.assertEqual(saved, {'schema': 1, 'enabled': False})

        restarted = self.make_window()
        self.assertFalse(restarted.group_switch.isChecked())
        self.assertFalse(restarted._group_reply_enabled)
        self.assertIn('已保存为关闭', restarted.group_switch_status.text())

        restarted.group_switch.setChecked(True)
        self.assertIn('已保存为开启', restarted.group_switch_status.text())
        third = self.make_window()
        self.assertTrue(third.group_switch.isChecked())
        self.assertTrue(third._group_reply_enabled)

    def test_failed_switch_save_reverts_visible_control(self):
        with mock.patch.object(
                self.window, '_ensure_data_lease', return_value=False):
            self.window.group_switch.setChecked(False)
        self.assertTrue(self.window.group_switch.isChecked())
        self.assertTrue(self.window._group_reply_enabled)
        self.assertFalse(self.switch_path().exists())
        self.assertIn('无法保存', self.window.status_label.text())

    # -- corrupt/unreadable switch fails closed -----------------------

    def test_corrupt_switch_fails_closed_and_preserves_bytes(self):
        original = b'{"schema":1,"enabled":'
        path = self.write_switch_bytes(original)
        window = self.make_window()

        self.assertFalse(window._group_reply_enabled)
        self.assertFalse(window.group_switch.isChecked())
        self.assertFalse(window.group_switch.isEnabled())
        self.assertTrue(window.group_switch_reset_button.isEnabled())
        self.assertIn('已关闭群聊 @ 回复', window.group_switch_status.text())
        self.assertIn('原文件', window.group_switch_status.text())
        # Private chat is not blocked by the local group setting.
        self.assertTrue(window.connect_button.isEnabled())
        self.assertFalse(window._recovery)
        self.assertIsNone(window._group_core)

        # A stale programmatic toggle must not enable the feature or rewrite
        # the unusable file.
        window.group_switch.setChecked(True)
        self.assertFalse(window.group_switch.isChecked())
        self.assertFalse(window._group_reply_enabled)
        self.assertEqual(path.read_bytes(), original)
        self.assertIn('已关闭群聊 @ 回复', window.status_label.text())

    def test_unreadable_switch_fails_closed_with_fixed_message(self):
        with mock.patch.object(
                main.SiQQWindow, '_read_group_switch_file',
                side_effect=OSError('synthetic permission failure')):
            window = self.make_window()
        self.assertFalse(window._group_reply_enabled)
        self.assertFalse(window.group_switch.isChecked())
        self.assertFalse(window.group_switch.isEnabled())
        self.assertIn('无法读取', window.group_switch_status.text())
        self.assertIn('原文件', window.group_switch_status.text())

    def test_corrupt_switch_explicit_reset_then_normal_toggle(self):
        original = b'not-json-at-all'
        path = self.write_switch_bytes(original)
        window = self.make_window()
        with mock.patch.object(
                main.QMessageBox, 'question',
                return_value=main.QMessageBox.Yes):
            window._reset_group_switch()

        saved = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(saved, {'schema': 1, 'enabled': False})
        self.assertTrue(window.group_switch.isEnabled())
        self.assertFalse(window.group_switch_reset_button.isEnabled())
        self.assertIn('已保存为关闭', window.group_switch_status.text())
        self.assertIn('已重置为关闭', window.status_label.text())

        # Once the file is valid, a normal explicit choice is persisted.
        window.group_switch.setChecked(True)
        self.assertTrue(window.group_switch.isChecked())
        self.assertTrue(window._group_reply_enabled)
        self.assertEqual(
            json.loads(path.read_text(encoding='utf-8')),
            {'schema': 1, 'enabled': True},
        )

    def test_corrupt_switch_reset_cancel_preserves_bytes(self):
        original = b'not-json-at-all'
        path = self.write_switch_bytes(original)
        window = self.make_window()
        with mock.patch.object(
                main.QMessageBox, 'question',
                return_value=main.QMessageBox.No):
            window._reset_group_switch()
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(window.group_switch.isEnabled())
        self.assertIn('未重置', window.status_label.text())

    def test_externally_changed_switch_is_not_overwritten(self):
        self.window.group_switch.setChecked(False)
        path = self.switch_path()
        self.assertEqual(
            json.loads(path.read_text(encoding='utf-8')),
            {'schema': 1, 'enabled': False},
        )
        # Another program changes the setting after this window loaded it.
        path.write_text(
            json.dumps({'schema': 1, 'enabled': True}), encoding='utf-8'
        )
        before = path.read_bytes()
        self.window.group_switch.setChecked(True)
        self.assertFalse(self.window.group_switch.isChecked())
        self.assertFalse(self.window._group_reply_enabled)
        self.assertEqual(path.read_bytes(), before)
        self.assertIn('其他程序修改', self.window.status_label.text())

    def test_switch_file_becoming_corrupt_is_not_overwritten(self):
        self.window.group_switch.setChecked(False)
        path = self.switch_path()
        path.write_bytes(b'corrupt-after-load')
        before = path.read_bytes()
        self.window.group_switch.setChecked(True)
        self.assertFalse(self.window.group_switch.isChecked())
        self.assertEqual(path.read_bytes(), before)
        self.assertIn('已关闭群聊 @ 回复', self.window.status_label.text())
        # The runtime corruption is now a load error: the switch stays
        # disabled and only the explicit reset path may replace the bytes.
        self.assertFalse(self.window.group_switch.isEnabled())
        self.assertTrue(self.window.group_switch_reset_button.isEnabled())

    # -- explicit local group recovery ---------------------------------

    def test_offline_group_recovery_discards_without_replay_and_releases(self):
        group, lease = self.install_offline_group_recovery()
        with mock.patch.object(
                main.QMessageBox, 'question',
                return_value=main.QMessageBox.Yes):
            self.window._recover_group()

        self.assertEqual(group.discard_calls, 1)
        self.assertFalse(group.pending)
        self.assertIsNone(self.window._group_core)
        self.assertTrue(lease.released)
        self.assertFalse(self.window.group_recovery_button.isEnabled())
        self.assertIn('没有重发', self.window.status_label.text())
        self.assertIn('重新连接', self.window.status_label.text())

    def test_group_recovery_cancel_and_failure_keep_durable_pending(self):
        group, lease = self.install_offline_group_recovery()
        with mock.patch.object(
                main.QMessageBox, 'question',
                return_value=main.QMessageBox.No):
            self.window._recover_group()
        self.assertEqual(group.discard_calls, 0)
        self.assertTrue(group.pending)
        self.assertIs(self.window._group_core, group)
        self.assertFalse(lease.released)
        self.assertTrue(self.window.group_recovery_button.isEnabled())
        self.assertIn('仍被保留', self.window.status_label.text())

        group.discard_result = False
        with mock.patch.object(
                main.QMessageBox, 'question',
                return_value=main.QMessageBox.Yes):
            self.window._recover_group()
        self.assertEqual(group.discard_calls, 1)
        self.assertTrue(group.pending)
        self.assertIs(self.window._group_core, group)
        self.assertFalse(lease.released)
        self.assertIn('仍被保留', self.window.status_label.text())

        group.discard_result = True
        with mock.patch.object(
                main.QMessageBox, 'question',
                return_value=main.QMessageBox.Yes):
            self.window._recover_group()
        self.assertFalse(group.pending)
        self.assertIsNone(self.window._group_core)
        self.assertTrue(lease.released)

    def test_group_recovery_dialog_state_change_never_writes_stale_ledger(self):
        group, lease = self.install_offline_group_recovery()
        replacement = FakeGroupCore(pending=True)

        def during_dialog(*args, **kwargs):
            self.window._group_core = replacement
            return main.QMessageBox.Yes

        with mock.patch.object(
                main.QMessageBox, 'question', side_effect=during_dialog):
            self.window._recover_group()

        self.assertEqual(group.discard_calls, 0)
        self.assertEqual(replacement.discard_calls, 0)
        self.assertTrue(group.pending)
        self.assertIs(self.window._group_core, replacement)
        self.assertFalse(lease.released)
        self.assertIn('状态已变化', self.window.status_label.text())

    def test_finish_stopped_retains_group_pending_for_local_recovery(self):
        adapter = QuietAdapter()
        group = FakeGroupCore(pending=True)
        lease = FakeLease()
        window = self.window
        window._adapter = adapter
        window._adapter_generation = 7
        window._stopping_adapter = None
        window._stop_thread = None
        window._core = None
        window._group_core = group
        window._connected = True
        window._data_lease = lease

        self.assertTrue(window._finish_stopped(adapter, 7))
        self.assertIsNone(window._adapter)
        self.assertIs(window._group_core, group)
        self.assertTrue(window.group_recovery_button.isEnabled())
        self.assertIn('群聊回复结果不明', window.status_label.text())
        self.assertFalse(lease.released)

        with mock.patch.object(
                main.QMessageBox, 'question',
                return_value=main.QMessageBox.Yes):
            window._recover_group()
        self.assertFalse(group.pending)
        self.assertIsNone(window._group_core)
        self.assertTrue(lease.released)

    def test_qt_group_refresh_does_not_wait_for_a_private_model(self):
        entered = threading.Event()
        release = threading.Event()

        def slow_private_model(*_args):
            entered.set()
            release.wait(3.0)
            return '合成答复'

        directory = self.window._data_directory()
        private = QQCore(directory, '100012345',
                         request_reply=slow_private_model)
        code = private.begin_pairing()
        self.assertIsNotNone(private.handle_private(
            '200054321', 'bind-only', '绑定 ' + code,
        ))
        group = GroupChat(directory, '100012345',
                          request_reply=lambda *_args: '合成群答复')
        self.window._group_core = group
        self.window._timer.stop()
        worker = threading.Thread(
            target=private.handle_private,
            args=('200054321', 'slow-private', '合成私聊测试'),
        )
        worker.start()
        # A regression must fail in bounded time instead of hanging Qt/the
        # test runner forever while a simulated private model holds its lock.
        watchdog = threading.Timer(1.0, release.set)
        durations = []

        def refresh_from_qt():
            started = time.monotonic()
            self.window._refresh_group_recovery()
            durations.append(time.monotonic() - started)

        try:
            self.assertTrue(entered.wait(1.0))
            watchdog.start()
            QTimer.singleShot(0, refresh_from_qt)
            self.app.processEvents()
            self.assertEqual(len(durations), 1)
            self.assertLess(durations[0], 0.3,
                            'Qt group refresh waited for private model')
        finally:
            release.set()
            watchdog.cancel()
            worker.join(2.0)
            if watchdog.ident is not None:
                watchdog.join(2.0)
            self.window._group_core = None

    def test_group_pending_does_not_block_private_reconnect(self):
        self.install_offline_group_recovery()
        self.window._sync_recovery_controls()
        self.assertTrue(self.window.connect_button.isEnabled())


if __name__ == '__main__':
    unittest.main()
