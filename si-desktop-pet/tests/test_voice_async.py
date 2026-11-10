"""Async VoiceStudio health probe and backend ownership tests.

The real client always talks to 127.0.0.1:3900. Each test installs a stand-in
``HTTPConnection`` that redirects only those loopback connections to a real
local server bound to an ephemeral port, so no test touches a real VoiceStudio.
"""
import http.client
import inspect
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtCore import (QCoreApplication, QEventLoop, QObject, QProcess,
                            QTimer, Signal, Slot)

from si_pet.voice import VoiceEngine
from si_pet.voice_client import HEALTH_TIMEOUT, VoiceClient


class _StubServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address, handler):
        super().__init__(address, handler)
        self.delay = 0.0
        self.heartbeat_duration = 0.0
        self.health_status = 200
        self.health_body = json.dumps({'status': 'ok'}).encode('utf-8')
        self.requests = []
        self.condition = threading.Condition()
        self.stop_event = threading.Event()

    def record(self, path):
        with self.condition:
            self.requests.append(path)
            self.condition.notify_all()

    def wait_for_requests(self, count=1, timeout=5.0):
        deadline = time.monotonic() + timeout
        with self.condition:
            while len(self.requests) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(remaining)
        return True


class _HealthHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.close_connection = True
        server = self.server
        server.record(self.path)
        if self.path == '/profiles':
            self._send(200, b'[]')
            return
        if self.path != '/health':
            self._send(404, b'')
            return
        if server.heartbeat_duration:
            self._heartbeat()
            return
        if server.delay and server.stop_event.wait(server.delay):
            return
        self._send(server.health_status, server.health_body)

    def _heartbeat(self):
        server = self.server
        try:
            self.wfile.write(b'HTTP/1.1 200 OK\r\n'
                             b'Content-Type: application/json; charset=utf-8\r\n')
            self.wfile.flush()
            deadline = time.monotonic() + server.heartbeat_duration
            while not server.stop_event.is_set() and time.monotonic() < deadline:
                self.wfile.write(b'X-Heartbeat: 1\r\n')
                self.wfile.flush()
                server.stop_event.wait(0.2)
            if server.stop_event.is_set():
                return
            body = server.health_body
            self.wfile.write(
                f'Content-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode('ascii') + body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _send(self, status, body):
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


class _StatusRecorder(QObject):
    def __init__(self):
        super().__init__()
        self.messages = []
        self.thread_ids = []

    @Slot(str)
    def record(self, message):
        self.messages.append(message)
        self.thread_ids.append(threading.get_ident())


class FakeProcess(QObject):
    """QProcess stand-in that never launches a real program."""

    ProcessState = QProcess.ProcessState
    ProcessChannelMode = QProcess.ProcessChannelMode

    started = Signal()
    finished = Signal(int, int)
    errorOccurred = Signal(object)

    instances = []

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = QProcess.ProcessState.NotRunning
        self.program = ''
        self.arguments = []
        self.working_directory = ''
        self.start_calls = 0
        self.terminate_calls = 0
        self.kill_calls = 0
        FakeProcess.instances.append(self)

    def setProgram(self, value):
        self.program = str(value)

    def setArguments(self, value):
        self.arguments = list(value)

    def setWorkingDirectory(self, value):
        self.working_directory = str(value)

    def setProcessChannelMode(self, _mode):
        pass

    def start(self):
        self.start_calls += 1
        self._state = QProcess.ProcessState.Starting
        QTimer.singleShot(0, self._emit_started)

    def _emit_started(self):
        if self._state == QProcess.ProcessState.Starting:
            self._state = QProcess.ProcessState.Running
            self.started.emit()

    def state(self):
        return self._state

    def terminate(self):
        self.terminate_calls += 1
        self._state = QProcess.ProcessState.NotRunning

    def kill(self):
        self.kill_calls += 1
        self._state = QProcess.ProcessState.NotRunning

    def waitForFinished(self, _milliseconds):
        return self._state == QProcess.ProcessState.NotRunning

    def deleteLater(self):
        pass


class VoiceEngineAsyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        self.httpd = _StubServer(('127.0.0.1', 0), _HealthHandler)
        self.server_thread = threading.Thread(
            target=self.httpd.serve_forever,
            kwargs={'poll_interval': 0.02},
            name='voice-health-stub',
            daemon=True,
        )
        self.server_thread.start()
        self.addCleanup(self._stop_server)

        self.appdata = Path(self.temp.name) / 'appdata'
        self.appdata.mkdir()
        self.runtime = self._make_runtime()

        self.connection_attempts = []
        attempts = self.connection_attempts
        server_port = self.httpd.server_port

        class RedirectingHTTPConnection(http.client.HTTPConnection):
            def __init__(self, host, port=3900, *args, **kwargs):
                requested_port = port
                if str(host) == '127.0.0.1' and port == 3900:
                    port = server_port
                attempts.append((str(host), requested_port, port,
                                 kwargs.get('timeout')))
                super().__init__(host, port, *args, **kwargs)

        self._http_patch = mock.patch.object(
            http.client, 'HTTPConnection', RedirectingHTTPConnection)
        self._http_patch.start()
        self.addCleanup(self._http_patch.stop)

        FakeProcess.instances = list()
        self._process_patch = mock.patch('si_pet.voice.QProcess', FakeProcess)
        self._process_patch.start()
        self.addCleanup(self._process_patch.stop)

        self._appdata_patch = mock.patch.dict(os.environ, {'APPDATA': str(self.appdata)})
        self._appdata_patch.start()
        self.addCleanup(self._appdata_patch.stop)

        self._data_root_patch = mock.patch(
            'si_pet.voice.data_root', return_value=self.appdata / 'data')
        self._data_root_patch.start()
        self.addCleanup(self._data_root_patch.stop)

        self._winsound_patch = mock.patch('si_pet.voice.winsound', None)
        self._winsound_patch.start()
        self.addCleanup(self._winsound_patch.stop)

    def _make_runtime(self):
        runtime = self.appdata / 'VoiceStudio' / 'runtime'
        python = runtime / 'project' / '.venv' / 'Scripts' / 'python.exe'
        backend = runtime / 'project' / 'backend' / 'main.py'
        python.parent.mkdir(parents=True)
        python.write_bytes(b'')
        backend.parent.mkdir(parents=True)
        backend.write_text('# test fixture; never started\n', encoding='utf-8')
        location = self.appdata / 'VoiceStudio' / 'runtime-location.json'
        location.write_text(json.dumps({'root': str(runtime)}), encoding='utf-8')
        return runtime

    def _stop_server(self):
        self.httpd.stop_event.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.server_thread.join(2.0)

    def make_engine(self):
        engine = VoiceEngine({
            'voice_enabled': False,
            'voice_profile_id': '06332768',
            'voice_volume': 65,
        })
        self.addCleanup(engine.stop)
        recorder = _StatusRecorder()
        engine.status.connect(recorder.record)
        return engine, recorder

    def spin(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(10)

        def check():
            try:
                done = bool(predicate())
            except Exception:
                done = True
            if done or time.monotonic() >= deadline:
                loop.quit()

        poll.timeout.connect(check)
        poll.start()
        QTimer.singleShot(0, check)
        loop.exec()
        poll.stop()
        try:
            return bool(predicate())
        except Exception:
            return False

    def spin_for(self, duration):
        deadline = time.monotonic() + duration
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(10)

        def check():
            if time.monotonic() >= deadline:
                loop.quit()

        poll.timeout.connect(check)
        poll.start()
        QTimer.singleShot(0, check)
        loop.exec()
        poll.stop()

    def test_probe_runs_off_qt_thread_and_success_launches_no_process(self):
        self.httpd.delay = 0.3
        engine, recorder = self.make_engine()
        main_thread = threading.get_ident()
        probe_threads = []
        original = VoiceClient.health

        def tracked(client, *args, **kwargs):
            probe_threads.append(threading.get_ident())
            return original(client, *args, **kwargs)

        with mock.patch.object(VoiceClient, 'health', tracked):
            started = time.monotonic()
            engine.start_backend()
            self.assertLess(time.monotonic() - started, 0.2)
            self.assertTrue(self.spin(
                lambda: any('已连接现有 VoiceStudio 引擎' in m for m in recorder.messages),
                timeout=3.0))

        self.assertEqual(len(probe_threads), 1)
        self.assertNotEqual(probe_threads[0], main_thread)
        self.assertTrue(recorder.thread_ids)
        self.assertTrue(all(tid == main_thread for tid in recorder.thread_ids))
        self.assertIsNone(engine.backend)
        self.assertFalse(FakeProcess.instances)
        self.assertEqual(self.httpd.requests, ['/health'])

    def test_slow_health_server_times_out_shortly_then_starts_local_backend(self):
        self.httpd.delay = 3.0
        engine, recorder = self.make_engine()
        started = time.monotonic()
        engine.start_backend()
        self.assertTrue(self.spin(lambda: bool(FakeProcess.instances), timeout=4.0))
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, HEALTH_TIMEOUT + 1.5)
        self.assertTrue(self.httpd.wait_for_requests(1, timeout=1.0))
        self.assertFalse(any('已连接现有 VoiceStudio 引擎' in m for m in recorder.messages))
        process = FakeProcess.instances[0]
        self.assertIs(engine.backend, process)
        self.assertEqual(process.start_calls, 1)
        self.assertEqual(process.program,
                         str(self.runtime / 'project' / '.venv' / 'Scripts' / 'python.exe'))
        self.assertEqual(process.arguments, ['-u', 'main.py'])

    def test_slow_heartbeat_cannot_extend_health_timeout(self):
        self.httpd.heartbeat_duration = 3.0
        engine, recorder = self.make_engine()
        started = time.monotonic()
        engine.start_backend()
        self.assertTrue(self.spin(lambda: bool(FakeProcess.instances), timeout=4.0))
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, HEALTH_TIMEOUT + 1.5)
        self.assertFalse(any('已连接现有 VoiceStudio 引擎' in m for m in recorder.messages))
        self.assertEqual(FakeProcess.instances[0].start_calls, 1)

    def test_concurrent_start_backend_calls_share_one_probe(self):
        self.httpd.delay = 0.4
        engine, recorder = self.make_engine()
        probe_calls = []
        original = VoiceClient.health

        def tracked(client, *args, **kwargs):
            probe_calls.append(1)
            return original(client, *args, **kwargs)

        with mock.patch.object(VoiceClient, 'health', tracked):
            engine.start_backend()
            engine.start_backend()
            engine.start_backend()
            self.assertTrue(self.spin(
                lambda: any('已连接现有 VoiceStudio 引擎' in m for m in recorder.messages),
                timeout=3.0))

        self.assertEqual(len(probe_calls), 1)
        self.assertEqual([p for p in self.httpd.requests if p == '/health'], ['/health'])
        self.assertFalse(FakeProcess.instances)

    def test_stop_during_probe_discards_late_result_and_never_launches(self):
        self.httpd.delay = 0.6
        self.httpd.health_status = 503
        engine, recorder = self.make_engine()
        engine.start_backend()
        self.assertTrue(self.httpd.wait_for_requests(1, timeout=2.0))
        before = len(recorder.messages)
        engine.stop()

        self.spin_for(HEALTH_TIMEOUT + 1.0)

        self.assertFalse(FakeProcess.instances)
        self.assertIsNone(engine.backend)
        self.assertFalse(any(
            '已连接现有 VoiceStudio 引擎' in m or 'VoiceStudio 引擎正在启动' in m
            for m in recorder.messages[before:]))
        self.assertFalse(engine._probing)
        self.assertTrue(engine._backend_shutdown)

    def test_failed_health_launches_owned_backend_and_stop_skips_foreign_process(self):
        self.httpd.health_status = 503
        engine, _recorder = self.make_engine()
        engine.start_backend()
        self.assertTrue(self.spin(lambda: bool(FakeProcess.instances), timeout=3.0))
        owned = FakeProcess.instances[0]
        self.assertIs(engine.backend, owned)
        self.assertEqual(owned.working_directory,
                         str(self.runtime / 'project' / 'backend'))

        foreign = FakeProcess()
        foreign._state = QProcess.ProcessState.Running
        engine.backend = foreign
        engine.stop()

        self.assertEqual(owned.terminate_calls, 1)
        self.assertEqual(foreign.terminate_calls, 0)
        self.assertTrue(engine._backend_shutdown)

    def test_health_failure_without_runtime_reports_and_launches_nothing(self):
        self.httpd.health_status = 503
        (self.appdata / 'VoiceStudio' / 'runtime-location.json').unlink()
        engine, recorder = self.make_engine()
        engine.start_backend()
        self.assertTrue(self.spin(
            lambda: any('未找到 VoiceStudio 本地运行环境' in m for m in recorder.messages),
            timeout=3.0))
        self.assertFalse(FakeProcess.instances)
        self.assertIsNone(engine.backend)

    def test_health_timeout_is_short_while_synthesis_timeout_stays_180(self):
        with tempfile.TemporaryDirectory() as cache:
            client = VoiceClient(cache)
            self.assertEqual(client.timeout, 180)
            self.assertLessEqual(HEALTH_TIMEOUT, 2.0)
            self.assertEqual(
                inspect.signature(VoiceClient.health).parameters['timeout'].default,
                HEALTH_TIMEOUT)
            self.assertTrue(client.health())
            client.profile_exists('06332768')

        attempts = self.connection_attempts
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0][3], HEALTH_TIMEOUT)
        self.assertEqual(attempts[1][3], 180)
        for host, requested_port, actual_port, _timeout in attempts:
            self.assertEqual((host, requested_port), ('127.0.0.1', 3900))
            self.assertEqual((host, actual_port), ('127.0.0.1', self.httpd.server_port))


if __name__ == '__main__':
    unittest.main()
