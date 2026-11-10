"""User-triggered lifecycle control for the local ShanghaiTech GenAI proxy.

This module deliberately knows only two HTTP endpoints, ``/`` and ``/health``,
on ``127.0.0.1:8000``.  Constructing :class:`GenAIService` performs no I/O and
starts no threads.  A network probe runs only after the user calls ``start()``,
and every probe is executed off the Qt thread.

The service never reads a school token, never calls a login helper, never opens
a browser, and never sends a models or chat request.  It also never treats a
healthy ``/health`` response as proof that a real model conversation succeeded.
"""

from __future__ import annotations

import errno
import http.client
import json
import os
from dataclasses import dataclass
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from typing import Mapping

from PySide6.QtCore import QObject, QProcess, QTimer, Qt, Signal

SERVICE_HOST = '127.0.0.1'
SERVICE_PORT = 8000
SERVICE_ADDRESS = f'{SERVICE_HOST}:{SERVICE_PORT}'
SERVICE_ID = 'ShanghaiTech GenAI OpenAI Proxy'
PROXY_ENV = 'SI_GENAI_PROXY_DIR'
WAITRESS_ARGV = (
    '-m', 'waitress',
    f'--listen={SERVICE_ADDRESS}',
    '--threads=8',
    'server:app',
)
PROBE_TIMEOUT = 1.6
STARTUP_TIMEOUT = 8.0
POLL_INTERVAL = 0.4
STOP_GRACE = 1.2
PROBE_BODY_LIMIT = 64 * 1024

# Probe result kinds.
PROBE_ABSENT = 'absent'
PROBE_EXISTING = 'existing'
PROBE_FOREIGN = 'foreign'
PROBE_UNKNOWN = 'unknown'
PROBE_TIMEOUT_RESULT = 'timeout'

# Public states carried by ``status_changed``.
STATE_IDLE = 'idle'
STATE_PROBING = 'probing'
STATE_STARTING = 'starting'
STATE_STARTED = 'started'
STATE_STARTED_LOGIN_REQUIRED = 'started_login_required'
STATE_STARTED_UNVERIFIED = 'started_unverified'
STATE_CONNECTED = 'connected'
STATE_LOGIN_REQUIRED = 'login_required'
STATE_RUNNING_UNVERIFIED = 'running_unverified'
STATE_PORT_BUSY = 'port_busy'
STATE_FAILED = 'failed'
STATE_STOPPING = 'stopping'
STATE_STOPPED = 'stopped'
STATE_STOP_LIMITED = 'stopped_limited'
STATE_CLOSED = 'closed'

_EXISTING_STATES = {
    STATE_CONNECTED,
    STATE_LOGIN_REQUIRED,
    STATE_RUNNING_UNVERIFIED,
    STATE_STARTED,
    STATE_STARTED_LOGIN_REQUIRED,
    STATE_STARTED_UNVERIFIED,
}


@dataclass(frozen=True)
class ProbeResult:
    """A small, sanitized result from the loopback health probe."""

    kind: str
    upstream_auth: str = ''
    health: str = ''


@dataclass(frozen=True)
class ProxyInstall:
    """Validated local proxy installation; no user data is read here."""

    root: Path
    python: Path
    server: Path


class ProxyInstallError(RuntimeError):
    """Raised when a fixed/explicit installation path is not safe or complete."""


def _is_link(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        junction = getattr(path, 'is_junction', None)
        return bool(junction and junction())
    except OSError:
        return True


def _reject_links(path: Path) -> None:
    """Reject a directory/file reached through a symlink or junction.

    Only the explicitly selected path (and its ancestors) is inspected.  There
    is no directory walking or disk-wide search.
    """
    current = path.absolute()
    while True:
        if _is_link(current):
            raise ProxyInstallError('反代安装路径包含链接，出于安全考虑未启动。')
        parent = current.parent
        if parent == current:
            return
        current = parent


def _validate_install(root: Path) -> ProxyInstall:
    if not root.is_absolute():
        raise ProxyInstallError('反代安装路径必须是绝对路径。')
    try:
        _reject_links(root)
    except OSError as exc:
        raise ProxyInstallError('无法安全检查反代安装路径。') from exc
    if not root.exists() or not root.is_dir():
        raise ProxyInstallError('未找到已安装的 genai-proxy 目录。')
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise ProxyInstallError('无法解析反代安装目录。') from exc
    if _is_link(root):
        raise ProxyInstallError('反代安装目录是链接，出于安全考虑未启动。')

    server = root / 'server.py'
    if _is_link(server) or not server.is_file():
        raise ProxyInstallError('反代目录缺少安全的 server.py，未启动。')

    if os.name == 'nt':
        candidates = (
            root / '.venv' / 'Scripts' / 'python.exe',
            root / 'Scripts' / 'python.exe',
            root / 'python.exe',
        )
    else:
        candidates = (
            root / '.venv' / 'bin' / 'python',
            root / 'bin' / 'python',
            root / 'python',
        )
    for python in candidates:
        if _is_link(python):
            continue
        if python.is_file():
            return ProxyInstall(root=root, python=python, server=server)
    raise ProxyInstallError('反代目录中未找到本地 Python（.venv），未启动。')


def _source_candidates(module_file: Path, frozen: bool, executable: Path):
    candidates = []
    if frozen:
        exe = executable.expanduser().absolute()
        candidates.append(exe.parent.parent / 'genai-proxy')
        return candidates
    module = module_file.expanduser().absolute()
    # .../<project>/src/si_pet/genai_service.py -> ../genai-proxy
    for parent in module.parents:
        if parent.name == 'si_pet' and parent.parent.name == 'src':
            candidates.append(parent.parent.parent.parent / 'genai-proxy')
            break
    if not candidates:
        # Keep the source fallback deterministic instead of searching.
        project = module.parents[2] if len(module.parents) >= 3 else module.parent
        candidates.append(project.parent / 'genai-proxy')
    return candidates


def find_proxy_install(explicit_dir=None, *, environ: Mapping[str, str] | None = None,
                       frozen: bool | None = None, executable=None,
                       module_file=None) -> ProxyInstall:
    """Locate a trusted installation from explicit/fixed candidates only.

    An explicit argument or ``SI_GENAI_PROXY_DIR`` is authoritative: an invalid
    value is reported rather than silently falling back to another installation.
    Otherwise only the frozen executable's parent sibling and the source
    project's parent sibling are considered.
    """
    env = os.environ if environ is None else environ
    if explicit_dir is not None:
        root = Path(explicit_dir).expanduser()
    else:
        configured = env.get(PROXY_ENV, '').strip()
        if configured:
            root = Path(configured).expanduser()
        else:
            is_frozen = bool(getattr(sys, 'frozen', False)) if frozen is None else bool(frozen)
            exe = Path(executable) if executable is not None else Path(sys.executable)
            src = Path(module_file) if module_file is not None else Path(__file__)
            last_error = None
            for candidate in _source_candidates(src, is_frozen, exe):
                try:
                    return _validate_install(candidate)
                except ProxyInstallError as exc:
                    last_error = exc
                    continue
            raise ProxyInstallError(str(last_error) if last_error
                                    else '未找到已安装的 genai-proxy。') from None
    return _validate_install(root)


def _remaining(deadline: float) -> float:
    return max(0.05, deadline - time.monotonic())


def _request_json(connection: http.client.HTTPConnection, path: str,
                  deadline: float):
    """GET one path; return (status, decoded JSON or None).

    The connection is a raw loopback HTTPConnection, so system proxy settings
    are irrelevant and redirects are never followed.
    """
    connection.timeout = _remaining(deadline)
    connection.request('GET', path, headers={
        'Accept': 'application/json',
        'Connection': 'close',
    })
    response = connection.getresponse()
    try:
        status = response.status
        raw = response.read(PROBE_BODY_LIMIT + 1)
    finally:
        response.close()
    if status != 200 or len(raw) > PROBE_BODY_LIMIT:
        return status, None
    try:
        return status, json.loads(raw.decode('utf-8'))
    except (UnicodeError, ValueError):
        return status, None


def probe_genai_service(timeout: float = PROBE_TIMEOUT) -> ProbeResult:
    """Probe only ``127.0.0.1:8000`` and only ``/`` plus ``/health``.

    A connection failure means no local listener.  Any listener that does not
    identify itself as this proxy is reported as foreign so callers can refuse
    to start a duplicate or talk to the wrong program.
    """
    timeout = max(0.2, float(timeout))
    deadline = time.monotonic() + timeout
    connection = http.client.HTTPConnection(
        SERVICE_HOST, SERVICE_PORT, timeout=timeout)
    try:
        try:
            root_status, root = _request_json(connection, '/', deadline)
        except ConnectionRefusedError:
            return ProbeResult(PROBE_ABSENT)
        except (TimeoutError, socket.timeout):
            return ProbeResult(PROBE_UNKNOWN)
        except OSError as exc:
            if exc.errno in (errno.ECONNREFUSED, errno.ECONNRESET):
                return ProbeResult(PROBE_ABSENT if exc.errno == errno.ECONNREFUSED
                                   else PROBE_UNKNOWN)
            return ProbeResult(PROBE_UNKNOWN)
        except (http.client.HTTPException, ValueError, UnicodeError):
            return ProbeResult(PROBE_UNKNOWN)

        if root_status != 200 or not isinstance(root, dict) \
                or root.get('service') != SERVICE_ID:
            return ProbeResult(PROBE_FOREIGN)

        try:
            health_status, health = _request_json(connection, '/health', deadline)
        except (OSError, http.client.HTTPException, ValueError, UnicodeError):
            return ProbeResult(PROBE_EXISTING, upstream_auth='unknown')
        if not isinstance(health, dict):
            return ProbeResult(PROBE_EXISTING, upstream_auth='unknown')
        status = health.get('status')
        auth = health.get('upstream_auth')
        if status not in ('ok', 'degraded') or auth not in ('valid', 'missing', 'expired'):
            return ProbeResult(PROBE_EXISTING, upstream_auth='unknown',
                               health=status if isinstance(status, str) else '')
        return ProbeResult(PROBE_EXISTING, upstream_auth=auth, health=status)
    finally:
        connection.close()


def _taskkill_arguments(pid: int, force: bool):
    """Fixed, shell-free command for an owned process tree."""
    return ['taskkill', '/PID', str(int(pid)), '/T'] + (['/F'] if force else [])


def _run_taskkill(pid: int, force: bool) -> None:
    """Stop only the tree rooted at ``pid``; output is discarded."""
    if os.name != 'nt':
        return
    try:
        subprocess.run(
            _taskkill_arguments(pid, force),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except (OSError, subprocess.SubprocessError):
        # The caller still has the QProcess handle; it can report the limit.
        pass


def _hide_windows_console(process) -> None:
    """Ask Qt to hide a console window when the binding exposes the hook."""
    if os.name != 'nt':
        return
    modifier = getattr(process, 'setCreateProcessArgumentsModifier', None)
    if modifier is None:
        return

    def hide(arguments):
        try:
            arguments.flags |= 0x08000000  # CREATE_NO_WINDOW
            startup = getattr(arguments, 'startupInfo', None)
            if startup is not None:
                startup.dwFlags |= 0x00000001  # STARTF_USESHOWWINDOW
                startup.wShowWindow = 0       # SW_HIDE
        except Exception:
            pass

    try:
        modifier(hide)
    except (RuntimeError, TypeError):
        pass


class GenAIService(QObject):
    """Qt-thread-owned controller for a user-started local GenAI proxy.

    ``status_changed`` carries ``(state, message)``.  Construction is inert.
    ``start()`` probes once in a worker, then either reports the existing
    service, refuses a foreign listener, or starts one QProcess.  ``stop()`` and
    ``stop_owned()`` can only terminate a process started by this object.
    """

    status_changed = Signal(str, str)
    _probe_ready = Signal(int, object, object, str)
    _poll_ready = Signal(int, object)

    def __init__(self, proxy_dir=None, parent=None, *, probe=None,
                 process_factory=None, probe_timeout: float = PROBE_TIMEOUT,
                 startup_timeout: float = STARTUP_TIMEOUT,
                 poll_interval: float = POLL_INTERVAL,
                 stop_grace: float = STOP_GRACE):
        super().__init__(parent)
        self._proxy_dir = proxy_dir
        self._probe = probe or probe_genai_service
        self._process_factory = process_factory
        self._probe_timeout = max(0.2, float(probe_timeout))
        self._startup_timeout = max(0.2, float(startup_timeout))
        self._poll_interval = max(0.02, float(poll_interval))
        self._stop_grace = max(0.05, float(stop_grace))

        self._state = STATE_IDLE
        self._message = '尚未连接或启动本机反代。'
        self._generation = 0
        self._active = False
        self._closed = False
        self._poll_cancel = None
        self._owned_process = None
        self._owned_pid = 0
        self._stopping = False
        self._stop_when_started = False

        # Explicit queued connections keep worker-thread results on the Qt
        # thread that created this object.
        self._probe_ready.connect(
            self._on_probe_ready, Qt.ConnectionType.QueuedConnection)
        self._poll_ready.connect(
            self._on_poll_ready, Qt.ConnectionType.QueuedConnection)

    @property
    def state(self) -> str:
        return self._state

    @property
    def message(self) -> str:
        return self._message

    @property
    def owned_process_id(self) -> int:
        return self._owned_pid

    def _set_status(self, state: str, message: str) -> None:
        self._state = state
        self._message = message
        try:
            self.status_changed.emit(state, message)
        except (RuntimeError, TypeError):
            pass

    def _cancel_work(self) -> None:
        self._generation += 1
        self._active = False
        cancel = self._poll_cancel
        self._poll_cancel = None
        if cancel is not None:
            cancel.set()

    def start(self) -> None:
        """Probe and, only if needed, start one proxy for this user action."""
        if self._closed or self._stopping:
            return
        if self._active or self._owned_process is not None:
            return

        # A user click may refresh a previously observed external service.  It
        # still probes only; an existing service is never duplicated or claimed.
        token = self._generation + 1
        self._generation = token
        self._active = True
        self._set_status(
            STATE_PROBING,
            '正在检查本机 127.0.0.1:8000 是否已有反代；不会连接其它服务。',
        )
        try:
            threading.Thread(
                target=self._probe_worker,
                args=(token,),
                daemon=True,
                name='si-genai-probe',
            ).start()
        except RuntimeError:
            self._active = False
            self._set_status(STATE_FAILED, '无法启动本地检查线程；未启动反代。')

    def _probe_worker(self, token: int) -> None:
        result = self._safe_probe()
        install = None
        error = ''
        if result.kind == PROBE_ABSENT:
            try:
                install = find_proxy_install(self._proxy_dir)
            except ProxyInstallError as exc:
                error = str(exc)
            except Exception:
                error = '无法检查本机反代安装，未启动。'
        try:
            self._probe_ready.emit(token, result, install, error)
        except (RuntimeError, TypeError):
            pass

    def _safe_probe(self) -> ProbeResult:
        try:
            result = self._probe(self._probe_timeout)
        except Exception:
            return ProbeResult(PROBE_UNKNOWN)
        return result if isinstance(result, ProbeResult) else ProbeResult(PROBE_UNKNOWN)

    def _on_probe_ready(self, token, result, install, error) -> None:
        if token != self._generation or not self._active or self._closed:
            return
        self._active = False
        if not isinstance(result, ProbeResult):
            result = ProbeResult(PROBE_UNKNOWN)

        if result.kind == PROBE_EXISTING:
            self._report_existing(result)
            return
        if result.kind == PROBE_FOREIGN:
            self._set_status(
                STATE_PORT_BUSY,
                '127.0.0.1:8000 已被其他程序占用，未启动反代，也不会连接该程序进行模型对话。'
                '请先释放端口再试。',
            )
            return
        if result.kind != PROBE_ABSENT:
            self._set_status(
                STATE_FAILED,
                '无法确认 127.0.0.1:8000 的状态（可能被其他程序占用或反代无响应）；'
                '为避免重复启动已停止本次操作，也未连接其它服务。',
            )
            return
        if error or not isinstance(install, ProxyInstall):
            self._set_status(
                STATE_FAILED,
                error or '未找到可用的 genai-proxy 安装（需要 server.py 和本地 Python），未启动。',
            )
            return
        self._start_owned(install)

    def _report_existing(self, result: ProbeResult) -> None:
        if result.upstream_auth in ('missing', 'expired'):
            self._set_status(
                STATE_LOGIN_REQUIRED,
                '本机已有上科大 GenAI 反代在运行，但学校登录缺失或已过期。'
                '请本人完成登录后重试；不会自动打开浏览器或运行登录脚本。',
            )
        elif result.upstream_auth == 'valid' and result.health == 'ok':
            self._set_status(
                STATE_CONNECTED,
                '本机已有上科大 GenAI 反代在运行，健康检查通过；未启动重复服务，'
                '也尚未发送模型对话。',
            )
        else:
            self._set_status(
                STATE_RUNNING_UNVERIFIED,
                '本机已有上科大 GenAI 反代在运行，但健康状态无法确认；'
                '未启动重复服务，也不会连接其它程序。',
            )

    def _start_owned(self, install: ProxyInstall) -> None:
        process = None
        try:
            factory = self._process_factory
            process = factory() if factory is not None else QProcess(self)
            if not isinstance(process, QObject):
                raise TypeError('process factory must return a QObject')
            if process.parent() is None:
                process.setParent(self)
            process.setProgram(str(install.python))
            process.setArguments(list(WAITRESS_ARGV))
            process.setWorkingDirectory(str(install.root))
            # Direct file redirection avoids pipes and any credential-bearing
            # output accumulating in this process.
            null_device = QProcess.nullDevice()
            process.setStandardOutputFile(null_device)
            process.setStandardErrorFile(null_device)
            process.setStandardInputFile(null_device)
            _hide_windows_console(process)
            process.started.connect(lambda: self._on_process_started(process))
            process.finished.connect(
                lambda code, status: self._on_process_finished(process, code, status))
            process.errorOccurred.connect(
                lambda error: self._on_process_error(process, error))
            self._owned_process = process
            self._owned_pid = 0
            self._active = True
            self._stopping = False
            self._stop_when_started = False
            self._set_status(
                STATE_STARTING,
                '正在启动本机反代（仅监听 127.0.0.1:8000），稍候检查健康状态。',
            )
            process.start()
        except Exception:
            self._discard_process(process)
            self._active = False
            self._set_status(
                STATE_FAILED,
                '反代进程无法启动；未自动重试，也未连接其它服务。',
            )

    def _discard_process(self, process) -> None:
        if process is not self._owned_process:
            return
        self._owned_process = None
        self._owned_pid = 0
        try:
            process.deleteLater()
        except (RuntimeError, AttributeError):
            pass

    def _on_process_started(self, process) -> None:
        if process is not self._owned_process:
            return
        try:
            pid = int(process.processId() or 0)
        except (RuntimeError, TypeError, ValueError):
            pid = 0
        self._owned_pid = pid
        if self._stopping or self._stop_when_started or self._closed:
            self._stop_when_started = False
            if self._closed:
                self._stopping = True
            self._begin_process_stop(process)
            return
        self._set_status(
            STATE_STARTING,
            '反代进程已启动，正在等待本机健康检查；尚未发送模型对话。',
        )
        self._start_health_poll(self._generation)

    def _start_health_poll(self, token: int) -> None:
        cancel = threading.Event()
        self._poll_cancel = cancel
        try:
            threading.Thread(
                target=self._poll_worker,
                args=(token, cancel),
                daemon=True,
                name='si-genai-health',
            ).start()
        except RuntimeError:
            self._active = False
            self._set_status(
                STATE_FAILED,
                '反代进程已启动，但无法运行健康检查；未自动重试。',
            )

    def _poll_worker(self, token: int, cancel: threading.Event) -> None:
        deadline = time.monotonic() + self._startup_timeout
        result = ProbeResult(PROBE_TIMEOUT_RESULT)
        while not cancel.wait(self._poll_interval):
            if time.monotonic() >= deadline:
                break
            result = self._safe_probe()
            if cancel.is_set():
                return
            if result.kind in (PROBE_EXISTING, PROBE_FOREIGN):
                break
        if cancel.is_set():
            return
        try:
            self._poll_ready.emit(token, result)
        except (RuntimeError, TypeError):
            pass

    def _on_poll_ready(self, token, result) -> None:
        if token != self._generation or not self._active or self._closed:
            return
        self._active = False
        cancel = self._poll_cancel
        self._poll_cancel = None
        if cancel is not None:
            cancel.set()
        if not isinstance(result, ProbeResult) or self._owned_process is None:
            return
        try:
            if self._owned_process.state() == QProcess.ProcessState.NotRunning:
                # The finished/error signal will report the real outcome.
                return
        except (RuntimeError, AttributeError):
            return
        if result.kind == PROBE_EXISTING:
            self._report_owned(result)
        elif result.kind == PROBE_FOREIGN:
            self._set_status(
                STATE_PORT_BUSY,
                '127.0.0.1:8000 在启动过程中显示为其他程序占用；未连接该程序，'
                '也不会自动重试。',
            )
        else:
            self._set_status(
                STATE_FAILED,
                '反代进程已启动，但健康检查超时或未通过；未自动重试，也未连接其它服务。'
                '本次进程仍由本应用管理，可手动停止。',
            )

    def _report_owned(self, result: ProbeResult) -> None:
        if result.upstream_auth in ('missing', 'expired'):
            self._set_status(
                STATE_STARTED_LOGIN_REQUIRED,
                '反代服务已启动，但需要学校本人登录。请自行完成登录；'
                '不会自动打开浏览器或运行登录脚本。',
            )
        elif result.upstream_auth == 'valid' and result.health == 'ok':
            self._set_status(
                STATE_STARTED,
                '反代服务已启动，健康检查通过；尚未发送模型对话。',
            )
        else:
            self._set_status(
                STATE_STARTED_UNVERIFIED,
                '反代进程已启动，但健康状态暂无法确认；尚未发送模型对话。',
            )

    def _on_process_error(self, process, error) -> None:
        if process is not self._owned_process:
            return
        try:
            failed_to_start = error == QProcess.ProcessError.FailedToStart
        except (TypeError, AttributeError):
            failed_to_start = False
        if not failed_to_start:
            return
        was_stopping = self._stopping
        self._cancel_work()
        self._discard_process(process)
        self._stopping = False
        if not self._closed:
            if was_stopping:
                self._set_status(
                    STATE_STOPPED,
                    '已在反代启动前停止本次进程；现有外部服务未被停止。',
                )
            else:
                self._set_status(
                    STATE_FAILED,
                    '本地 Python 或反代进程无法启动；未自动重试，也未连接其它服务。',
                )

    def _on_process_finished(self, process, _code, _status) -> None:
        if process is not self._owned_process:
            return
        was_stopping = self._stopping
        self._cancel_work()
        self._owned_process = None
        self._owned_pid = 0
        self._stopping = False
        try:
            process.deleteLater()
        except (RuntimeError, AttributeError):
            pass
        if self._closed:
            return
        if was_stopping:
            self._set_status(
                STATE_STOPPED,
                '已停止由本应用启动的反代；现有外部服务未被停止。',
            )
        else:
            self._set_status(
                STATE_FAILED,
                '反代进程已退出（可能启动失败或端口被占用）；未自动重试，'
                '也未连接其它服务。',
            )

    def stop_owned(self) -> None:
        """Cancel pending work and stop only a process this object started."""
        if self._closed or self._stopping:
            return
        was_active = self._active
        self._cancel_work()
        process = self._owned_process
        if process is None:
            if self._state in _EXISTING_STATES:
                self._set_status(
                    STATE_STOPPED,
                    '当前反代不是由本应用启动，未停止任何现有服务。'
                    '如需重新检查，请再次启动。',
                )
            elif was_active:
                self._set_status(
                    STATE_STOPPED,
                    '已取消本次启动检查；没有启动反代。',
                )
            else:
                self._set_status(
                    STATE_STOPPED,
                    '当前没有由本应用启动的反代进程，未停止任何服务。',
                )
            return

        self._stopping = True
        try:
            running = process.state() != QProcess.ProcessState.NotRunning
        except (RuntimeError, AttributeError):
            running = False
        if not running:
            # The parent launcher is already gone.  There is no reliable
            # identity with which to stop a possible detached child, so leave
            # it alone rather than killing by port or executable name.
            self._discard_process(process)
            self._stopping = False
            self._set_status(
                STATE_STOP_LIMITED,
                '反代父进程已退出，无法安全确认其派生子进程；未按端口或名称结束任何进程。',
            )
            return

        try:
            pid = int(process.processId() or 0)
        except (RuntimeError, TypeError, ValueError):
            pid = 0
        self._owned_pid = pid
        if pid <= 0:
            # QProcess is still starting.  Re-check when started; never guess a
            # PID from a port or a PID file.
            self._stop_when_started = True
            self._set_status(STATE_STOPPING, '正在反代启动过程中停止本次进程。')
            try:
                process.terminate()
            except (RuntimeError, AttributeError):
                pass
            return
        self._begin_process_stop(process)

    def stop(self) -> None:
        """Alias for :meth:`stop_owned`; never stops an external service."""
        self.stop_owned()

    def _begin_process_stop(self, process) -> None:
        if process is not self._owned_process or not self._stopping:
            return
        try:
            pid = int(process.processId() or 0)
        except (RuntimeError, TypeError, ValueError):
            pid = 0
        self._owned_pid = pid
        if not self._closed:
            self._set_status(
                STATE_STOPPING,
                '正在停止由本应用启动的反代进程（不影响现有外部服务）。',
            )
        if pid <= 0:
            # QProcess has not handed us a PID yet.  Use its gentle stop now
            # and retry the tree stop when the started signal arrives.
            try:
                process.terminate()
            except (RuntimeError, AttributeError):
                pass
            self._stop_when_started = True
            return
        if os.name != 'nt':
            try:
                process.terminate()
            except (RuntimeError, AttributeError):
                pass
            return
        # On Windows, kill only the tree rooted at our own live PID.  The
        # first taskkill is the gentle variant; the timer below escalates only
        # while the same QProcess handle still reports that same PID.
        self._request_tree_stop(pid, force=False)
        QTimer.singleShot(
            int(self._stop_grace * 1000),
            lambda: self._force_owned_stop(process, pid),
        )

    def _request_tree_stop(self, pid: int, force: bool) -> None:
        if pid <= 0:
            return
        try:
            threading.Thread(
                target=_run_taskkill,
                args=(int(pid), bool(force)),
                daemon=True,
                name='si-genai-stop-tree',
            ).start()
        except RuntimeError:
            pass

    def _force_owned_stop(self, process, pid: int) -> None:
        if process is not self._owned_process or not self._stopping:
            return
        try:
            if process.state() == QProcess.ProcessState.NotRunning:
                return
            if int(process.processId() or 0) != int(pid):
                # The original process ended and the handle no longer matches
                # the PID we took responsibility for.
                return
        except (RuntimeError, TypeError, ValueError):
            return
        self._request_tree_stop(pid, force=True)

    def close(self) -> None:
        """Invalidate late results and stop only this object's own process."""
        if self._closed:
            return
        if self._owned_process is not None:
            self.stop_owned()
        else:
            self._cancel_work()
        self._closed = True
        self._set_status(
            STATE_CLOSED,
            '服务控制已关闭；只会停止本对象启动的进程，不会影响现有反代。',
        )
