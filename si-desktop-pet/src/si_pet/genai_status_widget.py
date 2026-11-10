"""User-triggered, loopback-only status hint for the GenAI reverse proxy.

This widget intentionally does not import or control the proxy service.  It is
inert until the user presses its button, performs only two bounded GETs with
Qt's asynchronous network stack, and never starts a process, opens a browser,
reads credentials, or sends a model request.
"""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QTimer, QUrl, Qt, Signal
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkProxy,
    QNetworkReply,
    QNetworkRequest,
)
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout

from . import storage

PRODUCTION_BASE_URL = 'http://127.0.0.1:8000'
ROOT_PATH = '/'
HEALTH_PATH = '/health'
SERVICE_ID = 'ShanghaiTech GenAI OpenAI Proxy'
CHECK_TIMEOUT_MS = 1600
MAX_RESPONSE_BYTES = 64 * 1024
# Read-only aliases for callers that prefer URL/limit constants.
BASE_URL = PRODUCTION_BASE_URL
ROOT_URL = PRODUCTION_BASE_URL + ROOT_PATH
HEALTH_URL = PRODUCTION_BASE_URL + HEALTH_PATH
TIMEOUT_MS = CHECK_TIMEOUT_MS
BODY_LIMIT = MAX_RESPONSE_BYTES

STATE_IDLE = 'idle'
STATE_CHECKING = 'checking'
STATE_RECOGNIZED = 'recognized'
STATE_LOGIN_REQUIRED = 'login_required'
STATE_MISMATCH = 'mismatch'
STATE_UNAVAILABLE = 'unavailable'
STATE_TIMEOUT = 'timeout'
STATE_REDIRECT = 'redirect'
STATE_TOO_LARGE = 'too_large'
STATE_CANCELLED = 'cancelled'
STATE_CLOSED = 'closed'

_RECOGNIZED = '本机响应的服务标识与 GenAI 反代一致，不能确认进程身份。'
_HINT_ONLY = '这项检查只是提示，不会阻断其他对话路径。'
_CREDENTIAL_NOTE = '桌宠不读取登录凭据文件；反代自身可能读取其凭据来判断健康。'
_INITIAL_MESSAGE = (
    '尚未检查。按钮只访问本机 127.0.0.1:8000 的 / 与 /health，'
    '不会自动启动反代、打开浏览器或发送模型对话。'
    + _CREDENTIAL_NOTE + _HINT_ONLY
)
_CHECKING_MESSAGE = (
    '正在检查本机 127.0.0.1:8000 的 / 与 /health，'
    '不会访问模型或聊天接口。'
)

_REDIRECT_POLICY = QNetworkRequest.RedirectPolicy.ManualRedirectPolicy


def app_root():
    """Resolve through storage at call time so tests can patch either module."""
    return storage.app_root()


def _validated_loopback_base_url(value):
    """Return a normalized HTTP loopback base URL.

    Arbitrary production hosts are deliberately not supported.  The optional
    argument exists so tests can point at a server on an ephemeral loopback
    port; the widget's own default is always 127.0.0.1:8000.
    """
    if not isinstance(value, str):
        raise ValueError('本机检查地址必须是回环 HTTP 地址。')
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        raise ValueError('本机检查地址必须是回环 HTTP 地址。') from None
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
            or port is None or not 1 <= port <= 65535
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment
            or parsed.path not in ('', '/')):
        raise ValueError('本机检查地址必须是 127.0.0.1 上的 HTTP 根地址。')
    return f'http://127.0.0.1:{port}'


def _reply_status(reply):
    try:
        value = reply.attribute(
            QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        return int(value) if value is not None else None
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def _reply_redirected(reply, status):
    if isinstance(status, int) and 300 <= status < 400:
        return True
    try:
        target = reply.attribute(
            QNetworkRequest.Attribute.RedirectionTargetAttribute)
        return target is not None and not target.isEmpty()
    except (AttributeError, RuntimeError, TypeError):
        return False


def _reply_error(reply):
    try:
        return reply.error() != QNetworkReply.NetworkError.NoError
    except (AttributeError, RuntimeError, TypeError):
        return True


class GenAIStatusWidget(QFrame):
    """A quiet settings card; network activity happens only on demand.

    ``status_changed`` carries the human-readable status, while
    ``state_changed`` carries a stable state name for callers that need it.
    Closing, hiding, or cancelling the widget aborts any in-flight reply and
    invalidates its late signals.
    """

    status_changed = Signal(str)
    state_changed = Signal(str)
    check_finished = Signal(str, str)

    def __init__(self, parent=None, base_url=None, *, service_url=None,
                 manager=None, network_manager=None, command_path=None,
                 start_script=None, timeout_ms=CHECK_TIMEOUT_MS):
        super().__init__(parent)
        self.setObjectName('card')

        if base_url is not None and service_url is not None:
            if _validated_loopback_base_url(base_url) != \
                    _validated_loopback_base_url(service_url):
                raise ValueError('只能指定一个本机检查地址。')
        requested_url = base_url if base_url is not None else service_url
        self._base_url = _validated_loopback_base_url(
            requested_url if requested_url is not None else PRODUCTION_BASE_URL)
        self.root_url = self._base_url + ROOT_PATH
        self.health_url = self._base_url + HEALTH_PATH

        injected_manager = manager if manager is not None else network_manager
        self._owns_manager = injected_manager is None
        self._manager = (injected_manager if injected_manager is not None
                         else QNetworkAccessManager(self))
        # The check must not inherit a system/user proxy.  Some injected test
        # managers may be lightweight fakes, so optional configuration is not
        # allowed to break construction.
        try:
            self._manager.setProxy(
                QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            self._manager.setRedirectPolicy(_REDIRECT_POLICY)
        except (AttributeError, RuntimeError, TypeError):
            pass

        requested_script = (command_path if command_path is not None
                            else start_script)
        self.command_path = Path(
            requested_script if requested_script is not None
            else app_root().parent / 'genai-proxy' / 'start-local.cmd')
        self.start_script_path = self.command_path
        try:
            self._script_present = bool(self.command_path.exists())
        except OSError:
            self._script_present = False

        self._timeout_ms = max(1, int(timeout_ms))
        self._state = STATE_IDLE
        self._message = _INITIAL_MESSAGE
        self._busy = False
        self._closed = False
        self._phase = ''
        self._active_reply = None
        self._buffer = bytearray()
        # Bytes read across every phase of the current check.  The 64 KiB cap
        # is a whole-check budget, not a per-response one.
        self._received_bytes = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)

        self.title_label = QLabel('上科大 GenAI 反代')
        self.title_label.setObjectName('subhead')
        layout.addWidget(self.title_label)

        self.status_caption = QLabel('当前状态')
        self.status_caption.setObjectName('muted')
        layout.addWidget(self.status_caption)

        self.status_label = QLabel(_INITIAL_MESSAGE)
        self.status_label.setObjectName('muted')
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status_label)

        self.check_button = QPushButton('检查本机反代')
        self.check_button.clicked.connect(self.start_check)
        layout.addWidget(self.check_button)

        self.manual_label = QLabel(self._manual_text())
        self.manual_label.setObjectName('muted')
        self.manual_label.setWordWrap(True)
        self.manual_label.setTextFormat(Qt.TextFormat.PlainText)
        self.manual_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.manual_label)

        self.credential_label = QLabel(_CREDENTIAL_NOTE)
        self.credential_label.setObjectName('muted')
        self.credential_label.setWordWrap(True)
        self.credential_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.credential_label)

        # Short aliases make the card straightforward to inspect in tests and
        # embedding code without changing the widget structure.
        self.button = self.check_button
        self.status = self.status_label
        self.manual = self.manual_label

        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setTimerType(Qt.TimerType.PreciseTimer)
        self._deadline.timeout.connect(self._on_deadline)

        # A parented widget may be deleted without closeEvent().  This only
        # aborts the request; it deliberately touches no widget or timer state.
        try:
            self.destroyed.connect(self._abort_for_destruction)
        except (AttributeError, RuntimeError, TypeError):
            pass

    @property
    def state(self):
        return self._state

    @property
    def message(self):
        return self._message

    @property
    def busy(self):
        return self._busy

    @property
    def timeout_ms(self):
        return self._timeout_ms

    def _manual_text(self):
        path = str(self.command_path)
        if self._script_present:
            return (
                f'手动启动：请自行运行 {path}。'
                '本应用不会自动执行该脚本、打开浏览器或发起登录。'
            )
        return (
            f'反代未随包提供：未找到 {path}。'
            '请自行从可信来源取得反代并手动启动；'
            '本应用不会自动下载或执行脚本、打开浏览器或发起登录。'
        )

    def _manual_help(self):
        if self._script_present:
            return (
                f'如需手动启动，请自行运行 {self.command_path}；'
                '本应用不会自动执行该脚本或打开浏览器。'
            )
        return (
            f'反代未随包提供（未找到 {self.command_path}）。'
            '请自行从可信来源取得并手动启动；'
            '本应用不会自动下载或执行脚本、打开浏览器。'
        )

    def _set_checking(self):
        self._state = STATE_CHECKING
        self._message = _CHECKING_MESSAGE
        self.status_label.setText(_CHECKING_MESSAGE)
        self._emit(self.status_changed, _CHECKING_MESSAGE)
        self._emit(self.state_changed, STATE_CHECKING)

    @staticmethod
    def _emit(signal, value):
        try:
            signal.emit(value)
        except (RuntimeError, TypeError):
            pass

    def start_check(self):
        """Start one user-triggered check; a busy widget ignores extra calls."""
        if self._closed or self._busy:
            return False
        self._busy = True
        self._phase = 'root'
        self._buffer.clear()
        self._received_bytes = 0
        self.check_button.setEnabled(False)
        self._set_checking()
        self._deadline.start(self._timeout_ms)
        self._send(self.root_url, 'root')
        return True

    # Friendly aliases for callers that describe the action differently.
    def check(self):
        return self.start_check()

    def check_now(self):
        return self.start_check()

    def _send(self, url, phase):
        request = QNetworkRequest(QUrl(url))
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            _REDIRECT_POLICY,
        )
        request.setAttribute(
            QNetworkRequest.Attribute.CacheLoadControlAttribute,
            QNetworkRequest.CacheLoadControl.AlwaysNetwork,
        )
        # Do not send or store cookies, reuse cached HTTP credentials, or
        # persist response bodies.  The check needs no authentication material.
        # CacheSaveControlAttribute is a bool, not a LoadControl enum: Manual
        # there is truthy and could leave response caching enabled, so it must
        # be an explicit False.
        request.setAttribute(
            QNetworkRequest.Attribute.CacheSaveControlAttribute, False)
        for control_attribute in (
                QNetworkRequest.Attribute.CookieLoadControlAttribute,
                QNetworkRequest.Attribute.CookieSaveControlAttribute,
                QNetworkRequest.Attribute.AuthenticationReuseAttribute):
            request.setAttribute(control_attribute,
                                 QNetworkRequest.LoadControl.Manual)
        try:
            reply = self._manager.get(request)
        except (AttributeError, RuntimeError, TypeError):
            reply = None
        if reply is None:
            self._fail(phase, STATE_UNAVAILABLE,
                       self._unavailable_message(phase))
            return
        # Keep Qt's internal read buffer to one byte past the whole-check
        # budget.  Injected test doubles may not implement this optional
        # setter, so failure here must not break the check.
        try:
            reply.setReadBufferSize(MAX_RESPONSE_BYTES + 1)
        except (AttributeError, RuntimeError, TypeError):
            pass
        self._phase = phase
        self._active_reply = reply
        try:
            reply.readyRead.connect(partial(self._on_ready_read, reply))
            reply.finished.connect(partial(self._on_finished, reply))
        except (AttributeError, RuntimeError, TypeError):
            self._fail(phase, STATE_UNAVAILABLE,
                       self._unavailable_message(phase))

    def _is_active(self, reply):
        return (not self._closed and self._busy
                and reply is self._active_reply)

    def _on_ready_read(self, reply):
        if not self._is_active(reply):
            return
        self._drain(reply)

    def _on_finished(self, reply):
        if not self._is_active(reply):
            self._release_reply(reply)
            return

        phase = self._phase
        status = _reply_status(reply)
        if _reply_redirected(reply, status):
            message = (
                ('本机端口上的响应要求跳转；已忽略重定向，未继续请求 /health，'
                 '也未连接新地址。本机端口上的响应不匹配。')
                if phase == 'root' else
                (_RECOGNIZED + '但健康检查要求跳转；已忽略重定向，'
                 '未继续请求新地址。' + self._manual_help() + _HINT_ONLY)
            )
            self._fail(phase, STATE_REDIRECT, message)
            return

        # Qt reports HTTP 4xx/5xx through error() as well.  Those are still
        # complete HTTP responses, not connection failures: root must be
        # reported as a mismatch, and a health body may still say the school
        # login is missing.  Only errors without an HTTP error response count
        # as network failures (including a 2xx connection cut mid-body).
        http_error_response = status is not None and status >= 400
        if _reply_error(reply) and not http_error_response:
            self._fail(phase, STATE_UNAVAILABLE,
                       self._unavailable_message(phase))
            return

        if phase == 'root' and status != 200:
            self._fail(
                phase, STATE_MISMATCH,
                ('本机端口上的响应不匹配'
                 f'（HTTP {status if status is not None else "未知"}）。'
                 '已停止检查，未请求 /health，也未连接其他服务。'
                 + _HINT_ONLY),
            )
            return

        if not self._drain(reply):
            return

        payload = self._json_payload()
        if phase == 'root':
            if not isinstance(payload, dict) \
                    or payload.get('service') != SERVICE_ID:
                self._fail(
                    phase, STATE_MISMATCH,
                    ('本机端口上的响应不匹配；可能不是上科大 GenAI 反代。'
                     '已停止检查，未请求 /health，也未连接其他服务。'
                     + _HINT_ONLY),
                )
                return
            # Drop the parsed root body, but keep _received_bytes: the budget
            # is shared by both phases of this check.
            self._buffer.clear()
            self._release_reply(reply)
            self._phase = 'health'
            self._send(self.health_url, 'health')
            return

        if not isinstance(payload, dict):
            self._fail(
                phase, STATE_UNAVAILABLE,
                (_RECOGNIZED + '健康检查响应无法确认登录状态。'
                 + self._manual_help() + _HINT_ONLY),
            )
            return
        health = payload.get('status')
        auth = payload.get('upstream_auth')
        if auth in ('missing', 'expired'):
            message = (
                _RECOGNIZED
                + '健康检查显示学校登录缺失或已过期，请本人登录后再次检查。'
                '本应用不会自动打开浏览器或运行登录脚本。'
                + _CREDENTIAL_NOTE + _HINT_ONLY
            )
            state = STATE_LOGIN_REQUIRED
        elif status == 200 and auth == 'valid' and health == 'ok':
            message = (
                _RECOGNIZED
                + '健康检查报告登录有效，但这只是本机端口的自报状态，'
                '不代表模型回复正常，也不代表其他程序收不到对话。'
                + _HINT_ONLY
            )
            state = STATE_RECOGNIZED
        else:
            detail = (
                f'健康检查返回 HTTP {status if status is not None else "未知"}。'
                if status != 200 else
                '健康状态或登录状态未能确认；'
            )
            message = (
                _RECOGNIZED + detail + '请本人确认登录，'
                '必要时手动启动反代。'
                + self._manual_help() + _HINT_ONLY
            )
            state = STATE_UNAVAILABLE
        self._release_reply(reply)
        self._finish(state, message)

    def _drain(self, reply):
        """Read a bounded chunk against the shared whole-check byte budget.

        Every byte read in any phase counts, even after the root body has been
        parsed and its buffer cleared.  Once the total exceeds the limit the
        reply is aborted immediately.
        """
        remaining = MAX_RESPONSE_BYTES + 1 - self._received_bytes
        if remaining <= 0:
            self._too_large(self._phase)
            return False
        try:
            available = reply.bytesAvailable()
            if available > 0:
                chunk = reply.read(min(available, remaining))
                if chunk:
                    data = bytes(chunk)
                    self._buffer.extend(data)
                    self._received_bytes += len(data)
        except (RuntimeError, TypeError, ValueError):
            self._fail(self._phase, STATE_UNAVAILABLE,
                       self._unavailable_message(self._phase))
            return False
        if self._received_bytes > MAX_RESPONSE_BYTES:
            self._too_large(self._phase)
            return False
        return True

    def _too_large(self, phase):
        if phase == 'health':
            message = (
                _RECOGNIZED
                + '本次检查读取的响应合计超过 64 KiB，已中止检查。'
                + self._manual_help() + _HINT_ONLY
            )
        else:
            message = (
                '本机端口上的响应不匹配：本次检查读取的响应合计超过 64 KiB，'
                '已中止检查。' + _HINT_ONLY
            )
        self._fail(phase, STATE_TOO_LARGE, message)

    def _json_payload(self):
        try:
            return json.loads(bytes(self._buffer).decode('utf-8'))
        except (UnicodeError, ValueError, RecursionError):
            return None

    def _unavailable_message(self, phase):
        if phase == 'health':
            return (
                _RECOGNIZED
                + '但健康检查未能完成，无法确认登录状态。'
                + self._manual_help() + _HINT_ONLY
            )
        return (
            '检查失败：无法连接本机 127.0.0.1:8000，'
            '或连接在响应前中断。'
            + self._manual_help() + _HINT_ONLY
        )

    def _on_deadline(self):
        if self._closed or not self._busy:
            return
        phase = self._phase
        timeout_text = f'{self._timeout_ms / 1000:.1f}'.rstrip('0').rstrip('.')
        self._abort_reply()
        if phase == 'health':
            message = (
                _RECOGNIZED
                + f'但健康检查超过 {timeout_text} 秒仍未完成，已中止检查。'
                + self._manual_help() + _HINT_ONLY
            )
        else:
            message = (
                f'检查超时：{timeout_text} 秒内未得到本机 127.0.0.1:8000 的完整响应。'
                + self._manual_help() + _HINT_ONLY
            )
        self._finish(STATE_TIMEOUT, message)

    def _fail(self, phase, state, message):
        self._abort_reply()
        self._finish(state, message)

    def _finish(self, state, message):
        self._deadline.stop()
        self._busy = False
        self._phase = ''
        self._buffer.clear()
        self._received_bytes = 0
        self.check_button.setEnabled(not self._closed)
        self._state = state
        self._message = message
        if self._closed:
            return
        self.status_label.setText(message)
        self._emit(self.status_changed, message)
        self._emit(self.state_changed, state)
        try:
            self.check_finished.emit(state, message)
        except (RuntimeError, TypeError):
            pass

    def _release_reply(self, reply):
        if reply is not self._active_reply:
            return
        self._active_reply = None
        try:
            reply.deleteLater()
        except (AttributeError, RuntimeError, TypeError):
            pass

    def _abort_reply(self):
        reply = self._active_reply
        self._active_reply = None
        if reply is not None:
            try:
                reply.abort()
            except (AttributeError, RuntimeError, TypeError):
                pass
            try:
                reply.deleteLater()
            except (AttributeError, RuntimeError, TypeError):
                pass

    def _abort_for_destruction(self, *_args):
        # Late Qt signals are also rejected by identity checks; this is only a
        # best-effort socket cancellation when a parent deletes the widget.
        reply = self._active_reply
        self._active_reply = None
        if reply is not None:
            try:
                reply.abort()
            except (AttributeError, RuntimeError, TypeError):
                pass
            try:
                reply.deleteLater()
            except (AttributeError, RuntimeError, TypeError):
                pass

    def cancel_check(self, *, update_status=True):
        """Abort an in-flight check and reject any signal that arrives later."""
        if not self._busy:
            return False
        self._abort_reply()
        self._deadline.stop()
        self._busy = False
        self._phase = ''
        self._buffer.clear()
        self._received_bytes = 0
        self.check_button.setEnabled(not self._closed)
        self._state = STATE_CANCELLED
        message = ('检查已取消，可再次点击检查。' + _HINT_ONLY)
        self._message = message
        if update_status and not self._closed:
            self.status_label.setText(message)
            self._emit(self.status_changed, message)
            self._emit(self.state_changed, STATE_CANCELLED)
        return True

    def stop_check(self):
        return self.cancel_check(update_status=True)

    def showEvent(self, event):
        # A hidden page may have had its check cancelled without touching the
        # label.  When it becomes visible again, show the settled cancellation
        # text rather than leaving a stale "checking" message.
        if (not self._busy and not self._closed
                and self._state == STATE_CANCELLED):
            self.status_label.setText(self._message)
        super().showEvent(event)

    def closeEvent(self, event):
        self._closed = True
        self.cancel_check(update_status=False)
        self._deadline.stop()
        self._state = STATE_CLOSED
        super().closeEvent(event)

    def hideEvent(self, event):
        # Switching away from the settings page is also a safe cancellation
        # point; no reply is left to update an invisible page.
        self.cancel_check(update_status=False)
        super().hideEvent(event)


# Descriptive aliases keep imports friendly for callers using "card" or
# "proxy" terminology; the implementation is shared.
GenAIStatusCard = GenAIStatusWidget
GenAIProxyStatusWidget = GenAIStatusWidget
GenAIProxyStatusCard = GenAIStatusWidget
GenAIServiceStatusWidget = GenAIStatusWidget
