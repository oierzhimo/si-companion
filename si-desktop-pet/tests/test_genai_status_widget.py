"""Offline tests for the user-triggered GenAI proxy status hint.

Every HTTP test uses a temporary server on a random 127.0.0.1 port (or an
injected QNetworkAccessManager).  Nothing in this module connects to the real
127.0.0.1:8000, imports the proxy service, starts a process, or reads a
credential file.
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtCore import (
    QCoreApplication,
    QEventLoop,
    QObject,
    QPoint,
    QRect,
    QSize,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import QIcon, QPixmap, QRegion
from PySide6.QtNetwork import (
    QNetworkProxy,
    QNetworkReply,
    QNetworkRequest,
)
from PySide6.QtWidgets import QApplication, QScrollArea

from si_pet.conversation import (
    GENAI_ALLOWED_MODELS,
    GENAI_DEFAULT_MODEL,
    filter_genai_models,
)
from si_pet.genai_status_widget import (
    CHECK_TIMEOUT_MS,
    HEALTH_PATH,
    MAX_RESPONSE_BYTES,
    PRODUCTION_BASE_URL,
    ROOT_PATH,
    SERVICE_ID,
    STATE_LOGIN_REQUIRED,
    STATE_MISMATCH,
    STATE_RECOGNIZED,
    STATE_REDIRECT,
    STATE_TIMEOUT,
    STATE_TOO_LARGE,
    STATE_UNAVAILABLE,
    GenAIStatusWidget,
)
from si_pet.panel import Panel
from si_pet.storage import DEFAULTS, app_root

_QT_APP = None


def qt_application():
    global _QT_APP
    if _QT_APP is None:
        instance = QCoreApplication.instance()
        if instance is not None and not isinstance(instance, QApplication):
            raise unittest.SkipTest(
                'a non-GUI QCoreApplication already exists in this process')
        _QT_APP = instance or QApplication([])
        _QT_APP.setQuitOnLastWindowClosed(False)
    return _QT_APP


class _ServerState:
    def __init__(self):
        self.requests = []
        self.release = threading.Event()
        self.stopper = threading.Event()
        self.root_status = 200
        self.root_body = json.dumps({'service': SERVICE_ID}).encode('utf-8')
        self.health_status = 200
        self.health_body = json.dumps({
            'status': 'ok',
            'upstream_auth': 'valid',
        }).encode('utf-8')
        self.root_delay = 0.0
        self.health_delay = 0.0
        self.root_cookie = None
        self.root_redirect = None
        self.health_redirect = None
        self.drip_root = False
        self.drip_health = False
        self.close_root = False

    def record(self, method, path, headers):
        # The Qt main thread polls this list while its event loop is running.
        self.requests.append({
            'method': method,
            'path': path,
            'headers': {key.lower(): value for key, value in headers.items()},
        })


class _LoopbackServer:
    def __init__(self):
        self.state = _ServerState()
        state = self.state

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *_args):
                pass

            def _send(self, status, body=b'', headers=()):
                try:
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.send_header('Connection', 'close')
                    for name, value in headers:
                        self.send_header(name, value)
                    self.end_headers()
                    if body:
                        self.wfile.write(body)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

            def _drip(self):
                try:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.send_header('Content-Length', '1000000')
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    while not state.stopper.is_set():
                        self.wfile.write(b' ')
                        self.wfile.flush()
                        state.stopper.wait(0.05)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

            def do_GET(self):
                state.record(self.command, self.path, self.headers)
                if self.path == ROOT_PATH:
                    if state.root_delay:
                        state.release.wait(state.root_delay)
                    if state.close_root:
                        self.close_connection = True
                        try:
                            self.connection.close()
                        except OSError:
                            pass
                        return
                    if state.drip_root:
                        self._drip()
                        return
                    if state.root_redirect:
                        self._send(302, b'PRIVATE-REDIRECT-BODY',
                                   [('Location', state.root_redirect)])
                        return
                    headers = []
                    if state.root_cookie:
                        headers.append(('Set-Cookie', state.root_cookie))
                    self._send(state.root_status, state.root_body, headers)
                    return
                if self.path == HEALTH_PATH:
                    if state.health_delay:
                        state.release.wait(state.health_delay)
                    if state.health_redirect:
                        self._send(302, b'PRIVATE-HEALTH-PAGE',
                                   [('Location', state.health_redirect)])
                        return
                    if state.drip_health:
                        self._drip()
                        return
                    self._send(state.health_status, state.health_body)
                    return
                self._send(404, b'PRIVATE-ERROR-PAGE')

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.daemon_threads = True
        self.httpd.block_on_close = False
        self.thread = threading.Thread(
            target=self.httpd.serve_forever,
            kwargs={'poll_interval': 0.02},
            name='genai-status-test-server',
            daemon=True,
        )
        self.thread.start()
        self.base_url = f'http://127.0.0.1:{self.httpd.server_port}'

    def stop(self):
        self.state.release.set()
        self.state.stopper.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(2.0)


class _FakeReply(QObject):
    readyRead = Signal()
    finished = Signal()

    def __init__(self, status, body):
        super().__init__()
        self._status = status
        self._body = bytes(body)
        self._offset = 0
        self.aborted = False
        self.deletions = 0

    def bytesAvailable(self):
        return len(self._body) - self._offset

    def read(self, size):
        chunk = self._body[self._offset:self._offset + int(size)]
        self._offset += len(chunk)
        return chunk

    def attribute(self, code):
        if code == QNetworkRequest.Attribute.HttpStatusCodeAttribute:
            return self._status
        if code == QNetworkRequest.Attribute.RedirectionTargetAttribute:
            return None
        return None

    def error(self):
        return QNetworkReply.NetworkError.NoError

    def abort(self):
        self.aborted = True

    def deleteLater(self):
        self.deletions += 1


class _RecordingReply(_FakeReply):
    """Fake reply that records the read-buffer size the widget requested.

    The base _FakeReply deliberately omits setReadBufferSize, so the other
    injected-manager tests keep covering compatibility with a test double that
    does not implement it.
    """

    def __init__(self, status, body):
        super().__init__(status, body)
        self.read_buffer_sizes = []

    def setReadBufferSize(self, size):
        self.read_buffer_sizes.append(int(size))


class _FakeManager(QObject):
    """Injected QNAM stand-in that never opens a socket."""

    reply_class = _FakeReply

    def __init__(self):
        super().__init__()
        self.requests = []
        self.replies = []
        self.proxy = None
        self.redirect_policy = None
        self.payloads = {
            ROOT_PATH: (200, json.dumps({'service': SERVICE_ID}).encode('utf-8')),
            HEALTH_PATH: (200, json.dumps({
                'status': 'ok',
                'upstream_auth': 'valid',
            }).encode('utf-8')),
        }

    def setProxy(self, proxy):
        self.proxy = proxy

    def setRedirectPolicy(self, policy):
        self.redirect_policy = policy

    def get(self, request):
        self.requests.append(request)
        path = request.url().path()
        status, body = self.payloads[path]
        reply = self.reply_class(status, body)
        self.replies.append(reply)

        def deliver():
            reply.readyRead.emit()
            reply.finished.emit()

        QTimer.singleShot(0, deliver)
        return reply


class _RecordingManager(_FakeManager):
    reply_class = _RecordingReply


class _FakeSprites:
    def icon(self):
        return QIcon()

    def mask(self, state, index, scale):
        return QRegion(0, 0, 10, 10)

    def pixmap(self, state, index, scale, dpr=1.0):
        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.GlobalColor.transparent)
        return pixmap


class _FakeStore(dict):
    def __init__(self):
        super().__init__(copy.deepcopy(DEFAULTS))
        self.saves = 0
        self.sync_ledger = None

    def save(self):
        self.saves += 1


class _FakeController(QObject):
    changed = Signal()
    contact_reply = Signal(str)

    def __init__(self):
        super().__init__()
        self.store = _FakeStore()
        self.sprites = _FakeSprites()
        self.state = 'idle'
        self.focus_end = None

    def animation(self):
        return 'idle', 0

    def pat(self):
        pass

    def feed(self):
        pass

    def dock(self):
        pass


class _FakePet:
    def recover(self):
        pass


class GenAIStatusWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_application()

    def setUp(self):
        self.widgets = []
        self.panels = []
        self.server = _LoopbackServer()
        self.addCleanup(self.server.stop)
        self.addCleanup(self._close_widgets)
        if self.server.httpd.server_port == 8000:
            self.skipTest('the test server must never use the production port')

    def _close_widgets(self):
        for widget in self.widgets:
            try:
                widget.cancel_check(update_status=False)
            except Exception:
                pass
            try:
                widget.close()
            except Exception:
                pass
            try:
                widget.deleteLater()
            except Exception:
                pass
        for panel in self.panels:
            try:
                panel.timer.stop()
            except Exception:
                pass
            try:
                panel.hide()
            except Exception:
                pass
            try:
                panel.deleteLater()
            except Exception:
                pass
        self.pump(0.08)

    def pump(self, seconds=0.02):
        deadline = time.monotonic() + seconds
        while True:
            self.app.processEvents(QEventLoop.AllEvents, 20)
            if time.monotonic() >= deadline:
                break
            time.sleep(0.002)

    def wait_until(self, predicate, timeout=4.0):
        deadline = time.monotonic() + timeout
        loop = QEventLoop()
        timer = QTimer()
        timer.setInterval(10)

        def check():
            try:
                done = bool(predicate())
            except Exception:
                done = True
            if done or time.monotonic() >= deadline:
                loop.quit()

        timer.timeout.connect(check)
        timer.start()
        QTimer.singleShot(0, check)
        loop.exec()
        timer.stop()
        try:
            return bool(predicate())
        except Exception:
            return False

    def wait_idle(self, widget, timeout=4.0):
        self.assertTrue(
            self.wait_until(lambda: not widget.busy, timeout),
            'status check did not finish',
        )
        self.pump(0.02)

    def make_widget(self, base_url=None, **kwargs):
        widget = GenAIStatusWidget(
            base_url=base_url if base_url is not None else self.server.base_url,
            **kwargs,
        )
        self.widgets.append(widget)
        widget.show()
        self.pump(0.02)
        return widget

    def _labels_text(self, widget):
        return '\n'.join(
            label.text()
            for label in widget.findChildren(type(widget.status_label))
        )

    def test_construction_is_inert_and_production_address_is_fixed(self):
        widget = self.make_widget()
        self.pump(0.2)
        self.assertEqual(self.server.state.requests, [])
        self.assertFalse(widget.busy)
        self.assertEqual(widget.title_label.text(), '上科大 GenAI 反代')
        self.assertEqual(widget.check_button.text(), '检查本机反代')
        self.assertIn('尚未检查', widget.status_label.text())
        self.assertEqual(
            widget.command_path,
            Path(app_root()).parent / 'genai-proxy' / 'start-local.cmd',
        )
        self.assertIn(str(widget.command_path), widget.manual_label.text())
        if widget.command_path.exists():
            self.assertIn('手动启动', widget.manual_label.text())
        else:
            self.assertIn('反代未随包提供', widget.manual_label.text())
        self.assertEqual(widget.root_url, f'http://127.0.0.1:{self.server.httpd.server_port}/')
        self.assertEqual(widget.health_url,
                         f'http://127.0.0.1:{self.server.httpd.server_port}/health')

        default = GenAIStatusWidget()
        self.widgets.append(default)
        self.assertEqual(default.root_url, PRODUCTION_BASE_URL + '/')
        self.assertEqual(default.health_url, PRODUCTION_BASE_URL + '/health')
        self.assertEqual(default.timeout_ms, CHECK_TIMEOUT_MS)
        self.assertEqual(default._manager.proxy().type(),
                         QNetworkProxy.ProxyType.NoProxy)

        for bad_url in (
                'http://example.com:8000',
                'https://127.0.0.1:8000',
                'http://127.0.0.1:8000/v1',
                'http://127.0.0.1:8000/?token=x',
                'http://user:pass@127.0.0.1:8000'):
            with self.subTest(url=bad_url):
                with self.assertRaises(ValueError):
                    GenAIStatusWidget(base_url=bad_url)

    def test_manual_start_text_reflects_script_presence(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / 'genai-proxy' / 'start-local.cmd'
            script.parent.mkdir()
            script.write_text('@echo off\n', encoding='utf-8')
            present = GenAIStatusWidget(
                base_url=self.server.base_url, command_path=script)
            self.widgets.append(present)
            self.assertIn(str(script), present.manual_label.text())
            self.assertIn('手动启动', present.manual_label.text())
            self.assertIn('不会自动执行', present.manual_label.text())

            missing_path = script.with_name('not-bundled.cmd')
            missing = GenAIStatusWidget(
                base_url=self.server.base_url, command_path=missing_path)
            self.widgets.append(missing)
            self.assertIn(str(missing_path), missing.manual_label.text())
            self.assertIn('反代未随包提供', missing.manual_label.text())
            self.assertIn('手动启动', missing.manual_label.text())

        self.pump(0.05)
        self.assertEqual(self.server.state.requests, [])

    def test_injected_manager_shows_no_threads_or_script_startup(self):
        manager = _FakeManager()
        widget = GenAIStatusWidget(
            base_url='http://127.0.0.1:54321',
            manager=manager,
        )
        self.widgets.append(widget)
        widget.show()
        self.pump(0.02)
        self.assertEqual(manager.proxy.type(), QNetworkProxy.ProxyType.NoProxy)
        self.assertEqual(manager.redirect_policy,
                         QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        self.assertEqual(manager.requests, [])
        baseline = {thread.ident for thread in threading.enumerate()}
        with mock.patch('subprocess.Popen') as popen, \
                mock.patch('subprocess.run') as run, \
                mock.patch('os.system') as system, \
                mock.patch('os.startfile', create=True) as startfile, \
                mock.patch.object(webbrowser, 'open') as browser:
            widget.check_button.click()
            self.wait_idle(widget)

        self.assertFalse(popen.called)
        self.assertFalse(run.called)
        self.assertFalse(system.called)
        self.assertFalse(startfile.called)
        self.assertFalse(browser.called)
        self.assertEqual(
            [request.url().toString() for request in manager.requests],
            ['http://127.0.0.1:54321/', 'http://127.0.0.1:54321/health'],
        )
        self.assertEqual(len(manager.replies), 2)
        self.assertTrue(all(reply.deletions >= 1 for reply in manager.replies))
        extra_threads = [
            thread for thread in threading.enumerate()
            if thread.ident not in baseline
        ]
        self.assertEqual(extra_threads, [])
        for request in manager.requests:
            header_names = [bytes(name).lower()
                            for name in request.rawHeaderList()]
            self.assertNotIn(b'authorization', header_names)
            self.assertNotIn(b'proxy-authorization', header_names)
            self.assertNotIn(b'cookie', header_names)
            self.assertIsNotNone(request.attribute(
                QNetworkRequest.Attribute.RedirectPolicyAttribute))
            for attribute in (
                    QNetworkRequest.Attribute.CacheSaveControlAttribute,
                    QNetworkRequest.Attribute.CookieLoadControlAttribute,
                    QNetworkRequest.Attribute.CookieSaveControlAttribute,
                    QNetworkRequest.Attribute.AuthenticationReuseAttribute):
                self.assertIsNotNone(request.attribute(attribute))
        self.assertIn('不能确认进程身份', widget.status_label.text())

    def test_requests_use_explicit_cache_and_cookie_controls_and_read_buffer(self):
        manager = _RecordingManager()
        widget = GenAIStatusWidget(
            base_url='http://127.0.0.1:54321',
            manager=manager,
        )
        self.widgets.append(widget)
        widget.show()
        self.pump(0.02)
        widget.check_button.click()
        self.wait_idle(widget)

        self.assertEqual(len(manager.requests), 2)
        self.assertEqual(len(manager.replies), 2)
        for request, reply in zip(manager.requests, manager.replies):
            # CacheSaveControlAttribute is a bool; LoadControl.Manual would be
            # truthy and could still allow Qt to store the response.
            cache_save = request.attribute(
                QNetworkRequest.Attribute.CacheSaveControlAttribute)
            self.assertIsInstance(cache_save, bool)
            self.assertFalse(cache_save)
            for attribute in (
                    QNetworkRequest.Attribute.CookieLoadControlAttribute,
                    QNetworkRequest.Attribute.CookieSaveControlAttribute,
                    QNetworkRequest.Attribute.AuthenticationReuseAttribute):
                self.assertEqual(
                    request.attribute(attribute),
                    QNetworkRequest.LoadControl.Manual.value)
            self.assertEqual(
                request.attribute(
                    QNetworkRequest.Attribute.CacheLoadControlAttribute),
                QNetworkRequest.CacheLoadControl.AlwaysNetwork.value)
            self.assertEqual(
                request.attribute(
                    QNetworkRequest.Attribute.RedirectPolicyAttribute),
                QNetworkRequest.RedirectPolicy.ManualRedirectPolicy.value)
            self.assertEqual(reply.read_buffer_sizes,
                             [MAX_RESPONSE_BYTES + 1])
            header_names = [bytes(name).lower()
                            for name in request.rawHeaderList()]
            for forbidden in (b'authorization', b'proxy-authorization',
                              b'cookie'):
                self.assertNotIn(forbidden, header_names)

    def test_click_queries_only_root_and_health_without_auth_or_sensitive_output(self):
        self.server.state.root_cookie = 'session=SECRET-COOKIE; Path=/'
        self.server.state.root_body = json.dumps({
            'service': SERVICE_ID,
            'token_expires_at': 'TOKEN-SECRET-DO-NOT-SHOW',
            'private_error_page': 'PRIVATE-ERROR-PAGE',
        }).encode('utf-8')
        self.server.state.health_body = json.dumps({
            'status': 'ok',
            'upstream_auth': 'valid',
            'token_expires_at': 'TOKEN-SECRET-DO-NOT-SHOW',
            'detail': 'PRIVATE-ERROR-PAGE',
        }).encode('utf-8')
        widget = self.make_widget()
        widget.check_button.click()
        self.wait_idle(widget)

        requests = self.server.state.requests
        self.assertEqual([request['method'] for request in requests], ['GET', 'GET'])
        self.assertEqual([request['path'] for request in requests], ['/', '/health'])
        for request in requests:
            for forbidden in ('authorization', 'proxy-authorization', 'cookie',
                              'x-api-key'):
                self.assertNotIn(forbidden, request['headers'])
        text = self._labels_text(widget)
        self.assertIn('本机响应的服务标识与 GenAI 反代一致，不能确认进程身份', text)
        self.assertIn('不代表模型回复正常', text)
        self.assertIn('不代表其他程序收不到对话', text)
        self.assertIn('不会阻断其他对话路径', text)
        self.assertNotIn('TOKEN-SECRET-DO-NOT-SHOW', text)
        self.assertNotIn('SECRET-COOKIE', text)
        self.assertNotIn('token_expires_at', text)
        self.assertNotIn('PRIVATE-ERROR-PAGE', text)
        self.assertIsNone(widget._active_reply)
        self.assertTrue(widget.check_button.isEnabled())

    def test_root_mismatch_stops_before_health(self):
        self.server.state.root_body = json.dumps({
            'service': 'Some Other Local Service',
            'detail': 'PRIVATE-ERROR-PAGE',
        }).encode('utf-8')
        widget = self.make_widget()
        widget.check_button.click()
        self.wait_idle(widget)

        self.assertEqual([request['path'] for request in self.server.state.requests],
                         ['/'])
        self.assertEqual(widget.state, STATE_MISMATCH)
        self.assertIn('本机端口上的响应不匹配', widget.status_label.text())
        self.assertNotIn('PRIVATE-ERROR-PAGE', self._labels_text(widget))

    def test_root_http_error_is_a_mismatch_not_a_network_failure(self):
        self.server.state.root_status = 404
        self.server.state.root_body = b'PRIVATE-ERROR-PAGE'
        widget = self.make_widget()
        widget.check_button.click()
        self.wait_idle(widget)

        self.assertEqual(widget.state, STATE_MISMATCH)
        text = widget.status_label.text()
        self.assertIn('本机端口上的响应不匹配', text)
        self.assertIn('HTTP 404', text)
        self.assertNotIn('检查失败', text)
        self.assertEqual([request['path'] for request in self.server.state.requests],
                         ['/'])
        self.assertNotIn('PRIVATE-ERROR-PAGE', self._labels_text(widget))

    def test_missing_login_can_be_reported_with_an_http_error_status(self):
        self.server.state.health_status = 403
        self.server.state.health_body = json.dumps({
            'status': 'degraded',
            'upstream_auth': 'missing',
            'token_expires_at': 'TOKEN-SECRET-DO-NOT-SHOW',
        }).encode('utf-8')
        widget = self.make_widget()
        widget.check_button.click()
        self.wait_idle(widget)

        self.assertEqual(widget.state, STATE_LOGIN_REQUIRED)
        text = widget.status_label.text()
        self.assertIn('本机响应的服务标识与 GenAI 反代一致，不能确认进程身份', text)
        self.assertIn('请本人登录', text)
        self.assertEqual([request['path'] for request in self.server.state.requests],
                         ['/', '/health'])
        self.assertNotIn('TOKEN-SECRET-DO-NOT-SHOW', self._labels_text(widget))

    def test_missing_login_asks_the_user_personally_without_auto_login(self):
        self.server.state.health_body = json.dumps({
            'status': 'degraded',
            'upstream_auth': 'missing',
            'token_expires_at': 'TOKEN-SECRET-DO-NOT-SHOW',
        }).encode('utf-8')
        widget = self.make_widget()
        widget.check_button.click()
        self.wait_idle(widget)

        self.assertEqual(widget.state, STATE_LOGIN_REQUIRED)
        text = widget.status_label.text()
        self.assertIn('本机响应的服务标识与 GenAI 反代一致，不能确认进程身份', text)
        self.assertIn('请本人登录', text)
        self.assertIn('不会自动打开浏览器', text)
        self.assertIn('桌宠不读取登录凭据文件', text)
        self.assertIn('反代自身可能读取其凭据', text)
        self.assertNotIn('反代不会读取凭据', text)
        self.assertNotIn('TOKEN-SECRET-DO-NOT-SHOW', self._labels_text(widget))
        self.assertEqual([request['path'] for request in self.server.state.requests],
                         ['/', '/health'])

    def test_redirects_are_ignored_without_following_or_health_probe(self):
        self.server.state.root_redirect = \
            f'{self.server.base_url}/stolen-root'
        widget = self.make_widget()
        widget.check_button.click()
        self.wait_idle(widget)

        self.assertEqual(widget.state, STATE_REDIRECT)
        self.assertEqual([request['path'] for request in self.server.state.requests],
                         ['/'])
        self.assertIn('已忽略重定向', widget.status_label.text())
        self.assertIn('未继续请求', widget.status_label.text())
        self.assertIn('本机端口上的响应不匹配', widget.status_label.text())
        self.assertNotIn('PRIVATE-REDIRECT-BODY', self._labels_text(widget))

        self.server.state.requests.clear()
        self.server.state.root_redirect = None
        self.server.state.health_redirect = \
            f'{self.server.base_url}/stolen-health'
        widget = self.make_widget()
        widget.check_button.click()
        self.wait_idle(widget)

        self.assertEqual(widget.state, STATE_REDIRECT)
        self.assertEqual([request['path'] for request in self.server.state.requests],
                         ['/', '/health'])
        self.assertIn('不能确认进程身份', widget.status_label.text())
        self.assertIn('已忽略重定向', widget.status_label.text())
        self.assertNotIn('PRIVATE-HEALTH-PAGE', self._labels_text(widget))

    def test_oversized_body_is_aborted_before_health(self):
        self.server.state.root_body = (
            b'{"service":"' + b'x' * (MAX_RESPONSE_BYTES + 1024) + b'"}'
        )
        widget = self.make_widget()
        started = time.monotonic()
        widget.check_button.click()
        self.wait_idle(widget, timeout=3.0)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, CHECK_TIMEOUT_MS / 1000 + 0.7)
        self.assertEqual(widget.state, STATE_TOO_LARGE)
        self.assertEqual([request['path'] for request in self.server.state.requests],
                         ['/'])
        self.assertIn('超过 64 KiB', widget.status_label.text())
        self.assertIn('已中止', widget.status_label.text())
        self.assertNotIn('x' * 256, self._labels_text(widget))

    def test_two_under_limit_responses_abort_when_their_total_exceeds_budget(self):
        # Each response is below 64 KiB, but together they exceed the shared
        # whole-check budget: the health phase must abort.
        padding = MAX_RESPONSE_BYTES // 2 + 4096
        root_body = json.dumps({
            'service': SERVICE_ID,
            'padding': 'r' * padding,
        }).encode('utf-8')
        health_body = json.dumps({
            'status': 'ok',
            'upstream_auth': 'valid',
            'padding': 'h' * padding,
        }).encode('utf-8')
        self.assertLess(len(root_body), MAX_RESPONSE_BYTES)
        self.assertLess(len(health_body), MAX_RESPONSE_BYTES)
        self.assertGreater(len(root_body) + len(health_body),
                           MAX_RESPONSE_BYTES)
        self.server.state.root_body = root_body
        self.server.state.health_body = health_body
        widget = self.make_widget()
        widget.check_button.click()
        self.wait_idle(widget, timeout=3.0)

        self.assertEqual(
            [request['path'] for request in self.server.state.requests],
            ['/', '/health'])
        self.assertEqual(widget.state, STATE_TOO_LARGE)
        text = widget.status_label.text()
        self.assertIn('不能确认进程身份', text)
        self.assertIn('超过 64 KiB', text)
        self.assertIn('已中止', text)
        self.assertNotIn('r' * 256, self._labels_text(widget))
        self.assertNotIn('h' * 256, self._labels_text(widget))
        self.assertIsNone(widget._active_reply)

    def test_two_responses_within_the_shared_budget_still_succeed(self):
        padding = MAX_RESPONSE_BYTES // 4
        root_body = json.dumps({
            'service': SERVICE_ID,
            'padding': 'r' * padding,
        }).encode('utf-8')
        health_body = json.dumps({
            'status': 'ok',
            'upstream_auth': 'valid',
            'padding': 'h' * padding,
        }).encode('utf-8')
        self.assertLess(len(root_body) + len(health_body),
                        MAX_RESPONSE_BYTES)
        self.server.state.root_body = root_body
        self.server.state.health_body = health_body
        widget = self.make_widget()
        widget.check_button.click()
        self.wait_idle(widget, timeout=3.0)

        self.assertEqual(
            [request['path'] for request in self.server.state.requests],
            ['/', '/health'])
        self.assertEqual(widget.state, STATE_RECOGNIZED)
        self.assertIn('健康检查报告登录有效', widget.status_label.text())
        self.assertNotIn('r' * 256, self._labels_text(widget))
        self.assertNotIn('h' * 256, self._labels_text(widget))
        self.assertIsNone(widget._active_reply)

    def test_a_new_check_after_a_too_large_result_reuses_the_shared_budget(self):
        self.server.state.root_body = (
            b'{"service":"' + b'x' * (MAX_RESPONSE_BYTES + 1024) + b'"}'
        )
        widget = self.make_widget()
        widget.check_button.click()
        self.wait_idle(widget, timeout=3.0)
        self.assertEqual(widget.state, STATE_TOO_LARGE)

        self.server.state.root_body = json.dumps(
            {'service': SERVICE_ID}).encode('utf-8')
        widget.check_button.click()
        self.wait_idle(widget, timeout=3.0)
        self.assertEqual(widget.state, STATE_RECOGNIZED)
        self.assertEqual(
            [request['path'] for request in self.server.state.requests],
            ['/', '/', '/health'])
        self.assertIsNone(widget._active_reply)

    def test_oversized_health_body_is_aborted_without_claiming_health(self):
        self.server.state.health_body = (
            b'{"status":"' + b'x' * (MAX_RESPONSE_BYTES + 1024) + b'"}'
        )
        widget = self.make_widget()
        started = time.monotonic()
        widget.check_button.click()
        self.wait_idle(widget, timeout=3.0)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, CHECK_TIMEOUT_MS / 1000 + 0.7)
        self.assertEqual(widget.state, STATE_TOO_LARGE)
        self.assertEqual([request['path'] for request in self.server.state.requests],
                         ['/', '/health'])
        text = widget.status_label.text()
        self.assertIn('不能确认进程身份', text)
        self.assertIn('超过 64 KiB', text)
        self.assertIn('已中止', text)
        self.assertIn('手动启动', text)
        self.assertNotIn('x' * 256, self._labels_text(widget))
        self.assertIsNone(widget._active_reply)

    def test_slow_drip_cannot_extend_the_absolute_deadline(self):
        self.server.state.drip_root = True
        widget = self.make_widget()
        started = time.monotonic()
        widget.check_button.click()
        self.wait_idle(widget, timeout=3.0)
        elapsed = time.monotonic() - started

        self.assertGreaterEqual(elapsed, CHECK_TIMEOUT_MS / 1000 - 0.15)
        self.assertLess(elapsed, CHECK_TIMEOUT_MS / 1000 + 0.7)
        self.assertEqual(widget.state, STATE_TIMEOUT)
        self.assertIn('超时', widget.status_label.text())
        self.assertIn('1.6 秒', widget.status_label.text())
        self.assertIn('手动启动', widget.status_label.text())
        self.assertEqual([request['path'] for request in self.server.state.requests],
                         ['/'])

    def test_health_stage_does_not_restart_the_absolute_deadline(self):
        # The root responds only after 0.7 s and /health then drips forever.
        # A per-stage timer would finish near 2.3 s; the absolute 1.6 s budget
        # must abort during the health stage instead.
        self.server.state.root_delay = 0.7
        self.server.state.drip_health = True
        widget = self.make_widget()
        started = time.monotonic()
        widget.check_button.click()
        self.assertTrue(self.wait_until(
            lambda: len(self.server.state.requests) >= 2, timeout=1.5))
        health_seen = time.monotonic()
        self.wait_idle(widget, timeout=3.0)
        elapsed = time.monotonic() - started
        health_elapsed = time.monotonic() - health_seen

        self.assertGreaterEqual(elapsed, CHECK_TIMEOUT_MS / 1000 - 0.2)
        self.assertLess(elapsed, 2.1)
        self.assertLess(health_elapsed, CHECK_TIMEOUT_MS / 1000 - 0.3)
        self.assertEqual(widget.state, STATE_TIMEOUT)
        self.assertEqual([request['path'] for request in self.server.state.requests],
                         ['/', '/health'])
        text = widget.status_label.text()
        self.assertIn('不能确认进程身份', text)
        self.assertIn('健康检查超过 1.6 秒', text)
        self.assertIn('手动启动', text)
        self.assertIsNone(widget._active_reply)

    def test_network_error_offers_manual_start_help(self):
        self.server.state.close_root = True
        widget = self.make_widget()
        widget.check_button.click()
        self.wait_idle(widget, timeout=3.0)

        self.assertIn('检查失败', widget.status_label.text())
        self.assertIn('手动启动', widget.status_label.text())
        self.assertIn('不会自动', widget.status_label.text())
        self.assertIn('执行', widget.status_label.text())
        self.assertIn('不会阻断其他对话路径', widget.status_label.text())
        self.assertEqual(widget.state, STATE_UNAVAILABLE)
        self.assertTrue(self.server.state.requests)
        self.assertTrue(all(request['path'] == '/'
                            for request in self.server.state.requests))

    def test_repeated_clicks_during_a_check_start_only_one_request_pair(self):
        self.server.state.root_delay = 0.8
        widget = self.make_widget()
        widget.check_button.click()
        self.assertTrue(self.wait_until(
            lambda: len(self.server.state.requests) >= 1, timeout=2.0))
        self.assertTrue(widget.busy)
        self.assertFalse(widget.check_button.isEnabled())
        widget.check_button.click()
        self.assertFalse(widget.start_check())
        self.assertFalse(widget.check())
        self.pump(0.05)
        self.assertEqual(
            [request['path'] for request in self.server.state.requests], ['/'])
        self.server.state.release.set()
        self.wait_idle(widget, timeout=3.0)
        self.assertEqual(
            [request['path'] for request in self.server.state.requests],
            ['/', '/health'])

    def test_closing_cancels_reply_and_discards_late_update(self):
        self.server.state.root_delay = 0.6
        widget = self.make_widget()
        widget.check_button.click()
        self.assertTrue(self.wait_until(
            lambda: len(self.server.state.requests) >= 1, timeout=2.0))
        self.assertTrue(widget.busy)
        before = widget.status_label.text()
        widget.close()
        self.pump(0.05)
        self.assertFalse(widget.busy)
        self.assertIsNone(widget._active_reply)
        self.server.state.release.set()
        self.pump(0.8)
        self.assertEqual(widget.status_label.text(), before)
        self.assertEqual(
            [request['path'] for request in self.server.state.requests], ['/'])

    def test_hiding_the_settings_page_cancels_without_late_update(self):
        self.server.state.root_delay = 0.6
        widget = self.make_widget()
        widget.check_button.click()
        self.assertTrue(self.wait_until(
            lambda: len(self.server.state.requests) >= 1, timeout=2.0))
        before = widget.status_label.text()
        widget.hide()
        self.pump(0.05)
        self.assertFalse(widget.busy)
        self.server.state.release.set()
        self.pump(0.8)
        self.assertEqual(widget.status_label.text(), before)
        widget.show()
        self.pump(0.02)
        self.assertIn('检查已取消', widget.status_label.text())
        self.assertEqual(
            [request['path'] for request in self.server.state.requests], ['/'])

    def test_settings_page_at_900_by_680_scrolls_and_reaches_the_card(self):
        store = _FakeStore()
        before = copy.deepcopy(dict(store))
        controller = _FakeController()
        controller.store = store
        panel = Panel(controller, _FakePet())
        self.panels.append(panel)
        panel.preview.timer.stop()
        panel.resize(900, 680)
        panel.stack.setCurrentIndex(1)
        panel.show()
        self.pump(0.08)

        self.assertEqual(panel.size(), QSize(900, 680))
        scroll = panel.stack.widget(1)
        self.assertIsInstance(scroll, QScrollArea)
        self.assertTrue(
            self.wait_until(
                lambda: scroll.verticalScrollBar().maximum() > 0, timeout=2.0),
            'settings page did not become scrollable at 900x680',
        )
        card = panel.genai_status
        self.assertIsInstance(card, GenAIStatusWidget)
        self.assertIs(card.parentWidget(), scroll.widget())
        self.assertEqual(card.title_label.text(), '上科大 GenAI 反代')
        self.assertEqual(card.check_button.text(), '检查本机反代')
        self.assertEqual(card.root_url, PRODUCTION_BASE_URL + '/')
        self.assertEqual(card.health_url, PRODUCTION_BASE_URL + '/health')

        scroll.verticalScrollBar().setValue(
            scroll.verticalScrollBar().maximum())
        self.pump(0.02)
        top_left = card.mapTo(scroll.viewport(), QPoint(0, 0))
        card_rect = QRect(top_left, card.size())
        self.assertTrue(card_rect.intersects(scroll.viewport().rect()))
        self.assertEqual(dict(store), before)
        self.assertEqual(store.saves, 0)

    def test_default_free_deepseek_and_glm_model_choices_are_unchanged(self):
        self.assertEqual(GENAI_DEFAULT_MODEL, 'DeepSeek-V4.1-Flash')
        self.assertEqual(
            tuple(GENAI_ALLOWED_MODELS),
            ('DeepSeek-V4.1-Flash', 'GLM-5.3-Flash'),
        )
        self.assertEqual(
            filter_genai_models([
                'GPT-6.1-Sol',
                'GLM-5.3-Flash',
                'DeepSeek-V4.1-Flash',
            ]),
            ['DeepSeek-V4.1-Flash', 'GLM-5.3-Flash'],
        )


if __name__ == '__main__':
    unittest.main()
