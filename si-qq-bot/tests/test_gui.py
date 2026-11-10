"""Offscreen UI tests; fake adapter prevents QQ, network, and file access."""
import gc
import os
import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QLabel, QLineEdit, QPushButton, QPlainTextEdit,
)
import main  # noqa: E402


class FakeCore:
    def __init__(self):
        self.calls = 0
        self.recoveries = 0

    def begin_pairing(self):
        self.calls += 1
        return "TEST-CODE-ONLY"

    def local_status(self):
        return {"bound": False, "pending_recovery_required": False}

    def discard_uncertain_pending(self):
        self.recoveries += 1
        return True


class RecoveryCore(FakeCore):
    """Synthetic core whose unresolved pending state can be controlled.

    No model, network, QQ state, or user data is involved: it only mirrors
    the in-memory/durable semantics of ``QQCore.discard_uncertain_pending``.
    """

    def __init__(self, pending=True):
        super().__init__()
        self.pending = bool(pending)
        self.discard_result = True
        self.discard_error = None
        self.discard_calls = 0

    def local_status(self):
        return {
            "bound": True,
            "pending_recovery_required": bool(self.pending),
        }

    def discard_uncertain_pending(self):
        self.discard_calls += 1
        self.recoveries += 1
        if self.discard_error is not None:
            raise self.discard_error
        if not self.discard_result:
            return False
        self.pending = False
        return True


class FakeAdapter:
    instances = []

    def __init__(self, url, token, core_factory, status_callback=None):
        self.url = url
        self.token = token
        self.core_factory = core_factory
        self.status_callback = status_callback
        self.started = False
        self.stopped = False
        self.status = []
        type(self).instances.append(self)

    def start(self):
        self.started = True
        return True

    def stop(self, *args, **kwargs):
        self.stopped = True
        return True

    def is_quiescent(self):
        return True

    def verified_bot_id(self):
        return None

    def poll_status(self):
        return self.status.pop(0) if self.status else None


class SlowStopAdapter(FakeAdapter):
    """Adapter whose model worker outlives the bounded stop wait."""

    def __init__(self, quiescent=False):
        super().__init__("ws://127.0.0.1:3001/", "token",
                         lambda bot_id: FakeCore())
        self.quiescent = quiescent
        self.stop_calls = 0

    def stop(self, *args, **kwargs):
        self.stop_calls += 1
        self.stopped = True
        return self.quiescent

    def is_quiescent(self):
        return self.quiescent


class BlockingStatusCore(FakeCore):
    """Core whose status read blocks a non-UI watcher thread on demand."""

    def __init__(self):
        super().__init__()
        self.status_entered = threading.Event()
        self.status_release = threading.Event()

    def local_status(self):
        if threading.current_thread() is not threading.main_thread():
            self.status_entered.set()
            self.status_release.wait(5)
        return {"bound": False, "pending_recovery_required": False}


class FakeLease:
    def __init__(self):
        self.released = False

    def release(self):
        self.released = True


_PROFILE_HOLDER = r"""
import json
import os
import sys
import tempfile

sys.path.insert(0, sys.argv[2])
from core import acquire_data_directory_lease

directory = sys.argv[1]
path = os.path.join(directory, "admin_profile.json")
lease = acquire_data_directory_lease(directory)


def write_profile(profile):
    payload = json.dumps(
        {"schema": 1, "profile": profile},
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    fd, temp_name = tempfile.mkstemp(
        prefix="admin_profile.json.", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


write_profile(sys.argv[3])
print("locked", flush=True)
line = sys.stdin.readline()
if line:
    write_profile(line.rstrip("\n"))
    print("updated", flush=True)
sys.stdin.read()
lease.release()
"""


_LOCK_PROBE = r"""
import sys
sys.path.insert(0, sys.argv[2])
from core import DataDirectoryBusyError, acquire_data_directory_lease
try:
    lease = acquire_data_directory_lease(sys.argv[1])
except DataDirectoryBusyError:
    print("busy")
else:
    lease.release()
    print("acquired")
"""


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls):
        # All windows are deleted on this (the Qt GUI) thread by
        # dispose_window.  Collect any remaining Python/Qt cycles here too,
        # while the QApplication is still alive and on the main thread, so a
        # later background thread can never be the one that destroys a
        # QObject (which is what can fail-fast Windows).
        FakeAdapter.instances.clear()
        cls.app.processEvents()
        cls.app.sendPostedEvents(None, QEvent.DeferredDelete)
        gc.collect()
        super().tearDownClass()

    def setUp(self):
        FakeAdapter.instances.clear()
        temp_root = ROOT / "qa-test-temp"
        temp_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=str(temp_root))
        self.addCleanup(self.temp.cleanup)
        self.env = mock.patch.dict(os.environ, {"LOCALAPPDATA": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.adapter_patch = mock.patch.object(main, "OneBotAdapter", FakeAdapter)
        self.adapter_patch.start()
        self.addCleanup(self.adapter_patch.stop)
        self.window = self.make_window()
        self.window.show()
        self.app.processEvents()

    def make_window(self):
        """Create a window that is deterministically destroyed on this thread."""
        window = main.SiQQWindow()
        self.addCleanup(self.dispose_window, window)
        return window

    def dispose_window(self, window):
        """Join Qt-emitting threads and delete the QObject on the GUI thread."""
        # Every GUI test replaces main.OneBotAdapter with a fake that has no
        # real background loop.  Detach it so a deliberately non-quiescent
        # fake does not block cleanup; shutdown() still joins any stop worker
        # or status watcher that a test actually started.
        window._adapter = None
        window._adapter_generation = None
        window._connected = False
        window._bound = False
        window._recovery = False
        window._close_requested = False
        idle = window.shutdown(timeout=2.0)
        window.close()
        self.app.processEvents()
        if not idle or not window.background_threads_idle():
            # Leaving the QObject for a background thread to destroy would
            # reintroduce the Windows fail-fast this cleanup exists to stop.
            self.fail("a background Qt emitter was still running at cleanup")
        window.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def wait_for(self, predicate, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        self.app.processEvents()
        return predicate()

    def probe_lock(self, directory):
        """Ask a separate process whether it can take the directory lease."""
        result = subprocess.run(
            [sys.executable, "-c", _LOCK_PROBE, str(directory), str(ROOT)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(
            result.returncode, 0,
            f"lock probe failed: {result.stdout!r} {result.stderr!r}",
        )
        return result.stdout.strip()

    def spawn_profile_holder(self, directory, initial_profile):
        """Start a child that owns the lease and writes the profile file."""
        # The protocol carries Chinese profile text over stdin.  Windows
        # would otherwise use the locale codepage for the child's pipes and
        # the round trip would silently mangle it.
        child_env = dict(os.environ)
        child_env["PYTHONIOENCODING"] = "utf-8"
        child = subprocess.Popen(
            [sys.executable, "-c", _PROFILE_HOLDER,
             str(directory), str(ROOT), initial_profile],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", env=child_env,
        )
        self.addCleanup(self.stop_child, child)
        if self.read_child_line(child) != "locked":
            self.fail("profile holder did not acquire the lease")
        return child

    def read_child_line(self, child):
        lines = queue.Queue()
        threading.Thread(
            target=lambda: lines.put(child.stdout.readline()), daemon=True
        ).start()
        try:
            return lines.get(timeout=30).strip()
        except queue.Empty:
            self.fail("profile holder did not respond in time")

    def stop_child(self, child):
        try:
            if child.stdin is not None and not child.stdin.closed:
                child.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=10)
        for stream in (child.stdout, child.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:  # pragma: no cover - defensive only
                pass

    def install_connection(self, adapter, generation, core):
        self.window._adapter = adapter
        self.window._adapter_generation = generation
        self.window._core = core
        self.window._stopping_adapter = None
        self.window._connected = True
        self.window._bound = False
        self.window._recovery = False

    def install_offline_recovery(self, window=None, core=None):
        """Install a disconnected retained core with unresolved pending."""
        window = self.window if window is None else window
        core = RecoveryCore() if core is None else core
        lease = FakeLease()
        window._adapter = None
        window._adapter_generation = None
        window._stop_thread = None
        window._stopping_adapter = None
        window._core = core
        window._connected = False
        window._bound = True
        window._recovery = True
        window._recovering = False
        window._data_lease = lease
        window.recovery_button.setText("恢复未确认回复（已断开）")
        window.recovery_button.setEnabled(True)
        return core, lease

    def test_construction_has_no_connection_or_real_user_data(self):
        self.assertEqual(FakeAdapter.instances, [])
        self.assertEqual(self.window.token.echoMode(), QLineEdit.Password)
        self.assertEqual(self.window._profile_value, main.DEFAULT_ADMIN_PROFILE)
        core = self.window._create_core_for_bot("test-bot")
        self.assertEqual(core._profile_snapshot(), main.DEFAULT_ADMIN_PROFILE)
        user_data = Path(self.temp.name) / "SiCompanion" / "QQPrivateChat"
        self.assertFalse(user_data.exists())
        self.assertTrue(self.window.connect_button.isEnabled())
        self.assertFalse(self.window.bind_button.isEnabled())

    def test_only_explicit_connect_constructs_adapter_and_token_stays_password(self):
        self.window.token.setText("not-a-real-token")
        self.assertEqual(self.window.token.echoMode(), QLineEdit.Password)
        self.assertEqual(FakeAdapter.instances, [])
        self.window.connect_button.click()
        self.app.processEvents()
        self.assertEqual(len(FakeAdapter.instances), 1)
        adapter = FakeAdapter.instances[0]
        self.assertTrue(adapter.started)
        self.assertEqual(adapter.url, "ws://127.0.0.1:3001/")
        self.assertEqual(adapter.token, "not-a-real-token")
        self.assertEqual(self.window.token.text(), "")
        # Connecting reserves the data directory with a lock file before any
        # core can read state, but no chat state is created yet.
        user_data = Path(self.temp.name) / "SiCompanion" / "QQPrivateChat"
        self.assertFalse((user_data / "si_qq_core_state.json").exists())
        self.assertIsNotNone(self.window._data_lease)

    def test_remote_endpoint_is_rejected_without_constructing_adapter(self):
        self.window.endpoint.setText("ws://192.168.1.2:3001/")
        self.window.token.setText("token")
        self.window.connect_button.click()
        self.app.processEvents()
        self.assertEqual(FakeAdapter.instances, [])
        self.assertIn("只允许", self.window.status_label.text())

    def test_pairing_code_is_created_only_after_explicit_click(self):
        core = FakeCore()
        self.window._core = core
        self.window._connected = True
        self.window.bind_button.setEnabled(True)
        self.assertEqual(core.calls, 0)
        self.window.bind_button.click()
        self.assertEqual(core.calls, 1)
        self.assertIn("TEST-CODE-ONLY", self.window.bind_label.text())

    def test_profile_import_reads_only_profile_and_saves_locally(self):
        source = Path(self.temp.name) / "conversation.json"
        source.write_text(json.dumps({
            "profile": "称呼我小葱；表达自然、亲切、克制。",
            "turns": [{"user": "不要导入这段私聊", "assistant": "旧聊天"}],
        }, ensure_ascii=False), encoding="utf-8")

        self.window.profile_button.click()
        self.app.processEvents()
        dialog = self.window._profile_dialog
        editor = dialog.findChild(QPlainTextEdit, "adminProfileEditor")
        self.assertIsNotNone(editor)
        with mock.patch.object(
                main.QFileDialog, "getOpenFileName",
                return_value=(str(source), "JSON 文件 (conversation.json)")):
            dialog.findChild(QPushButton, "importProfileButton").click()
        self.assertEqual(editor.toPlainText(), "称呼我小葱；表达自然、亲切、克制。")

        dialog.findChild(QPushButton, "saveProfileButton").click()
        self.app.processEvents()
        saved = Path(self.temp.name) / "SiCompanion" / "QQPrivateChat" / "admin_profile.json"
        value = json.loads(saved.read_text(encoding="utf-8"))
        self.assertEqual(value, {
            "schema": 1, "profile": "称呼我小葱；表达自然、亲切、克制。",
        })
        self.assertNotIn("不要导入这段私聊", saved.read_text(encoding="utf-8"))
        self.assertEqual(self.window._profile_value, value["profile"])

    def test_empty_saved_profile_overrides_default_across_restart(self):
        self.window.profile_button.click()
        self.app.processEvents()
        dialog = self.window._profile_dialog
        dialog.findChild(QPlainTextEdit, "adminProfileEditor").clear()
        dialog.findChild(QPushButton, "saveProfileButton").click()

        restarted = self.make_window()
        self.assertEqual(restarted._profile_value, "")

    def test_saved_profile_is_loaded_and_passed_to_local_core(self):
        profile_path = (
            Path(self.temp.name) / "SiCompanion" / "QQPrivateChat" / "admin_profile.json"
        )
        profile_path.parent.mkdir(parents=True)
        profile_path.write_text(json.dumps({
            "schema": 1, "profile": "称呼我小葱。",
        }, ensure_ascii=False), encoding="utf-8")

        window = self.make_window()
        self.assertEqual(window._profile_value, "称呼我小葱。")
        loaded = window._create_core_for_bot("test-bot")
        self.assertEqual(loaded._profile_snapshot(), "称呼我小葱。")

    def test_missing_saved_profile_is_not_confused_with_an_unreadable_one(self):
        data_dir = (
            Path(self.temp.name) / "SiCompanion" / "QQPrivateChat"
        )
        data_dir.mkdir(parents=True)
        profile_path = data_dir / "admin_profile.json"

        window = self.make_window()
        self.assertTrue(window._profile_known)
        self.assertIsNone(window._profile_load_error)
        self.assertEqual(window._profile_value, main.DEFAULT_ADMIN_PROFILE)

        window.token.setText("not-a-real-token")
        window._connect()
        self.assertEqual(len(FakeAdapter.instances), 1)
        self.assertFalse(profile_path.exists())
        core = FakeAdapter.instances[-1].core_factory("test-bot")
        self.assertEqual(core._profile_snapshot(), main.DEFAULT_ADMIN_PROFILE)

    def test_corrupt_saved_profile_blocks_connect_and_keeps_bytes(self):
        data_dir = (
            Path(self.temp.name) / "SiCompanion" / "QQPrivateChat"
        )
        data_dir.mkdir(parents=True)
        profile_path = data_dir / "admin_profile.json"
        original = b'{"schema":1,"profile":'
        profile_path.write_bytes(original)

        window = self.make_window()
        self.assertFalse(window._profile_known)
        self.assertEqual(window._profile_load_error, "corrupt")
        self.assertIn("损坏", window.status_label.text())
        self.assertEqual(window._profile_value, main.DEFAULT_ADMIN_PROFILE)

        window.token.setText("not-a-real-token")
        window._connect()
        self.assertEqual(FakeAdapter.instances, [])
        self.assertIn("不会连接", window.status_label.text())
        self.assertIn("原文件已保留", window.status_label.text())
        self.assertEqual(profile_path.read_bytes(), original)
        self.assertIsNone(window._data_lease)

    def test_unreadable_saved_profile_blocks_connect_and_keeps_bytes(self):
        data_dir = (
            Path(self.temp.name) / "SiCompanion" / "QQPrivateChat"
        )
        data_dir.mkdir(parents=True)
        profile_path = data_dir / "admin_profile.json"
        profile_path.write_text(json.dumps({
            "schema": 1, "profile": "称呼我小葱。",
        }, ensure_ascii=False), encoding="utf-8")
        original = profile_path.read_bytes()

        with mock.patch.object(
                Path, "read_text", side_effect=PermissionError("synthetic")):
            window = self.make_window()
            self.assertEqual(window._profile_load_error, "unreadable")
            self.assertIn("无法读取", window.status_label.text())
            window.token.setText("not-a-real-token")
            window._connect()
            self.assertEqual(FakeAdapter.instances, [])
            self.assertIn("不会连接", window.status_label.text())
            self.assertIsNone(window._data_lease)

        self.assertEqual(profile_path.read_bytes(), original)

    def test_busy_startup_does_not_overwrite_and_connect_reads_latest_profile(self):
        data_dir = (
            Path(self.temp.name) / "SiCompanion" / "QQPrivateChat"
        )
        holder = self.spawn_profile_holder(data_dir, "A 初始偏好")
        profile_path = data_dir / "admin_profile.json"
        initial_bytes = profile_path.read_bytes()

        # B starts while A owns the lease.  It must not claim the default is
        # the saved preference, read the file behind A's back, or write it.
        window = self.make_window()
        self.assertFalse(window._profile_known)
        self.assertEqual(window._profile_value, main.DEFAULT_ADMIN_PROFILE)
        self.assertIn("另一个程序", window.status_label.text())
        self.assertEqual(profile_path.read_bytes(), initial_bytes)

        # While A still owns the lease, B cannot connect and cannot overwrite.
        window.token.setText("not-a-real-token")
        window._connect()
        self.assertEqual(FakeAdapter.instances, [])
        self.assertEqual(profile_path.read_bytes(), initial_bytes)

        # Even an explicit save from B is refused while A owns the lease.
        window.profile_button.click()
        self.app.processEvents()
        dialog = window._profile_dialog
        dialog.findChild(QPlainTextEdit, "adminProfileEditor").setPlainText(
            "B 不应覆盖"
        )
        with mock.patch.object(main.QMessageBox, "information"):
            dialog.findChild(QPushButton, "saveProfileButton").click()
        self.assertEqual(profile_path.read_bytes(), initial_bytes)
        dialog.close()
        self.app.processEvents()

        # A saves a new profile and releases the lease.
        holder.stdin.write("A 更新后的偏好\n")
        holder.stdin.flush()
        self.assertEqual(self.read_child_line(holder), "updated")
        updated_bytes = profile_path.read_bytes()
        self.assertEqual(
            json.loads(updated_bytes.decode("utf-8"))["profile"],
            "A 更新后的偏好",
        )
        self.stop_child(holder)

        # B now acquires the lease and must read the latest profile before
        # constructing the core that will serve messages.
        window._connect()
        self.assertEqual(len(FakeAdapter.instances), 1)
        adapter = FakeAdapter.instances[-1]
        self.assertEqual(window._profile_value, "A 更新后的偏好")
        self.assertNotEqual(profile_path.read_bytes(), initial_bytes)
        self.assertEqual(profile_path.read_bytes(), updated_bytes)
        core = adapter.core_factory("test-bot")
        self.assertIsNotNone(core)
        self.assertEqual(core._profile_snapshot(), "A 更新后的偏好")

    def test_stale_editor_cannot_overwrite_a_profile_saved_while_lock_was_busy(self):
        data_dir = (
            Path(self.temp.name) / "SiCompanion" / "QQPrivateChat"
        )
        holder = self.spawn_profile_holder(data_dir, "A 初始偏好")
        profile_path = data_dir / "admin_profile.json"

        # B starts and opens the editor while A owns the lease.  The editor
        # can only show the built-in default; no saved value has been read.
        window = self.make_window()
        window.profile_button.click()
        self.app.processEvents()
        dialog = window._profile_dialog
        editor = dialog.findChild(QPlainTextEdit, "adminProfileEditor")
        self.assertEqual(editor.toPlainText(), main.DEFAULT_ADMIN_PROFILE)

        # A saves a newer profile and releases the lease while B's editor is
        # still open.
        holder.stdin.write("A 更新后的偏好\n")
        holder.stdin.flush()
        self.assertEqual(self.read_child_line(holder), "updated")
        updated_bytes = profile_path.read_bytes()
        self.stop_child(holder)

        # B's unedited editor still shows the stale default.  Saving it must
        # not silently overwrite A's newer preference.
        with mock.patch.object(main.QMessageBox, "information") as info:
            dialog.findChild(QPushButton, "saveProfileButton").click()
        self.assertTrue(info.called)
        self.assertEqual(profile_path.read_bytes(), updated_bytes)
        self.assertEqual(json.loads(
            updated_bytes.decode("utf-8"))["profile"], "A 更新后的偏好")

        # Reopening the editor now shows the saved value, and saving that is
        # allowed (it does not change the user's preference).
        dialog.close()
        self.app.processEvents()
        window.profile_button.click()
        self.app.processEvents()
        dialog = window._profile_dialog
        editor = dialog.findChild(QPlainTextEdit, "adminProfileEditor")
        self.assertEqual(editor.toPlainText(), "A 更新后的偏好")
        with mock.patch.object(main.QMessageBox, "information"):
            dialog.findChild(QPushButton, "saveProfileButton").click()
        self.assertEqual(profile_path.read_bytes(), updated_bytes)
        self.assertEqual(window._profile_value, "A 更新后的偏好")

    def test_shutdown_waits_for_watcher_and_no_signal_is_emitted_after_stop(self):
        adapter = FakeAdapter("ws://127.0.0.1:3001/", "token",
                              lambda bot_id: FakeCore())
        core = BlockingStatusCore()
        self.install_connection(adapter, 1, core)
        self.window._start_status_watch(adapter, 1, core)
        self.assertTrue(core.status_entered.wait(2))

        emitted = []
        self.window._events.core_status.connect(
            lambda *args: emitted.append(args)
        )
        # The watcher is blocked in local_status: shutdown must not claim the
        # window is safe to destroy yet.
        self.assertFalse(self.window.shutdown(timeout=0.1))
        self.assertFalse(self.window.background_threads_idle())

        core.status_release.set()
        self.assertTrue(self.window.wait_for_background_threads(timeout=2.0))
        self.app.processEvents()
        # The stop flag was set before local_status returned, so no queued
        # signal may target the window after shutdown reported quiescence.
        self.assertEqual(emitted, [])
        self.assertTrue(self.window.background_threads_idle())

    def test_close_is_deferred_until_the_status_watcher_is_quiescent(self):
        adapter = FakeAdapter("ws://127.0.0.1:3001/", "token",
                              lambda bot_id: FakeCore())
        core = BlockingStatusCore()
        self.install_connection(adapter, 1, core)
        self.window._start_status_watch(adapter, 1, core)
        self.assertTrue(core.status_entered.wait(2))

        self.window.close()
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window._close_requested)

        core.status_release.set()
        self.assertTrue(self.wait_for(
            lambda: not self.window.isVisible(), timeout=3.0
        ))
        self.assertTrue(self.window.background_threads_idle())

    def test_bootstrap_requires_napcat_login_before_connecting(self):
        texts = [label.text() for label in self.window.findChildren(QLabel)]
        intro = next(
            text for text in texts
            if "NapCat" in text and "连接" in text
        )
        self.assertLess(
            intro.index("扫码登录"), intro.index("点击“连接”")
        )
        self.assertNotIn("连接后由你本人扫码登录", intro)
        self.assertIn("专用 QQ 账号", intro)

    def test_admin_profile_example_does_not_name_the_user(self):
        self.assertNotIn("小葱", main.DEFAULT_ADMIN_PROFILE)
        self.window._open_profile_editor()
        self.app.processEvents()
        editor = self.window._profile_editor
        self.assertIsNotNone(editor)
        self.assertNotIn("小葱", editor.placeholderText())
        self.assertIn("管理员", editor.placeholderText())
        self.window._profile_dialog.close()

    def test_stop_pending_is_not_announced_as_safe_and_blocks_reconnect(self):
        adapter = SlowStopAdapter(quiescent=False)
        core = FakeCore()
        self.window._adapter = adapter
        self.window._core = core
        self.window._connected = True
        self.window.connect_button.setEnabled(False)
        self.window.disconnect_button.setEnabled(True)

        self.window._disconnect()
        self.assertTrue(self.wait_for(
            lambda: self.window._stop_thread is not None
            and not self.window._stop_thread.is_alive()
        ))
        self.assertTrue(self.wait_for(
            lambda: "暂未安全停止" in self.window.status_label.text()
        ))
        # The old adapter (and its quiescent core) is kept; reconnect is not
        # possible while the worker may still write the state file.
        self.assertIs(self.window._adapter, adapter)
        self.assertFalse(self.window.connect_button.isEnabled())
        self.assertFalse(self.window.disconnect_button.isEnabled())
        self.assertNotIn("已安全断开", self.window.status_label.text())
        instances_before = list(FakeAdapter.instances)
        self.window._connect()
        self.assertEqual(FakeAdapter.instances, instances_before)

        # Only a verified quiescent adapter finishes the stop.
        adapter.quiescent = True
        self.window._refresh()
        self.app.processEvents()
        self.assertIsNone(self.window._adapter)
        self.assertTrue(self.window.connect_button.isEnabled())
        self.assertTrue(self.window.token.isEnabled())
        self.assertIn("已安全断开", self.window.status_label.text())

    def test_stop_pending_keeps_unknown_delivery_recoverable_after_disconnect(self):
        adapter = SlowStopAdapter(quiescent=False)
        core = FakeCore()
        core.local_status = lambda: {
            "bound": True, "pending_recovery_required": True,
        }
        self.window._adapter = adapter
        self.window._core = core
        self.window._connected = True
        self.window.disconnect_button.setEnabled(True)

        self.window._disconnect()
        self.assertTrue(self.wait_for(
            lambda: self.window._stop_thread is not None
            and not self.window._stop_thread.is_alive()
        ))
        adapter.quiescent = True
        self.window._refresh()
        self.app.processEvents()

        self.assertIsNone(self.window._adapter)
        self.assertIs(self.window._core, core)
        self.assertTrue(self.window.recovery_button.isEnabled())
        self.assertIn("不会重发", self.window.status_label.text())
        self.assertIn("结果不明", self.window.status_label.text())

    def test_unknown_delivery_enables_local_recovery_without_resend(self):
        adapter = SlowStopAdapter(quiescent=True)
        # The status callback and the core must agree: recovery is offered
        # only for a core that still reports the unresolved pending record.
        core = RecoveryCore()
        self.window._adapter = adapter
        self.window._core = core
        self.window._connected = True
        self.window._on_core_status(adapter, None, True, True)
        self.assertTrue(self.window.recovery_button.isEnabled())
        self.assertIn("未确认", self.window.recovery_button.text())

        with mock.patch.object(
                main.QMessageBox, "question",
                return_value=main.QMessageBox.Yes):
            self.window._recover()
        self.assertEqual(core.recoveries, 1)
        self.assertFalse(self.window.recovery_button.isEnabled())
        self.assertIn("没有重发", self.window.status_label.text())
        self.assertIn("后续私聊可以继续", self.window.status_label.text())

    def test_offline_recovery_keeps_state_and_allows_reconnect(self):
        core = RecoveryCore()
        self.window._core = core
        self.window._recovery = True
        self.window.recovery_button.setEnabled(True)
        self.window.recovery_button.setText("恢复未确认回复（已断开）")

        with mock.patch.object(
                main.QMessageBox, "question",
                return_value=main.QMessageBox.Yes):
            self.window._recover()
        self.assertEqual(core.recoveries, 1)
        self.assertFalse(self.window.recovery_button.isEnabled())
        self.assertIn("没有重发", self.window.status_label.text())
        self.assertIn("重新连接", self.window.status_label.text())
        self.assertTrue(self.window.connect_button.isEnabled())

    def test_offline_recovery_dialog_rejects_reentrant_actions_then_allows_reconnect(self):
        """The confirmation's nested loop must not build a second lifecycle."""
        core, lease = self.install_offline_recovery()
        observations = {}
        questions = []

        def during_dialog(*args, **kwargs):
            questions.append(1)
            # These are exactly the re-entrant paths a nested Qt event loop
            # can reach while QMessageBox.question is still on the stack.
            self.window._connect()
            self.window._recover()
            self.window._make_pair_code()
            self.window._refresh()
            self.window.close()
            self.app.processEvents()
            observations.update(
                adapters=len(FakeAdapter.instances),
                core_is_original=self.window._core is core,
                recovering=self.window._recovering,
                close_requested=self.window._close_requested,
                visible=self.window.isVisible(),
                lease_released=lease.released,
                discard_calls=core.discard_calls,
            )
            return main.QMessageBox.Yes

        with mock.patch.object(
                main.QMessageBox, "question", side_effect=during_dialog):
            self.window._recover()

        # Re-entry is rejected: no adapter/core, no second confirmation, no
        # close, no lease release, and no discard while the dialog is open.
        self.assertEqual(len(questions), 1)
        self.assertEqual(observations["adapters"], 0)
        self.assertTrue(observations["core_is_original"])
        self.assertTrue(observations["recovering"])
        self.assertFalse(observations["close_requested"])
        self.assertTrue(observations["visible"])
        self.assertFalse(observations["lease_released"])
        self.assertEqual(observations["discard_calls"], 0)

        # The outer confirmation committed exactly once, to the captured core.
        self.assertEqual(core.discard_calls, 1)
        self.assertFalse(core.pending)
        self.assertFalse(self.window._recovery)
        self.assertIsNone(self.window._core)
        self.assertTrue(lease.released)
        self.assertTrue(self.window.connect_button.isEnabled())
        self.assertTrue(self.window.isVisible())

        # Only now may a connection be built, and it must be a fresh core.
        self.window.token.setText("not-a-real-token")
        self.window._connect()
        self.assertEqual(len(FakeAdapter.instances), 1)
        adapter = FakeAdapter.instances[0]
        new_core = adapter.core_factory("test-bot")
        self.assertIsNotNone(new_core)
        self.assertIs(self.window._core, new_core)
        self.assertIsNot(new_core, core)

    def test_offline_recovery_cancel_failure_keep_pending_and_block_connect(self):
        core, lease = self.install_offline_recovery()

        def assert_still_blocked(expected_discard_calls):
            self.assertEqual(core.discard_calls, expected_discard_calls)
            self.assertTrue(core.pending)
            self.assertTrue(self.window._recovery)
            self.assertIs(self.window._core, core)
            self.assertFalse(lease.released)
            self.assertFalse(self.window.connect_button.isEnabled())
            self.assertTrue(self.window.recovery_button.isEnabled())
            # The cancelled or failed recovery itself must say that the
            # durable pending record is still in place, before the connection
            # attempt below replaces the status text.
            self.assertIn("仍被保留", self.window.status_label.text())
            before = len(FakeAdapter.instances)
            self.window._connect()
            self.assertEqual(len(FakeAdapter.instances), before)
            # The refused reconnect independently reports that pending is
            # still retained and must be recovered first.
            refused = self.window.status_label.text()
            self.assertIn("仍被保留", refused)
            self.assertIn("请先完成", refused)
            self.assertIn("恢复", refused)

        # Cancel preserves the durable record, the core, and deduplication.
        with mock.patch.object(
                main.QMessageBox, "question",
                return_value=main.QMessageBox.No):
            self.window._recover()
        # Assert the cancel message here: assert_still_blocked's _connect()
        # deliberately overwrites the status with its refusal text.
        self.assertIn("仍被保留", self.window.status_label.text())
        self.assertIn("没有重发", self.window.status_label.text())
        self.assertIn(
            "恢复成功前不会建立新连接", self.window.status_label.text()
        )
        assert_still_blocked(0)

        # The retained core itself is authoritative: a stale UI flag alone
        # must not reopen the connection path to a second core.
        self.window._recovery = False
        before = len(FakeAdapter.instances)
        self.window._connect()
        self.assertEqual(len(FakeAdapter.instances), before)
        refused = self.window.status_label.text()
        self.assertIn("仍被保留", refused)
        self.assertIn("请先完成", refused)
        self.assertIn("恢复", refused)
        self.window._recovery = True

        # A raising persistence call must also keep everything.
        core.discard_error = OSError("synthetic")
        with mock.patch.object(
                main.QMessageBox, "question",
                return_value=main.QMessageBox.Yes):
            self.window._recover()
        assert_still_blocked(1)

        # A False result (failed atomic commit) keeps everything too.
        core.discard_error = None
        core.discard_result = False
        with mock.patch.object(
                main.QMessageBox, "question",
                return_value=main.QMessageBox.Yes):
            self.window._recover()
        assert_still_blocked(2)

        # Success is the only path that clears pending and unlocks connect.
        core.discard_result = True
        with mock.patch.object(
                main.QMessageBox, "question",
                return_value=main.QMessageBox.Yes):
            self.window._recover()
        self.assertEqual(core.discard_calls, 3)
        self.assertFalse(core.pending)
        self.assertFalse(self.window._recovery)
        self.assertIsNone(self.window._core)
        self.assertTrue(lease.released)
        self.assertTrue(self.window.connect_button.isEnabled())
        self.window.token.setText("not-a-real-token")
        self.window._connect()
        self.assertEqual(len(FakeAdapter.instances), 1)

    def test_online_recovery_dialog_rejects_disconnect_and_keeps_lifecycle(self):
        adapter = SlowStopAdapter(quiescent=True)
        core = RecoveryCore()
        self.window._adapter = adapter
        self.window._adapter_generation = 3
        self.window._core = core
        self.window._stopping_adapter = None
        self.window._connected = True
        self.window._bound = True
        self.window._recovery = False
        self.window._on_core_status(adapter, 3, True, True)
        self.assertTrue(self.window._recovery)
        observations = {}

        def during_dialog(*args, **kwargs):
            # A stop here could retire the adapter/core captured by _recover.
            self.window._disconnect()
            observations.update(
                stop_thread=self.window._stop_thread,
                adapter=self.window._adapter,
                recovering=self.window._recovering,
            )
            return main.QMessageBox.Yes

        with mock.patch.object(
                main.QMessageBox, "question", side_effect=during_dialog):
            self.window._recover()

        self.assertIsNone(observations["stop_thread"])
        self.assertIs(observations["adapter"], adapter)
        self.assertTrue(observations["recovering"])
        self.assertIs(self.window._adapter, adapter)
        self.assertEqual(self.window._adapter_generation, 3)
        self.assertIs(self.window._core, core)
        self.assertEqual(adapter.stop_calls, 0)
        self.assertFalse(adapter.stopped)
        self.assertEqual(core.discard_calls, 1)
        self.assertFalse(core.pending)
        self.assertFalse(self.window._recovery)
        self.assertFalse(self.window.recovery_button.isEnabled())
        self.assertTrue(self.window.disconnect_button.isEnabled())
        self.assertIn("后续私聊可以继续", self.window.status_label.text())

    def test_state_change_during_recovery_dialog_never_writes_stale_core(self):
        for mutation in ("core", "adapter", "generation", "lease",
                         "connected", "shutdown", "close"):
            with self.subTest(mutation=mutation):
                window = self.make_window()
                core, lease = self.install_offline_recovery(window)
                replacement = RecoveryCore()
                other_lease = FakeLease()

                def during_dialog(*args, **kwargs):
                    if mutation == "core":
                        window._core = replacement
                    elif mutation == "adapter":
                        window._adapter = object()
                    elif mutation == "generation":
                        window._adapter_generation = 41
                    elif mutation == "lease":
                        window._data_lease = other_lease
                    elif mutation == "connected":
                        window._connected = True
                    elif mutation == "shutdown":
                        window._shutting_down = True
                    elif mutation == "close":
                        window._close_requested = True
                    return main.QMessageBox.Yes

                with mock.patch.object(
                        main.QMessageBox, "question",
                        side_effect=during_dialog):
                    window._recover()

                # Even a confirmed dialog must not write through the captured
                # core once any identity, lease, or lifecycle state changed.
                self.assertEqual(core.discard_calls, 0, mutation)
                self.assertEqual(replacement.discard_calls, 0, mutation)
                self.assertTrue(core.pending, mutation)
                self.assertFalse(window._recovering, mutation)
                self.assertFalse(lease.released, mutation)
                self.assertIn("状态已变化", window.status_label.text(), mutation)
                # No path may construct a new adapter/core from that state.
                before = len(FakeAdapter.instances)
                window._connect()
                self.assertEqual(len(FakeAdapter.instances), before, mutation)

    def test_close_is_deferred_while_a_worker_is_still_running(self):
        adapter = SlowStopAdapter(quiescent=False)
        self.window._adapter = adapter
        self.window._core = None
        self.window._connected = True

        self.window.close()
        self.assertTrue(self.wait_for(
            lambda: self.window._stop_thread is not None
            and not self.window._stop_thread.is_alive()
        ))
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window._close_requested)
        self.assertIs(self.window._adapter, adapter)

        adapter.quiescent = True
        self.window._refresh()
        self.app.processEvents()
        self.assertFalse(self.window.isVisible())
        self.assertIsNone(self.window._adapter)

    @staticmethod
    def _abort_barrier(barrier):
        """Unblock a regression test's barrier even on an early failure."""
        try:
            barrier.abort()
        except threading.BrokenBarrierError:
            pass

    def test_stopped_signal_does_not_retire_adapter_while_stop_worker_is_alive(self):
        """A queued ``stopped`` must not finish while its emitter still lives.

        The worker is blocked on a barrier right after emit(), so delivery of
        the queued signal is deterministic while the thread is still alive.
        """
        adapter = SlowStopAdapter(quiescent=True)
        core = FakeCore()
        self.install_connection(adapter, 1, core)
        lease = FakeLease()
        self.window._data_lease = lease
        self.window.disconnect_button.setEnabled(True)

        barrier = threading.Barrier(2)
        self.addCleanup(self._abort_barrier, barrier)
        emitted = threading.Event()
        finished = []
        original_finish = self.window._finish_stopped

        def finish_spy(adapter_arg, generation_arg):
            finished.append((adapter_arg, generation_arg))
            return original_finish(adapter_arg, generation_arg)

        self.window._finish_stopped = finish_spy

        def stop_worker():
            # Mirrors _disconnect's worker, but remains alive after emit().
            adapter.stop()
            self.window._events.stopped.emit(adapter, 1)
            emitted.set()
            try:
                barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass

        thread = threading.Thread(
            target=stop_worker, name="SiQQStopBarrier", daemon=True
        )
        self.window._stop_thread = thread
        self.window._stopping_adapter = adapter
        self.window._connected = False
        thread.start()
        self.assertTrue(emitted.wait(2))

        # The user closes immediately, and the queued stopped slot is then
        # delivered while the stop worker is still blocked after emit().
        self.window.close()
        self.assertTrue(self.window._close_requested)
        self.assertTrue(self.wait_for(lambda: finished, timeout=2))
        self.assertTrue(thread.is_alive())

        # Nothing may be retired while the emitting thread is still alive:
        # not the thread record, the adapter, its core, the lease, or the
        # window/QObject that a late signal could target.
        self.assertIs(self.window._stop_thread, thread)
        self.assertIs(self.window._adapter, adapter)
        self.assertEqual(self.window._adapter_generation, 1)
        self.assertIs(self.window._core, core)
        self.assertIs(self.window._data_lease, lease)
        self.assertFalse(lease.released)
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.background_threads_idle())

        # Only after the barrier releases and the thread has really exited is
        # the deferred final close allowed to run.
        barrier.wait(timeout=5)
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(self.wait_for(
            lambda: not self.window.isVisible(), timeout=3
        ))
        self.assertIsNone(self.window._stop_thread)
        self.assertIsNone(self.window._adapter)
        self.assertIsNone(self.window._data_lease)
        self.assertTrue(lease.released)
        self.assertTrue(self.window.background_threads_idle())

    def test_normal_close_keeps_lease_until_stop_worker_is_quiescent(self):
        """Normal close path must not release the lease while stop() is False."""
        adapter = SlowStopAdapter(quiescent=False)
        core = FakeCore()
        self.install_connection(adapter, 1, core)
        lease = FakeLease()
        self.window._data_lease = lease
        self.window.disconnect_button.setEnabled(True)

        self.window.close()
        self.assertTrue(self.window._close_requested)
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.wait_for(
            lambda: self.window._stop_thread is not None
            and not self.window._stop_thread.is_alive()
        ))
        self.assertTrue(self.wait_for(
            lambda: "暂未安全停止" in self.window.status_label.text()
        ))
        self.assertIs(self.window._adapter, adapter)
        self.assertIs(self.window._data_lease, lease)
        self.assertFalse(lease.released)
        self.assertTrue(self.window.isVisible())

        adapter.quiescent = True
        self.window._refresh()
        self.app.processEvents()
        self.assertFalse(self.window.isVisible())
        self.assertIsNone(self.window._adapter)
        self.assertTrue(lease.released)

    def test_shutdown_retains_adapter_receiver_and_lease_until_quiescent(self):
        """Unexpected-exit path: stop() is False with a core worker still live."""
        adapter = SlowStopAdapter(quiescent=False)
        core = FakeCore()
        self.install_connection(adapter, 1, core)
        lease = FakeLease()
        self.window._data_lease = lease
        self.window.disconnect_button.setEnabled(True)

        # After app.exec() has returned this is the only remaining chance to
        # stop background writers.  A bounded stop that is not quiescent must
        # leave everything in place and report failure.
        self.assertFalse(self.window.shutdown(timeout=0.1))
        self.assertIs(self.window._adapter, adapter)
        self.assertEqual(self.window._adapter_generation, 1)
        self.assertIs(self.window._data_lease, lease)
        self.assertFalse(lease.released)
        self.assertFalse(self.window.background_threads_idle())
        # The QObject and its widgets are still alive and usable.  Destroying
        # them here would let a late adapter emit target freed Qt state.
        self.assertTrue(self.window.isVisible())
        self.assertIn("正在断开", self.window.status_label.text())

        adapter.quiescent = True
        self.assertTrue(self.window.shutdown(timeout=2.0))
        self.assertIsNone(self.window._adapter)
        self.assertIsNone(self.window._core)
        self.assertIsNone(self.window._data_lease)
        self.assertTrue(lease.released)
        self.assertTrue(self.window.background_threads_idle())

    # -- identity/generation guards for delayed old-connection signals -----

    def test_core_factory_is_generation_guarded_and_runs_under_lease(self):
        self.window.token.setText("not-a-real-token")
        self.window.connect_button.click()
        self.app.processEvents()
        adapter = FakeAdapter.instances[-1]

        core = adapter.core_factory("test-bot")
        self.assertIsNotNone(core)
        self.assertIs(self.window._core, core)
        self.assertIsNotNone(self.window._data_lease)

        # The same adapter/factory closure, but a stale lifecycle generation,
        # must not construct or install another core.
        self.window._adapter_generation = self.window._adapter_generation + 1
        self.assertIsNone(adapter.core_factory("test-bot"))
        self.assertIs(self.window._core, core)

    def test_queued_old_connection_signals_cannot_change_new_connection(self):
        old = FakeAdapter("ws://127.0.0.1:3001/", "old-token",
                          lambda bot_id: FakeCore())
        new = FakeAdapter("ws://127.0.0.1:3001/", "new-token",
                          lambda bot_id: FakeCore())
        new_core = FakeCore()
        self.install_connection(new, 2, new_core)
        lease = FakeLease()
        self.window._data_lease = lease
        self.window.status_label.setText("新连接状态")
        self.window.account_label.setText("祀的 QQ 账号：新连接")
        self.window.connect_button.setEnabled(False)
        self.window.disconnect_button.setEnabled(True)
        self.window.bind_button.setEnabled(True)
        self.window.recovery_button.setEnabled(False)

        emitted = threading.Event()

        def emit_old_signals():
            events = self.window._events
            events.status.emit(old, 1, "connected", "旧连接已连接")
            events.status.emit(old, 1, "connection_error", "旧连接错误")
            events.core_status.emit(old, 1, True, True)
            events.stop_pending.emit(old, 1)
            events.stopped.emit(old, 1)
            emitted.set()

        writer = threading.Thread(target=emit_old_signals)
        writer.start()
        writer.join(5)
        self.assertTrue(emitted.is_set())
        # Let Qt deliver the queued signals.
        self.wait_for(lambda: False, timeout=0.2)

        # None of the old lifecycle's queued stop/error/connection/recovery
        # callbacks may touch the new connection.
        self.assertIs(self.window._adapter, new)
        self.assertEqual(self.window._adapter_generation, 2)
        self.assertIs(self.window._core, new_core)
        self.assertTrue(self.window._connected)
        self.assertFalse(self.window._bound)
        self.assertFalse(self.window._recovery)
        self.assertIsNone(self.window._stop_thread)
        self.assertEqual(self.window.status_label.text(), "新连接状态")
        self.assertEqual(self.window.account_label.text(), "祀的 QQ 账号：新连接")
        self.assertFalse(self.window.connect_button.isEnabled())
        self.assertTrue(self.window.disconnect_button.isEnabled())
        self.assertTrue(self.window.bind_button.isEnabled())
        self.assertFalse(self.window.recovery_button.isEnabled())
        self.assertFalse(lease.released)

    def test_reused_adapter_object_with_old_generation_is_ignored(self):
        adapter = FakeAdapter("ws://127.0.0.1:3001/", "token",
                              lambda bot_id: FakeCore())
        core = FakeCore()
        self.install_connection(adapter, 2, core)
        self.window.status_label.setText("当前代状态")

        # The same adapter object is current at generation 2.  Callbacks that
        # still carry generation 1 are stale even though identity matches.
        self.window._on_status(adapter, 1, "connected", "旧代连接状态")
        self.window._on_core_status(adapter, 1, True, True)
        self.window._on_stop_pending(adapter, 1)
        self.window._on_stopped(adapter, 1)

        self.assertIs(self.window._adapter, adapter)
        self.assertIs(self.window._core, core)
        self.assertTrue(self.window._connected)
        self.assertFalse(self.window._bound)
        self.assertFalse(self.window._recovery)
        self.assertIsNone(self.window._stop_thread)
        self.assertEqual(self.window.status_label.text(), "当前代状态")

        # The current generation is still accepted, proving the guard is not
        # simply rejecting every callback for this adapter object.
        self.window._on_status(adapter, 2, "connected", "当前代已连接")
        self.assertEqual(self.window.status_label.text(), "当前代已连接")
        self.window._on_core_status(adapter, 2, True, True)
        self.assertTrue(self.window._bound)
        self.assertTrue(self.window._recovery)
        self.assertTrue(self.window.recovery_button.isEnabled())

    def test_queued_connected_status_during_stop_cannot_revive_connection(self):
        adapter = SlowStopAdapter(quiescent=False)
        core = FakeCore()
        self.install_connection(adapter, 1, core)
        self.window.disconnect_button.setEnabled(True)

        self.window._disconnect()
        self.window._on_status(adapter, 1, "connected", "已识别 QQ 账号")
        self.assertFalse(self.window._connected)
        self.assertIn("正在断开", self.window.status_label.text())

        self.assertTrue(self.wait_for(
            lambda: self.window._stop_thread is not None
            and not self.window._stop_thread.is_alive()
        ))
        self.assertTrue(self.wait_for(
            lambda: "暂未安全停止" in self.window.status_label.text()
        ))
        self.assertIs(self.window._adapter, adapter)

        adapter.quiescent = True
        self.window._refresh()
        self.app.processEvents()
        self.assertIsNone(self.window._adapter)

    # -- cross-process data-directory lease --------------------------------

    def test_directory_lock_is_held_until_stop_worker_is_quiescent(self):
        adapter = SlowStopAdapter(quiescent=False)
        core = FakeCore()
        self.install_connection(adapter, 1, core)
        self.assertTrue(self.window._ensure_data_lease())
        data_dir = self.window._data_directory()

        # While this window owns the directory no other process can take it.
        self.assertEqual(self.probe_lock(data_dir), "busy")

        self.window.disconnect_button.setEnabled(True)
        self.window._disconnect()
        self.assertTrue(self.wait_for(
            lambda: self.window._stop_thread is not None
            and not self.window._stop_thread.is_alive()
        ))
        self.assertTrue(self.wait_for(
            lambda: "暂未安全停止" in self.window.status_label.text()
        ))
        # A stop that is not quiescent must not release the persistent lease.
        self.assertIsNotNone(self.window._data_lease)
        self.assertEqual(self.probe_lock(data_dir), "busy")

        adapter.quiescent = True
        self.window._refresh()
        self.app.processEvents()
        self.assertTrue(self.wait_for(
            lambda: self.window._adapter is None
        ))
        self.assertIsNone(self.window._data_lease)
        self.assertEqual(self.probe_lock(data_dir), "acquired")


if __name__ == "__main__":
    unittest.main()
