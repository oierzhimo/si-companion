"""Tests for the explicit frozen-EXE GenAI live acceptance probe.

The live probe is exercised with mocked request functions, but with the real
``ConversationDialog`` and a real Qt event loop.  The tests therefore cover the
button wiring, queued-signal waits, report isolation, success/failure/timeout
paths and overwrite refusal without contacting any model or network service.
They do not prove real model availability and are not a physical-GUI test.
"""

import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtWidgets import QApplication

import si_pet.app as app_module
import si_pet.conversation_ui as conversation_ui_module
from si_pet import genai_frozen_probe as probe_module
from si_pet.conversation import (
    Conversation,
    ConversationError,
    DEFAULT_GENAI_ENDPOINT,
    GENAI_ALLOWED_MODELS,
    GENAI_DEFAULT_API_KEY,
    GENAI_DEFAULT_MODEL,
)

_QT_APP = None


def qt_application():
    global _QT_APP
    if _QT_APP is None:
        _QT_APP = QApplication.instance() or QApplication([])
    return _QT_APP


class FakeLiveBackend:
    """Thread-safe request double; no sockets and no model are involved."""

    def __init__(self, *, models=None, reply='合成验收回复：我在。'):
        self.models = list(models if models is not None else [
            'GPT-6.1-Sol',
            'Qwen3-Coder',
            'GLM-5.3-Flash',
            'DeepSeek-V4.1-Flash',
            'deepseek-v4.1-flash',
        ])
        self.reply = reply
        self.fetch_error = None
        self.reply_error = None
        self.block_reply = False
        self.release_reply = threading.Event()
        self.fetch_calls = []
        self.reply_calls = []

    def fetch(self, endpoint, api_key=''):
        self.fetch_calls.append({
            'endpoint': endpoint,
            'api_key_present': bool(api_key),
        })
        if self.fetch_error is not None:
            raise self.fetch_error
        return list(self.models)

    def request(self, endpoint, model, messages, api_key=''):
        last_user = ''
        if messages and isinstance(messages[-1], dict):
            last_user = messages[-1].get('content', '')
        self.reply_calls.append({
            'endpoint': endpoint,
            'model': model,
            'last_user': last_user,
            'api_key_present': bool(api_key),
        })
        if self.block_reply:
            self.release_reply.wait(10.0)
        if self.reply_error is not None:
            raise self.reply_error
        return self.reply

    def release(self):
        self.release_reply.set()


class FrozenProbeLiveFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_application()

    def setUp(self):
        # PySide reports unhandled exceptions raised inside Qt slots through
        # the process-wide sys.excepthook.  The desktop application replaces
        # that hook with one that opens a modal error box, so isolate every
        # probe test from whatever hook a previous test left behind and record
        # (instead of displaying) anything that escapes a slot or a worker.
        self._saved_excepthook = sys.excepthook
        self._saved_thread_excepthook = getattr(threading, 'excepthook', None)
        self._escaped_exceptions = []

        def _record_exception(exc_type, exc_value, exc_tb):
            self._escaped_exceptions.append((exc_type, exc_value))

        def _record_thread_exception(args):
            self._escaped_exceptions.append((args.exc_type, args.exc_value))

        sys.excepthook = _record_exception
        if self._saved_thread_excepthook is not None:
            threading.excepthook = _record_thread_exception
        self.addCleanup(self._restore_excepthooks)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.report_path = self.root / 'report.json'
        self.backend = FakeLiveBackend()
        self.patches = [
            patch.object(conversation_ui_module, 'fetch_models_request',
                         self.backend.fetch),
            patch.object(conversation_ui_module, 'request_reply',
                         self.backend.request),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        # LIFO cleanups: release and let the late worker return while the Qt
        # application is still alive, then drain the signals it posted.
        self.addCleanup(self._drain_events)
        self.addCleanup(self._release_and_wait)

    def _restore_excepthooks(self):
        escaped = list(self._escaped_exceptions)
        sys.excepthook = self._saved_excepthook
        if self._saved_thread_excepthook is not None:
            threading.excepthook = self._saved_thread_excepthook
        self.assertEqual(
            escaped, [],
            'an exception escaped into the process-wide excepthook: '
            + repr(escaped))

    @staticmethod
    def _probe_threads_alive():
        return any(thread.name in ('si-local-chat', 'si-models')
                   and thread.is_alive()
                   for thread in threading.enumerate())

    def _release_and_wait(self):
        self.backend.release()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            self.app.processEvents()
            if not self._probe_threads_alive():
                return
            time.sleep(0.01)

    def _drain_events(self):
        for _ in range(10):
            self.app.processEvents()
            time.sleep(0.005)

    def run_probe(self, timeout=5.0):
        return probe_module.run_frozen_genai_probe(
            self.app, self.report_path, total_timeout=timeout)

    def read_report(self):
        return json.loads(self.report_path.read_text(encoding='utf-8'))

    def test_success_uses_real_dialog_programmatic_clicks_and_isolated_save(self):
        code = self.run_probe()
        report = self.read_report()

        self.assertEqual(code, probe_module.EXIT_OK)
        self.assertEqual(report['status'], 'ok')
        self.assertTrue(report['ok'])
        self.assertTrue(report['technical_roundtrip_verified'])
        self.assertFalse(report['physical_gui_verified'])
        self.assertFalse(report['real_user_data_read'])
        self.assertFalse(report['school_token_read'])
        self.assertFalse(report['voice_cache_read'])
        self.assertFalse(report['voice_studio_started'])
        self.assertFalse(report['global_hotkey_registered'])
        self.assertFalse(report['tray_created'])
        self.assertFalse(report['desktop_pet_created'])
        self.assertFalse(report['proxy_started_or_stopped'])
        self.assertFalse(report['window_shown'])

        interaction = report['interaction']
        self.assertEqual(interaction['method'], 'programmatic_qt_widget_click')
        self.assertTrue(interaction['model_list_button_clicked_programmatically'])
        self.assertTrue(interaction['send_button_clicked_programmatically'])
        self.assertEqual(interaction['programmatic_qt_click_count'], 2)
        self.assertFalse(interaction['physical_mouse_or_keyboard_used'])
        self.assertFalse(interaction['physical_gui_clicked'])
        self.assertFalse(interaction['user_desktop_acceptance'])
        self.assertTrue(interaction['not_a_physical_gui_acceptance'])
        self.assertTrue(interaction['queued_qt_signals_wait_used'])

        # The service deliberately returned GPT, an unrelated model, a GLM and
        # two case variants of DeepSeek.  The real dialog kept only the two
        # verified domestic models and did not switch the default DeepSeek.
        self.assertEqual(
            [name.casefold() for name in report['model_list']],
            [name.casefold() for name in GENAI_ALLOWED_MODELS],
        )
        self.assertTrue(report['model_list_only_allowed_domestic'])
        self.assertTrue(report['default_preset_preserved'])
        self.assertTrue(report['default_model_preserved'])
        self.assertEqual(report['model_fetch_request_count'], 1)
        self.assertEqual(report['reply_request_count'], 1)
        self.assertEqual(report['sent_model'].casefold(),
                         GENAI_DEFAULT_MODEL.casefold())
        self.assertEqual(report['default_endpoint'], DEFAULT_GENAI_ENDPOINT)
        self.assertTrue(report['reloaded_consistent'])
        self.assertTrue(report['saved_in_isolated_artifact_dir'])
        self.assertFalse(report['api_key_in_report'])
        self.assertFalse(report['api_key_in_conversation_file'])
        self.assertFalse(report['authorization_header_recorded'])
        self.assertTrue(report['turn']['assistant'].strip())

        self.assertEqual(len(self.backend.fetch_calls), 1)
        self.assertEqual(self.backend.fetch_calls[0]['endpoint'],
                         DEFAULT_GENAI_ENDPOINT)
        self.assertTrue(self.backend.fetch_calls[0]['api_key_present'])
        self.assertEqual(len(self.backend.reply_calls), 1)
        sent = self.backend.reply_calls[0]
        self.assertEqual(sent['endpoint'], DEFAULT_GENAI_ENDPOINT)
        self.assertEqual(sent['model'].casefold(), GENAI_DEFAULT_MODEL.casefold())
        self.assertEqual(sent['last_user'], probe_module.SYNTHETIC_PROMPT)
        self.assertTrue(sent['api_key_present'])
        self.assertEqual(report['controller_say_records'],
                         [{'text': self.backend.reply, 'force': True}])
        self.assertFalse(report['audio_playback_started'])

        artifact_dir = Path(report['artifact_dir'])
        conversation_path = Path(report['conversation_path'])
        self.assertEqual(artifact_dir.parent, self.report_path.parent)
        self.assertTrue(artifact_dir.name.startswith('report.artifacts-'))
        self.assertEqual(conversation_path.parent, artifact_dir)
        saved = json.loads(conversation_path.read_text(encoding='utf-8'))
        self.assertEqual(saved['provider'], 'genai')
        self.assertEqual(saved['endpoint'], DEFAULT_GENAI_ENDPOINT)
        self.assertEqual(saved['model'].casefold(),
                         GENAI_DEFAULT_MODEL.casefold())
        self.assertEqual(saved['turns'],
                         [report['turn']])
        reloaded = Conversation(conversation_path)
        self.assertEqual(reloaded.turns, saved['turns'])

        raw_report = self.report_path.read_text(encoding='utf-8')
        raw_conversation = conversation_path.read_text(encoding='utf-8')
        self.assertNotIn(GENAI_DEFAULT_API_KEY, raw_report)
        self.assertNotIn(GENAI_DEFAULT_API_KEY, raw_conversation)
        self.assertNotIn('bearer ', raw_report.casefold())
        self.assertNotIn('authorization', raw_conversation.casefold())

    def test_model_list_failure_writes_failure_and_never_sends(self):
        self.backend.fetch_error = ConversationError(
            '模拟模型列表失败；口令 ' + GENAI_DEFAULT_API_KEY)
        code = self.run_probe()
        report = self.read_report()

        self.assertEqual(code, probe_module.EXIT_FAILED)
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['ok'])
        self.assertFalse(report['technical_roundtrip_verified'])
        self.assertEqual(report['error']['stage'], 'model_list')
        self.assertEqual(report['error']['type'], 'ModelListError')
        self.assertEqual(report['reply_request_count'], 0)
        self.assertFalse(report['interaction']['send_button_clicked_programmatically'])
        self.assertEqual(report['interaction']['programmatic_qt_click_count'], 1)
        self.assertEqual(self.backend.reply_calls, [])
        self.assertIsNone(report['conversation_path'])
        self.assertIn('模拟模型列表失败', report['error']['message'])
        raw = self.report_path.read_text(encoding='utf-8')
        self.assertNotIn(GENAI_DEFAULT_API_KEY, raw)

    def test_reply_failure_writes_failure_without_saved_turn(self):
        self.backend.reply_error = ConversationError('模拟模型回复失败')
        code = self.run_probe()
        report = self.read_report()

        self.assertEqual(code, probe_module.EXIT_FAILED)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['error']['type'], 'ReplyError')
        self.assertEqual(report['error']['stage'], 'reply_apply')
        self.assertEqual(report['reply_request_count'], 1)
        self.assertEqual(len(self.backend.reply_calls), 1)
        self.assertIsNone(report.get('turn'))
        if report['conversation_path'] is not None:
            saved = json.loads(Path(report['conversation_path']).read_text(
                encoding='utf-8'))
            self.assertEqual(saved['turns'], [])
        self.assertFalse(report['interaction']['physical_gui_clicked'])

    def test_timeout_exits_with_timeout_report_without_retry(self):
        self.backend.block_reply = True
        started = time.monotonic()
        code = self.run_probe(timeout=1.0)
        elapsed = time.monotonic() - started
        report = self.read_report()

        self.assertEqual(code, probe_module.EXIT_TIMEOUT)
        self.assertEqual(report['status'], 'timeout')
        self.assertFalse(report['ok'])
        self.assertEqual(report['error']['type'], 'ProbeTimeout')
        self.assertLess(elapsed, 5.0, 'probe exceeded its finite total timeout')
        self.assertGreater(report['elapsed_seconds'], 0)
        self.assertFalse(report['interaction']['physical_gui_clicked'])
        self.assertEqual(len(self.backend.fetch_calls), 1)
        # At most one send attempt; no fallback, retry, or alternate model.
        self.assertLessEqual(len(self.backend.reply_calls), 1)

    def test_existing_report_is_refused_without_creation_or_request(self):
        sentinel = b'{"keep":"this exact original report"}\n'
        self.report_path.write_bytes(sentinel)
        before = sorted(path.name for path in self.root.iterdir())
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            code = self.run_probe()

        self.assertEqual(code, probe_module.EXIT_REFUSED)
        self.assertEqual(self.report_path.read_bytes(), sentinel)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), before)
        self.assertEqual(self.backend.fetch_calls, [])
        self.assertEqual(self.backend.reply_calls, [])
        payload = json.loads(stderr.getvalue())
        self.assertEqual(payload['status'], 'refused')
        self.assertFalse(payload['ok'])
        self.assertIn('拒绝覆盖', payload['error']['message'])

    def test_existing_artifact_like_target_is_not_reused(self):
        # A directory with the report stem is not the artifact name, but it
        # demonstrates that prepare_artifact_dir always creates a new sibling
        # rather than selecting any pre-existing directory.
        existing = self.root / 'report.artifacts-existing'
        existing.mkdir()
        marker = existing / 'keep.txt'
        marker.write_text('do not touch', encoding='utf-8')

        code = self.run_probe()
        report = self.read_report()

        self.assertEqual(code, probe_module.EXIT_OK)
        self.assertEqual(marker.read_text(encoding='utf-8'), 'do not touch')
        self.assertNotEqual(Path(report['artifact_dir']), existing)
        self.assertTrue(Path(report['artifact_dir']).is_dir())


class _FakeSignal:
    def connect(self, *_args, **_kwargs):
        pass


class _FakeQApplication:
    def __init__(self, *_args, **_kwargs):
        self.screenAdded = _FakeSignal()
        self.screenRemoved = _FakeSignal()
        self.aboutToQuit = _FakeSignal()

    def setApplicationName(self, _name):
        pass

    def setOrganizationName(self, _name):
        pass

    def setQuitOnLastWindowClosed(self, _value):
        pass

    def installNativeEventFilter(self, _filter):
        pass

    def screens(self):
        return []

    def exec(self):
        return 0


class AppStartupRoutingTests(unittest.TestCase):
    def _normal_startup_mocks(self):
        fake_app = _FakeQApplication()
        local_socket = MagicMock()
        local_socket.waitForConnected.return_value = False
        server = MagicMock()
        return fake_app, local_socket, server

    def test_default_startup_does_not_enter_live_probe_or_request_models(self):
        # main() installs its own process-wide sys.excepthook; do not leave
        # that hook (which opens a modal error box) behind for other tests.
        saved_excepthook = sys.excepthook
        self.addCleanup(sys.__setattr__, 'excepthook', saved_excepthook)
        fake_app, local_socket, server = self._normal_startup_mocks()
        with patch.multiple(
            app_module,
            QApplication=MagicMock(return_value=fake_app),
            QLocalSocket=MagicMock(return_value=local_socket),
            QLocalServer=MagicMock(return_value=server),
            QSystemTrayIcon=MagicMock(),
            QTimer=MagicMock(),
            Store=MagicMock(),
            Sprites=MagicMock(),
            Controller=MagicMock(),
            VoiceEngine=MagicMock(),
            PetWindow=MagicMock(),
            Panel=MagicMock(),
            RecoveryHotkey=MagicMock(),
            native=MagicMock(),
            configure_logging=MagicMock(return_value=None),
            QMessageBox=MagicMock(),
        ), patch(
            'si_pet.genai_frozen_probe.run_frozen_genai_probe'
        ) as live_probe, patch.object(
            conversation_ui_module, 'fetch_models_request'
        ) as fetch_models, patch.object(
            conversation_ui_module, 'request_reply'
        ) as request_reply, patch.object(
            socket, 'socket', side_effect=AssertionError('network socket attempted')
        ), patch.object(
            socket, 'create_connection',
            side_effect=AssertionError('network connection attempted')
        ):
            code = app_module.main([])

        self.assertEqual(code, 0)
        live_probe.assert_not_called()
        fetch_models.assert_not_called()
        request_reply.assert_not_called()

    def test_hidden_argument_alone_returns_before_normal_app_startup(self):
        fake_app, _socket, _server = self._normal_startup_mocks()
        report_path = self.report_path()
        # patch.multiple() only puts DEFAULT-patched attributes into the dict
        # it yields to ``as``; with explicit MagicMock values the returned
        # dict is empty.  Keep our own mapping so every not-called assertion
        # still checks the real patch object.
        normal_mocks = {
            'Store': MagicMock(),
            'Sprites': MagicMock(),
            'Controller': MagicMock(),
            'VoiceEngine': MagicMock(),
            'PetWindow': MagicMock(),
            'Panel': MagicMock(),
            'QSystemTrayIcon': MagicMock(),
            'QLocalSocket': MagicMock(),
            'QLocalServer': MagicMock(),
            'configure_logging': MagicMock(),
        }
        with patch.multiple(
            app_module,
            QApplication=MagicMock(return_value=fake_app),
            **normal_mocks,
        ), patch(
            'si_pet.genai_frozen_probe.run_frozen_genai_probe', return_value=0
        ) as live_probe:
            code = app_module.main([
                '--check-genai-live', str(report_path),
            ])

        self.assertEqual(code, 0)
        live_probe.assert_called_once_with(fake_app, report_path)
        for name, mock in normal_mocks.items():
            with self.subTest(name=name):
                mock.assert_not_called()

    def test_hidden_argument_rejects_relative_report_path_before_qt(self):
        with patch.object(app_module, 'QApplication') as qt, patch(
            'si_pet.genai_frozen_probe.run_frozen_genai_probe'
        ) as live_probe, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                app_module.main(['--check-genai-live', 'relative-report.json'])
        self.assertEqual(caught.exception.code, 2)
        qt.assert_not_called()
        live_probe.assert_not_called()

    def test_hidden_argument_rejects_normal_startup_flags(self):
        with patch.object(app_module, 'QApplication') as qt, patch(
            'si_pet.genai_frozen_probe.run_frozen_genai_probe'
        ) as live_probe, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                app_module.main([
                    '--quiet', '--check-genai-live',
                    str(self.report_path()),
                ])
        self.assertEqual(caught.exception.code, 2)
        qt.assert_not_called()
        live_probe.assert_not_called()

    def report_path(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        return Path(folder.name) / 'report.json'


if __name__ == '__main__':
    unittest.main()
