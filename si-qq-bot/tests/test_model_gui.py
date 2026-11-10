"""Offscreen tests for the compact model-connection UI.

No real adapter, HTTP service, QQ account, credential, or user data is used.
The only worker threads are synthetic test doubles that never touch Qt.
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

from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

import main  # noqa: E402
import model_connection as mc  # noqa: E402


SECRET = 'sk-synthetic-gui-secret'
ACTIVE_ENDPOINT = 'https://models.example.test/v1'
ACTIVE_MODEL = 'deepseek-v4-pro'


class ExplodingAdapter:
    def __init__(self, *args, **kwargs):
        raise AssertionError('no adapter may be constructed in this test')


class ModelGuiTests(unittest.TestCase):
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
        self.addCleanup(self.dispose_window, window)
        return window

    def dispose_window(self, window):
        try:
            window._adapter = None
            window._adapter_generation = None
            window._connected = False
            window._bound = False
            window._recovery = False
            window._close_requested = False
            window._core = None
            window._group_core = None
            window.shutdown(timeout=1.0)
            window.close()
        except RuntimeError:
            pass
        self.app.processEvents()
        try:
            window.deleteLater()
        except RuntimeError:
            pass
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def make_dialog(self):
        dialog = main.ModelConnectionDialog(
            self.window, self.window._model_connection,
            self.window._apply_model_connection,
        )
        dialog.show()
        self.app.processEvents()
        self.addCleanup(self.dispose_dialog, dialog)
        return dialog

    def dispose_dialog(self, dialog):
        try:
            dialog._shutdown_test_poll()
            dialog.close()
        except RuntimeError:
            pass
        self.app.processEvents()
        try:
            dialog.deleteLater()
        except RuntimeError:
            pass
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def data_dir(self):
        return Path(self.temp.name) / 'SiCompanion' / 'QQPrivateChat'

    def config_path(self):
        return self.data_dir() / mc.MODEL_CONNECTION_FILENAME

    def pump_until(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        return bool(predicate())

    # -- default, apply, failure ---------------------------------------

    def test_default_school_status_creates_no_data_directory(self):
        self.assertFalse(self.data_dir().exists())
        self.assertEqual(
            self.window.model_connection_button.text(), '模型连接…'
        )
        text = self.window.model_connection_status.text()
        self.assertIn('学校 GenAI', text)
        self.assertIn(mc.SCHOOL_MODEL, text)
        self.assertNotIn(mc.SCHOOL_ENDPOINT, text)
        snapshot = self.window._model_connection.current()
        self.assertEqual(snapshot.provider, mc.SCHOOL_PROVIDER)
        self.assertEqual(snapshot.endpoint, mc.SCHOOL_ENDPOINT)
        self.assertEqual(snapshot.model, mc.SCHOOL_MODEL)
        self.assertEqual(snapshot.api_key, mc.SCHOOL_API_KEY)

    def test_apply_persists_only_selection_and_updates_visible_status(self):
        ok, message = self.window._apply_model_connection(
            mc.CUSTOM_PROVIDER, ACTIVE_ENDPOINT, ACTIVE_MODEL, SECRET
        )
        self.assertTrue(ok)
        self.assertEqual(message, main.MODEL_CONNECTION_APPLIED_TEXT)
        self.assertEqual(self.window.status_label.text(), message)

        raw = self.config_path().read_text(encoding='utf-8')
        payload = json.loads(raw)
        self.assertEqual(
            set(payload), {'schema', 'provider', 'endpoint', 'model'}
        )
        self.assertEqual(payload['provider'], mc.CUSTOM_PROVIDER)
        self.assertEqual(payload['endpoint'], ACTIVE_ENDPOINT)
        self.assertEqual(payload['model'], ACTIVE_MODEL)
        self.assertNotIn(SECRET, raw)
        self.assertEqual(
            self.window._model_connection.current().api_key, SECRET
        )
        self.assertIsNone(self.window._data_lease)

        status = self.window.model_connection_status.text()
        self.assertIn('自定义 HTTPS', status)
        self.assertIn(ACTIVE_MODEL, status)
        self.assertNotIn(ACTIVE_ENDPOINT, status)
        self.assertNotIn(SECRET, status)
        self.assertNotIn(SECRET, self.window.status_label.text())

    def test_save_failure_does_not_persist_activate_or_retain_the_key(self):
        before = self.window._model_connection.current()
        with mock.patch.object(
                mc, '_atomic_write_json',
                side_effect=OSError('synthetic disk failure')):
            ok, message = self.window._apply_model_connection(
                mc.CUSTOM_PROVIDER, ACTIVE_ENDPOINT, ACTIVE_MODEL, SECRET
            )
        self.assertFalse(ok)
        self.assertEqual(message, mc.ERROR_CONFIG_SAVE_TEXT)
        self.assertIs(self.window._model_connection.current(), before)
        self.assertFalse(self.config_path().exists())
        self.assertNotIn(SECRET, message)

    def test_external_change_is_not_overwritten(self):
        ok, _message = self.window._apply_model_connection(
            mc.LOCAL_PROVIDER, mc.LOCAL_DEFAULT_ENDPOINT,
            mc.LOCAL_DEFAULT_MODEL, ''
        )
        self.assertTrue(ok)
        external = b'{"schema":1,"provider":"local","endpoint":' \
                   b'"http://localhost:1234/v1","model":"qwen2.5-7b"}'
        self.config_path().write_bytes(external)
        ok, message = self.window._apply_model_connection(
            mc.CUSTOM_PROVIDER, ACTIVE_ENDPOINT, ACTIVE_MODEL, SECRET
        )
        self.assertFalse(ok)
        self.assertEqual(message, mc.ERROR_CONFIG_STALE_TEXT)
        self.assertEqual(self.config_path().read_bytes(), external)
        self.assertEqual(
            self.window._model_connection.current().provider,
            mc.LOCAL_PROVIDER,
        )
        self.assertNotIn(SECRET, message)

    def test_corrupt_config_blocks_connect_and_preserves_original_bytes(self):
        original = b'{"schema":1,'
        self.config_path().parent.mkdir(parents=True, exist_ok=True)
        self.config_path().write_bytes(original)
        window = self.make_window()
        self.assertIsNone(window._model_connection.current())
        self.assertEqual(window._model_connection.load_error, 'corrupt')
        self.assertIn('损坏', window.model_connection_status.text())
        self.assertEqual(self.config_path().read_bytes(), original)

        with mock.patch.object(main, 'OneBotAdapter', ExplodingAdapter):
            window._connect()
        self.assertIn(
            '请先在“模型连接”中应用有效设置', window.status_label.text()
        )
        self.assertIsNone(window._data_lease)
        self.assertEqual(self.config_path().read_bytes(), original)

        # An explicit Apply is allowed to replace the unchanged bad file.
        ok, _message = window._apply_model_connection(
            mc.CUSTOM_PROVIDER, ACTIVE_ENDPOINT, ACTIVE_MODEL, SECRET
        )
        self.assertTrue(ok)
        self.assertIsNotNone(window._model_connection.current())
        self.assertNotIn(SECRET, self.config_path().read_text(encoding='utf-8'))

    # -- dialog: editing and explicit apply ----------------------------

    def test_dialog_editing_and_test_do_not_apply_until_apply_is_clicked(self):
        dialog = self.make_dialog()
        self.assertIn('当前生效', dialog.status_label.text())
        index = dialog.provider_combo.findData(mc.CUSTOM_PROVIDER)
        dialog.provider_combo.setCurrentIndex(index)
        dialog.endpoint_edit.setText(ACTIVE_ENDPOINT)
        dialog.model_edit.setText(ACTIVE_MODEL)
        dialog.key_edit.setText(SECRET)

        self.assertFalse(self.config_path().exists())
        self.assertEqual(
            self.window._model_connection.current().provider,
            mc.SCHOOL_PROVIDER,
        )

        def quick_test(provider, endpoint, model, api_key):
            return True, '合成测试完成：已读取模型列表，未发送聊天内容。'

        with mock.patch.object(main, 'test_connection', side_effect=quick_test):
            dialog.test_button.click()
            self.assertTrue(self.pump_until(
                lambda: '合成测试完成' in dialog.status_label.text()
            ))
        self.assertFalse(self.config_path().exists())
        self.assertEqual(
            self.window._model_connection.current().provider,
            mc.SCHOOL_PROVIDER,
        )

        dialog.apply_button.click()
        self.app.processEvents()
        self.assertTrue(self.config_path().exists())
        self.assertEqual(
            self.window._model_connection.current().provider,
            mc.CUSTOM_PROVIDER,
        )
        self.assertIn('模型连接已更新', dialog.status_label.text())
        self.assertNotIn(
            SECRET, self.config_path().read_text(encoding='utf-8')
        )
        self.assertNotIn(SECRET, dialog.status_label.text())

    def test_dialog_test_failure_is_fixed_and_does_not_apply(self):
        dialog = self.make_dialog()

        def failing_test(provider, endpoint, model, api_key):
            return False, mc.ERROR_LOCAL_ABSENT_TEXT

        with mock.patch.object(main, 'test_connection',
                               side_effect=failing_test):
            dialog.test_button.click()
            self.assertTrue(self.pump_until(
                lambda: dialog.status_label.text()
                == mc.ERROR_LOCAL_ABSENT_TEXT
            ))
        self.assertFalse(self.config_path().exists())
        self.assertEqual(
            self.window._model_connection.current().provider,
            mc.SCHOOL_PROVIDER,
        )
        self.assertNotIn(ACTIVE_ENDPOINT, dialog.status_label.text())
        self.assertNotIn(SECRET, dialog.status_label.text())

    # -- dialog: off-thread and lifecycle ------------------------------

    def test_test_runs_off_the_ui_thread_and_close_while_running_is_safe(self):
        dialog = self.make_dialog()
        index = dialog.provider_combo.findData(mc.CUSTOM_PROVIDER)
        dialog.provider_combo.setCurrentIndex(index)
        dialog.endpoint_edit.setText(ACTIVE_ENDPOINT)
        dialog.model_edit.setText(ACTIVE_MODEL)
        dialog.key_edit.setText(SECRET)

        entered = threading.Event()
        release = threading.Event()
        worker_threads = []

        def blocking_test(provider, endpoint, model, api_key):
            worker_threads.append(threading.get_ident())
            entered.set()
            release.wait(3.0)
            return True, '合成完成'

        try:
            with mock.patch.object(main, 'test_connection',
                                   side_effect=blocking_test):
                dialog.test_button.click()
                self.assertTrue(entered.wait(1.0))
                self.assertNotEqual(
                    worker_threads[0], threading.get_ident()
                )
                self.assertIn('正在测试连接', dialog.status_label.text())
                self.assertFalse(self.config_path().exists())
                self.assertEqual(
                    self.window._model_connection.current().provider,
                    mc.SCHOOL_PROVIDER,
                )
                # Close while the worker is outstanding.  It must only leave a
                # result in the queue, never emit into this QObject.
                dialog.close()
                self.assertEqual(dialog.key_edit.text(), '')
                self.app.processEvents()
        finally:
            release.set()
        # Let the synthetic worker finish before the window is destroyed.
        deadline = time.monotonic() + 1.3
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)

    def test_closing_the_application_window_with_a_dialog_open_is_safe(self):
        dialog = self.make_dialog()
        release = threading.Event()
        entered = threading.Event()

        def blocking_test(provider, endpoint, model, api_key):
            entered.set()
            release.wait(3.0)
            return True, '合成完成'

        try:
            with mock.patch.object(main, 'test_connection',
                                   side_effect=blocking_test):
                dialog.test_button.click()
                self.assertTrue(entered.wait(1.0))
                # Simulate application teardown: close and defer-delete the
                # parent window and its child dialog while the worker runs.
                self.dispose_dialog(dialog)
                self.dispose_window(self.window)
        finally:
            release.set()
        # The test double never touches Qt; no exception is expected here.
        self.app.processEvents()

    def test_closing_dialog_clears_a_masked_secret_from_the_widget(self):
        ok, _message = self.window._apply_model_connection(
            mc.CUSTOM_PROVIDER, ACTIVE_ENDPOINT, ACTIVE_MODEL, SECRET
        )
        self.assertTrue(ok)
        dialog = self.make_dialog()
        self.assertEqual(dialog.key_edit.text(), SECRET)
        self.assertEqual(dialog.key_edit.echoMode(), QLineEdit.Password)
        self.assertNotIn(SECRET, dialog.status_label.text())
        self.assertNotIn(ACTIVE_ENDPOINT, dialog.status_label.text())
        dialog.close()
        self.assertEqual(dialog.key_edit.text(), '')


if __name__ == '__main__':
    unittest.main()
