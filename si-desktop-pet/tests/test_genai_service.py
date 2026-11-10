import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtCore import (QCoreApplication, QEventLoop, QObject, QProcess,
                            QTimer, Signal)

from si_pet.genai_service import (PROBE_ABSENT, PROBE_EXISTING,
                                 PROBE_FOREIGN, PROXY_ENV, STATE_CLOSED,
                                 STATE_CONNECTED, STATE_FAILED,
                                 STATE_LOGIN_REQUIRED, STATE_PORT_BUSY,
                                 STATE_PROBING, STATE_STARTED,
                                 STATE_STARTED_LOGIN_REQUIRED, STATE_STOPPING,
                                 STATE_STOP_LIMITED, STATE_STOPPED,
                                 GenAIService, ProbeResult,
                                 ProxyInstallError, _taskkill_arguments,
                                 find_proxy_install)


def make_install(root):
    root = Path(root)
    if os.name == 'nt':
        python = root / '.venv' / 'Scripts' / 'python.exe'
    else:
        python = root / '.venv' / 'bin' / 'python'
    python.parent.mkdir(parents=True)
    python.write_bytes(b'')
    (root / 'server.py').write_text('# test fixture; never imported\n', encoding='utf-8')
    return root


def existing(auth='valid', health='ok'):
    return ProbeResult(PROBE_EXISTING, auth, health)


class FixedProbe:
    def __init__(self, result):
        self.result = result
        self.calls = 0
        self.lock = threading.Lock()

    def __call__(self, _timeout):
        with self.lock:
            self.calls += 1
        return self.result


class SequenceProbe:
    """Controlled health responses, optionally holding the first call."""

    def __init__(self, results, first_gate=None):
        self.results = list(results)
        self.first_gate = first_gate
        self.calls = 0
        self.lock = threading.Lock()

    def __call__(self, _timeout):
        with self.lock:
            self.calls += 1
            index = min(self.calls - 1, len(self.results) - 1)
            gate = self.first_gate if self.calls == 1 else None
        if gate is not None:
            gate.wait(2)
        return self.results[index]


class FakeProcess(QObject):
    started = Signal()
    finished = Signal(int, int)
    errorOccurred = Signal(object)

    def __init__(self, pid=4242):
        super().__init__()
        self.pid = pid
        self._state = QProcess.ProcessState.NotRunning
        self.program = ''
        self.arguments = []
        self.working_directory = ''
        self.output_files = []
        self.error_files = []
        self.input_files = []
        self.terminate_calls = 0
        self.kill_calls = 0
        self.auto_finish = False

    def setProgram(self, value):
        self.program = value

    def setArguments(self, value):
        self.arguments = list(value)

    def setWorkingDirectory(self, value):
        self.working_directory = value

    def setStandardOutputFile(self, value):
        self.output_files.append(value)

    def setStandardErrorFile(self, value):
        self.error_files.append(value)

    def setStandardInputFile(self, value):
        self.input_files.append(value)

    def start(self):
        self._state = QProcess.ProcessState.Starting
        QTimer.singleShot(0, self._emit_started)

    def _emit_started(self):
        if self._state == QProcess.ProcessState.Starting:
            self._state = QProcess.ProcessState.Running
            self.started.emit()

    def state(self):
        return self._state

    def processId(self):
        if self._state == QProcess.ProcessState.NotRunning:
            return 0
        return self.pid

    def terminate(self):
        self.terminate_calls += 1
        if self.auto_finish:
            self.finish()

    def kill(self):
        self.kill_calls += 1
        if self.auto_finish:
            self.finish()

    def finish(self, code=0, status=0):
        if self._state == QProcess.ProcessState.NotRunning:
            return
        self._state = QProcess.ProcessState.NotRunning
        self.finished.emit(code, status)

    def deleteLater(self):
        # Keep the Python-side fake inspectable after Qt cleanup.
        pass


class GenAIServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self._taskkill_patch = mock.patch(
            'si_pet.genai_service._run_taskkill',
            side_effect=lambda _pid, _force: None,
        )
        self.taskkill = self._taskkill_patch.start()
        self.addCleanup(self._taskkill_patch.stop)
        self.addCleanup(self._cleanup_services)
        self.install = make_install(Path(self.temp.name) / 'genai-proxy')
        self.services = []
        self.processes = []

    def _cleanup_services(self):
        for service in self.services:
            try:
                service.close()
            except Exception:
                pass
        for process in self.processes:
            try:
                if process.state() != QProcess.ProcessState.NotRunning:
                    process.finish()
            except Exception:
                pass
        self.spin(lambda: True, 0.03)
        QCoreApplication.processEvents()

    def spin(self, predicate, timeout=1.5):
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
        loop.exec()
        poll.stop()
        return bool(predicate())

    def make_service(self, probe, *, proxy_dir=None, **kwargs):
        def factory():
            process = FakeProcess(pid=5000 + len(self.processes))
            self.processes.append(process)
            return process

        options = dict(
            probe_timeout=0.3,
            startup_timeout=0.6,
            poll_interval=0.02,
            stop_grace=0.05,
        )
        options.update(kwargs)
        service = GenAIService(
            proxy_dir=self.install if proxy_dir is None else proxy_dir,
            probe=probe,
            process_factory=factory,
            **options,
        )
        self.services.append(service)
        return service

    def test_construction_is_inert(self):
        probe = mock.Mock(side_effect=AssertionError('probe must not run'))
        service = self.make_service(probe)
        self.assertEqual(service.state, 'idle')
        self.assertEqual(probe.call_count, 0)
        self.assertEqual(self.processes, [])
        self.assertEqual(self.taskkill.call_count, 0)

    def test_event_loop_responds_while_probe_is_delayed(self):
        gate = threading.Event()
        probe = SequenceProbe([existing()], first_gate=gate)
        service = self.make_service(probe)
        statuses = []
        service.status_changed.connect(lambda state, message: statuses.append(state))
        heartbeat = {'count': 0}
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: heartbeat.__setitem__('count', heartbeat['count'] + 1))
        timer.start()
        try:
            began = time.monotonic()
            service.start()
            self.assertLess(time.monotonic() - began, 0.5)
            self.assertEqual(service.state, STATE_PROBING)
            self.assertTrue(self.spin(
                lambda: heartbeat['count'] >= 3 and probe.calls >= 1, 0.8))
            self.assertEqual(probe.calls, 1)
            gate.set()
            self.assertTrue(self.spin(lambda: service.state == STATE_CONNECTED))
        finally:
            timer.stop()
            gate.set()
        self.assertIn(STATE_PROBING, statuses)
        self.assertEqual(statuses[-1], STATE_CONNECTED)
        self.assertIn('尚未发送模型对话', service.message)

    def test_repeated_clicks_create_only_one_probe_and_process(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        probe = SequenceProbe([ProbeResult(PROBE_ABSENT), existing()], first_gate=gate)
        service = self.make_service(probe)
        for _ in range(5):
            service.start()
        self.assertEqual(service.state, STATE_PROBING)
        gate.set()
        self.assertTrue(self.spin(lambda: service.state == STATE_STARTED, 2.0))
        self.assertEqual(len(self.processes), 1)
        self.assertGreaterEqual(probe.calls, 2)
        calls = probe.calls
        service.start()
        self.spin(lambda: False, 0.05)
        self.assertEqual(probe.calls, calls)
        self.assertEqual(len(self.processes), 1)

        process = self.processes[0]
        self.assertIn(Path(process.program).name, ('python.exe', 'python'))
        self.assertEqual(process.arguments, [
            '-m', 'waitress', '--listen=127.0.0.1:8000', '--threads=8', 'server:app'])
        self.assertEqual(process.working_directory, str(self.install.resolve()))
        self.assertEqual(process.output_files, [QProcess.nullDevice()])
        self.assertEqual(process.error_files, [QProcess.nullDevice()])
        self.assertEqual(process.input_files, [QProcess.nullDevice()])

    def test_existing_service_is_never_started_claimed_or_stopped(self):
        probe = FixedProbe(existing())
        service = self.make_service(probe)
        service.start()
        self.assertTrue(self.spin(lambda: service.state == STATE_CONNECTED))
        self.assertEqual(probe.calls, 1)
        self.assertEqual(self.processes, [])
        self.assertIn('已有上科大 GenAI 反代', service.message)
        self.assertIn('尚未发送模型对话', service.message)

        service.stop_owned()
        self.assertEqual(service.state, STATE_STOPPED)
        self.assertIn('不是由本应用启动', service.message)
        self.assertEqual(self.taskkill.call_count, 0)

        service.close()
        self.assertEqual(service.state, STATE_CLOSED)
        self.assertEqual(self.taskkill.call_count, 0)

    def test_foreign_listener_refuses_to_start_or_connect(self):
        service = self.make_service(FixedProbe(ProbeResult(PROBE_FOREIGN)))
        service.start()
        self.assertTrue(self.spin(lambda: service.state == STATE_PORT_BUSY))
        self.assertEqual(self.processes, [])
        self.assertIn('已被其他程序占用', service.message)
        self.assertIn('不会连接', service.message)
        self.assertEqual(self.taskkill.call_count, 0)

    def test_missing_login_does_not_open_browser_or_login_script(self):
        service = self.make_service(
            FixedProbe(existing('missing', 'degraded')))
        with mock.patch('webbrowser.open') as browser, \
                mock.patch('os.startfile', create=True) as startfile:
            service.start()
            self.assertTrue(self.spin(lambda: service.state == STATE_LOGIN_REQUIRED))
        self.assertFalse(browser.called)
        self.assertFalse(startfile.called)
        self.assertEqual(self.processes, [])
        self.assertIn('登录', service.message)
        self.assertIn('不会自动打开浏览器', service.message)

    def test_owned_service_can_report_login_required_after_polling(self):
        probe = SequenceProbe([
            ProbeResult(PROBE_ABSENT),
            existing('expired', 'degraded'),
        ])
        service = self.make_service(probe)
        with mock.patch('webbrowser.open') as browser, \
                mock.patch('os.startfile', create=True) as startfile:
            service.start()
            self.assertTrue(self.spin(
                lambda: service.state == STATE_STARTED_LOGIN_REQUIRED, 2.0))
        self.assertFalse(browser.called)
        self.assertFalse(startfile.called)
        self.assertEqual(len(self.processes), 1)
        self.assertIn('服务已启动', service.message)
        self.assertIn('学校本人登录', service.message)

    def test_user_stop_ignores_late_probe_and_never_starts(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        probe = SequenceProbe([ProbeResult(PROBE_ABSENT)], first_gate=gate)
        service = self.make_service(probe)
        service.start()
        self.assertEqual(service.state, STATE_PROBING)
        service.stop_owned()
        self.assertEqual(service.state, STATE_STOPPED)
        gate.set()
        self.assertTrue(self.spin(lambda: probe.calls >= 1, 0.8))
        self.spin(lambda: False, 0.12)
        self.assertEqual(self.processes, [])
        self.assertEqual(self.taskkill.call_count, 0)

    def test_close_ignores_late_probe_and_never_starts(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        probe = SequenceProbe([ProbeResult(PROBE_ABSENT)], first_gate=gate)
        service = self.make_service(probe)
        service.start()
        self.assertEqual(service.state, STATE_PROBING)
        service.close()
        self.assertEqual(service.state, STATE_CLOSED)
        gate.set()
        self.assertTrue(self.spin(lambda: probe.calls >= 1, 0.8))
        self.spin(lambda: False, 0.12)
        self.assertEqual(self.processes, [])
        service.start()
        self.spin(lambda: False, 0.05)
        self.assertEqual(service.state, STATE_CLOSED)
        self.assertEqual(self.processes, [])
        self.assertEqual(self.taskkill.call_count, 0)

    def test_missing_server_file_is_rejected_before_process_start(self):
        broken = make_install(Path(self.temp.name) / 'broken-proxy')
        (broken / 'server.py').unlink()
        with self.assertRaises(ProxyInstallError):
            find_proxy_install(broken, environ={})
        service = self.make_service(
            FixedProbe(ProbeResult(PROBE_ABSENT)), proxy_dir=broken)
        service.start()
        self.assertTrue(self.spin(lambda: service.state == STATE_FAILED))
        self.assertEqual(self.processes, [])
        self.assertIn('server.py', service.message)

    def test_symlinked_install_is_rejected(self):
        target = make_install(Path(self.temp.name) / 'real-proxy')
        link = Path(self.temp.name) / 'linked-proxy'
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('symlink creation is unavailable on this system')
        with self.assertRaises(ProxyInstallError):
            find_proxy_install(link, environ={})

    def test_source_candidate_is_parent_sibling_only(self):
        project = Path(self.temp.name) / 'source-project'
        module = project / 'src' / 'si_pet' / 'genai_service.py'
        module.parent.mkdir(parents=True)
        module.write_text('', encoding='utf-8')
        # setUp already created the parent-sibling install; reuse it instead
        # of installing over the same directory.
        proxy = self.install
        install = find_proxy_install(
            environ={}, frozen=False, module_file=module)
        self.assertEqual(install.root, proxy.resolve())
        configured = find_proxy_install(environ={PROXY_ENV: str(proxy)})
        self.assertEqual(configured.root, proxy.resolve())
        executable = Path(self.temp.name) / 'SiDesktopPet' / 'Si-DesktopPet.exe'
        executable.parent.mkdir()
        frozen = find_proxy_install(
            environ={}, frozen=True, executable=executable)
        self.assertEqual(frozen.root, proxy.resolve())

    def test_stop_reports_limit_when_owned_parent_already_gone(self):
        probe = SequenceProbe([
            ProbeResult(PROBE_ABSENT),
            existing(),
        ])
        service = self.make_service(probe)
        service.start()
        self.assertTrue(self.spin(lambda: service.state == STATE_STARTED, 2.0))
        process = self.processes[0]
        # Simulate a launcher whose parent vanished without an observable
        # finished event: no PID remains that can be safely tied to our tree.
        process._state = QProcess.ProcessState.NotRunning
        service.stop_owned()
        self.assertEqual(service.state, STATE_STOP_LIMITED)
        self.assertIn('未按端口或名称结束', service.message)
        self.assertEqual(self.taskkill.call_count, 0)

    def test_stop_only_owned_process_tree(self):
        if os.name != 'nt':
            self.skipTest('taskkill process-tree checks are Windows-specific')
        probe = SequenceProbe([
            ProbeResult(PROBE_ABSENT),
            existing(),
        ])
        service = self.make_service(probe)
        service.start()
        self.assertTrue(self.spin(lambda: service.state == STATE_STARTED, 2.0))
        self.assertEqual(len(self.processes), 1)
        process = self.processes[0]
        pid = process.processId()
        self.assertGreater(pid, 0)
        self.taskkill.reset_mock()

        service.stop_owned()
        self.assertEqual(service.state, STATE_STOPPING)
        self.assertEqual(process.kill_calls, 0)
        self.assertTrue(self.spin(lambda: self.taskkill.call_count >= 2, 0.8))
        calls = [call.args for call in self.taskkill.call_args_list]
        self.assertIn((pid, False), calls)
        self.assertIn((pid, True), calls)
        self.assertTrue(all(call[0] == pid for call in calls))

        gentle = _taskkill_arguments(pid, False)
        forced = _taskkill_arguments(pid, True)
        self.assertEqual(gentle[:3], ['taskkill', '/PID', str(pid)])
        self.assertIn('/T', gentle)
        self.assertNotIn('/F', gentle)
        self.assertIn('/T', forced)
        self.assertIn('/F', forced)

        process.finish()
        self.assertTrue(self.spin(lambda: service.state == STATE_STOPPED))
        self.assertEqual(service.owned_process_id, 0)


if __name__ == '__main__':
    unittest.main()
