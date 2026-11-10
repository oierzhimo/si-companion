"""GenAI reverse-proxy compatibility tests using only loopback protocol stubs.

These tests never contact the school service.  The HTTP server below speaks the
same OpenAI-compatible routes as the local proxy (``GET /v1/models`` and
``POST /v1/chat/completions``) so the client's headers, limits, errors and Qt
lifecycle can be exercised against real sockets without a real model.
"""
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QLineEdit
import shiboken6

import si_pet.conversation as conversation_module
import si_pet.conversation_ui as conversation_ui_module
from si_pet.conversation import (Conversation, ConversationError, DEFAULT_GENAI_ENDPOINT,
                                 DEFAULT_LOCAL_ENDPOINT, GENAI_ALLOWED_MODELS,
                                 GENAI_DEFAULT_API_KEY, GENAI_DEFAULT_MODEL,
                                 GENAI_NO_MATCH_MESSAGE, LIMIT, fetch_models,
                                 filter_genai_models, looks_like_gpt_model, request_reply,
                                 validate_model_for_send)
from si_pet.conversation_ui import ConversationDialog

_QT_APP = None


def qt_application():
    global _QT_APP
    if _QT_APP is None:
        _QT_APP = QApplication.instance() or QApplication([])
    return _QT_APP


class StubGenAI:
    """Controllable loopback OpenAI-compatible server, never a real model."""

    def __init__(self):
        self.models_json = {'data': [{'id': 'alpha'}, {'id': 'beta'}, {'id': 'alpha'}]}
        self.models_raw = None
        self.models_status = 200
        self.models_location = None
        self.chat_status = 200
        self.chat_location = None
        self.reply = '好，我在这里。'
        self.error_body = b'PRIVATE-UPSTREAM-BODY sk-do-not-print'
        self.block_models = False
        self.models_release = threading.Event()
        self.models_gates = []
        self.requests = []
        self.condition = threading.Condition()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.0'

            def log_message(self, *args):
                pass

            def _record(self):
                length = int(self.headers.get('Content-Length', '0'))
                raw = self.rfile.read(length) if length else b''
                try:
                    body = json.loads(raw.decode('utf-8')) if raw else None
                except (UnicodeDecodeError, ValueError):
                    body = None
                with owner.condition:
                    gate = threading.Event()
                    if owner.models_release.is_set():
                        gate.set()
                    request = {
                        'method': self.command,
                        'path': self.path,
                        'headers': {key: value for key, value in self.headers.items()},
                        'body': body,
                        'raw_body': raw.decode('utf-8', 'replace'),
                    }
                    owner.requests.append(request)
                    if self.command == 'GET':
                        owner.models_gates.append(gate)
                    owner.condition.notify_all()
                return gate

            def _send(self, status, body=b'', extra_headers=()):
                try:
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    for key, value in extra_headers:
                        self.send_header(key, value)
                    self.end_headers()
                    if body:
                        self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

            def do_GET(self):
                gate = self._record()
                if self.path != '/v1/models':
                    self._send(404, b'{"error":"not found"}')
                    return
                if owner.block_models and not owner.models_release.is_set():
                    gate.wait(15.0)
                if owner.models_status != 200:
                    headers = []
                    if owner.models_status in (301, 302, 303, 307, 308) and owner.models_location:
                        headers.append(('Location', owner.models_location))
                    self._send(owner.models_status, owner.error_body, headers)
                    return
                if owner.models_raw is not None:
                    raw = owner.models_raw
                else:
                    raw = json.dumps(owner.models_json, ensure_ascii=False).encode('utf-8')
                self._send(200, raw)

            def do_POST(self):
                self._record()
                if self.path != '/v1/chat/completions':
                    self._send(404, b'{"error":"not found"}')
                    return
                if owner.chat_status != 200:
                    headers = []
                    if owner.chat_status in (301, 302, 303, 307, 308) and owner.chat_location:
                        headers.append(('Location', owner.chat_location))
                    self._send(owner.chat_status, owner.error_body, headers)
                    return
                raw = json.dumps(
                    {'choices': [{'message': {'content': owner.reply}}]},
                    ensure_ascii=False,
                ).encode('utf-8')
                self._send(200, raw)

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, name='genai-stub-http', daemon=True
        )
        self.thread.start()
        self.port = self.httpd.server_port
        self.endpoint = f'http://127.0.0.1:{self.port}/v1'

    def matching(self, method):
        with self.condition:
            return [request for request in self.requests if request['method'] == method]

    def wait_for_requests(self, count, timeout=5.0):
        deadline = time.monotonic() + timeout
        with self.condition:
            while len(self.requests) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(remaining)
        return True

    def release_models(self):
        self.models_release.set()
        with self.condition:
            for gate in self.models_gates:
                gate.set()

    def stop(self):
        self.release_models()
        self.httpd.shutdown()
        self.httpd.server_close()


class _TrackedBody(io.BytesIO):
    """BytesIO that records reads so a test can prove the body never leaked."""

    def __init__(self, data=b''):
        super().__init__(data)
        self.read_calls = 0

    def read(self, *args, **kwargs):
        self.read_calls += 1
        return super().read(*args, **kwargs)


class _RaisingOpener:
    """Minimal opener stand-in that raises one prepared HTTPError."""

    def __init__(self, error):
        self.error = error

    def open(self, request, timeout=None):
        raise self.error


def _tracked_http_error(code, body=b'PRIVATE-UPSTREAM-BODY sk-do-not-print'):
    response = _TrackedBody(body)
    error = urllib.error.HTTPError(
        'http://127.0.0.1:9/v1/models', code, 'upstream failure',
        {'Content-Type': 'text/plain'}, response,
    )
    return error, response


class GenAIProtocolTests(unittest.TestCase):
    def setUp(self):
        self.server = StubGenAI()
        self.addCleanup(self.server.stop)

    def test_genai_filter_keeps_only_verified_domestic_models_and_fetch_stays_generic(self):
        self.server.models_json = {'data': [
            {'id': 'DeepSeek-V4.1-Flash'},
            {'id': 'deepseek-v4.1-flash'},
            {'id': 'GLM-5.3-Flash'},
            {'id': 'glm-5.3-flash'},
            {'id': 'GPT-6.1-Sol'},
            {'id': 'OpenAI-GPT-4o'},
            {'id': 'ChatGPT-Latest'},
            {'id': 'Qwen3-Coder'},
            {'id': 'Kimi-K2'},
            {'id': 'DeepSeek-V4.1-Flash-Pro'},
            {'id': 'GLM-5.3-Flash-Lite'},
        ]}
        # The protocol function must keep returning the raw chat models; the
        # domestic allow-list belongs to the UI/explicit helper, not the wire.
        raw = fetch_models(self.server.endpoint)
        self.assertIn('GPT-6.1-Sol', raw)
        self.assertIn('Qwen3-Coder', raw)
        self.assertIn('Kimi-K2', raw)

        filtered = filter_genai_models(raw)
        self.assertEqual(
            [name.casefold() for name in filtered],
            ['deepseek-v4.1-flash', 'glm-5.3-flash'],
        )
        allowed = {name.casefold() for name in GENAI_ALLOWED_MODELS}
        self.assertTrue(all(name.casefold() in allowed for name in filtered))
        self.assertFalse(any(looks_like_gpt_model(name) for name in filtered))
        self.assertNotIn('DeepSeek-V4.1-Flash-Pro', filtered)
        self.assertNotIn('GLM-5.3-Flash-Lite', filtered)

    def test_model_validation_is_case_insensitive_for_genai_and_refuses_gpt_everywhere(self):
        self.assertEqual(validate_model_for_send('genai', 'deepseek-v4.1-flash'),
                         'DeepSeek-V4.1-Flash')
        self.assertEqual(validate_model_for_send('genai', 'glm-5.3-flash'),
                         'GLM-5.3-Flash')
        # A service-returned spelling wins over our constant casing.
        self.assertEqual(
            validate_model_for_send('genai', 'deepseek-v4.1-flash',
                                    ['GLM-5.3-Flash', 'deepseek-v4.1-flash']),
            'deepseek-v4.1-flash',
        )
        for bad in ('GPT-6.1-Sol', 'gpt-4o', 'ChatGPT-Latest',
                    'openai/gpt-4o', 'Qwen3-Coder', 'Kimi-K2'):
            with self.subTest(provider='genai', model=bad):
                with self.assertRaises(ConversationError) as caught:
                    validate_model_for_send('genai', bad)
                self.assertIn('国内开源', str(caught.exception))
                self.assertIn('DeepSeek-V4.1-Flash', str(caught.exception))

        # The ordinary local preset keeps accepting self-deployed names, but
        # clearly GPT/OpenAI/ChatGPT-branded IDs are still refused.
        self.assertEqual(validate_model_for_send('local', 'qwen2.5-coder-local'),
                         'qwen2.5-coder-local')
        for bad in ('GPT-6.1-Sol', 'gpt-4o', 'ChatGPT-Latest', 'openai/gpt-4o'):
            with self.subTest(provider='local', model=bad):
                with self.assertRaises(ConversationError) as caught:
                    validate_model_for_send('local', bad)
                self.assertIn('GPT', str(caught.exception))
                self.assertIn('国内开源', str(caught.exception))
        self.assertEqual(self.server.requests, [],
                         'pure model validation must not touch the network')

    def test_bearer_header_only_and_never_in_payload_or_save(self):
        key = 'sk-test-private-value'
        messages = [{'role': 'user', 'content': '我在画画'}]
        reply = request_reply(self.server.endpoint, 'actual-model', messages, key)
        self.assertEqual(reply, '好，我在这里。')

        posts = self.server.matching('POST')
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]['path'], '/v1/chat/completions')
        self.assertEqual(posts[0]['headers'].get('Authorization'), 'Bearer ' + key)
        self.assertNotIn(key, posts[0]['raw_body'])
        self.assertEqual(posts[0]['body']['messages'], messages)

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'conversation.json'
            session = Conversation(path)
            session.endpoint = self.server.endpoint
            session.model = 'actual-model'
            session.provider = 'genai'
            session.commit('我在画画', reply)
            saved = path.read_text(encoding='utf-8')
            self.assertNotIn(key, saved)
            self.assertEqual(json.loads(saved)['provider'], 'genai')

    def test_legacy_three_argument_call_sends_no_authorization(self):
        request_reply(self.server.endpoint, 'actual-model',
                      [{'role': 'user', 'content': '你好'}])
        posts = self.server.matching('POST')
        self.assertEqual(len(posts), 1)
        self.assertNotIn('Authorization', posts[0]['headers'])

        self.assertEqual(fetch_models(self.server.endpoint), ['alpha', 'beta'])
        gets = self.server.matching('GET')
        self.assertEqual(len(gets), 1)
        self.assertNotIn('Authorization', gets[0]['headers'])

    def test_friendly_chat_statuses_do_not_leak_body_or_key(self):
        key = 'sk-do-not-print'
        messages = [{'role': 'user', 'content': '测试'}]
        cases = {
            401: ('反代访问口令有误',),
            403: ('反代访问口令有误',),
            503: ('学校登录', '反代'),
            429: ('忙', '稍后'),
            500: ('500',),
        }
        for status, phrases in cases.items():
            with self.subTest(status=status):
                self.server.chat_status = status
                with self.assertRaises(ConversationError) as caught:
                    request_reply(self.server.endpoint, 'actual-model', messages, key)
                text = str(caught.exception)
                for phrase in phrases:
                    self.assertIn(phrase, text)
                self.assertNotIn(key, text)
                self.assertNotIn('PRIVATE-UPSTREAM-BODY', text)

    def test_api_key_validation_rejects_injection_overlength_and_non_string(self):
        bad_keys = ('bad\rkey', 'bad\nkey', 'x' * 513, None, 123, b'bytes', '密钥')
        for bad in bad_keys:
            with self.subTest(key=repr(bad)):
                with self.assertRaises(ConversationError) as caught:
                    request_reply(self.server.endpoint, 'actual-model',
                                  [{'role': 'user', 'content': '你好'}], bad)
                self.assertNotIn(str(bad), str(caught.exception))
        self.assertEqual(self.server.requests, [], 'validation must happen before any request')

    def test_models_bearer_dedupe_and_bounded_result(self):
        key = 'sk-models-private'
        data = [{'id': 'dup'}, {'id': 'dup'}, {'id': ''}, {'id': None},
                {'id': 7}, {'id': 'x' * 201}]
        data.extend({'id': f'model-{index:03d}'} for index in range(250))
        self.server.models_json = {'data': data}
        models = fetch_models(self.server.endpoint, key)

        self.assertEqual(len(models), 200)
        self.assertEqual(len(set(models)), 200)
        self.assertEqual(models[0], 'dup')
        self.assertTrue(all(isinstance(item, str) and 0 < len(item) <= 200 for item in models))
        self.assertTrue(all(item != 'x' * 201 for item in models))
        gets = self.server.matching('GET')
        self.assertEqual(len(gets), 1)
        self.assertEqual(gets[0]['path'], '/v1/models')
        self.assertEqual(gets[0]['headers'].get('Authorization'), 'Bearer ' + key)
        self.assertEqual(self.server.matching('POST'), [])

    def test_models_capabilities_chat_false_excluded_and_missing_still_compatible(self):
        self.server.models_json = {'data': [
            {'id': 'plain-model'},
            {'id': 'chat-model', 'capabilities': {'chat': True}},
            {'id': 'image-capability', 'capabilities': {'chat': False, 'image': True}},
            {'id': 'GPT-Image-2.5-Sunburst', 'capabilities': {'chat': False}},
            {'id': 'no-chat-key', 'capabilities': {'image': True}},
            {'id': 'image-looking-name'},
            {'id': 'string-false', 'capabilities': {'chat': 'false'}},
            {'id': 'numeric-zero', 'capabilities': {'chat': 0}},
            {'id': 'capabilities-list', 'capabilities': ['chat']},
            {'id': 'capabilities-null', 'capabilities': None},
            {'id': 'capabilities-missing'},
        ]}
        models = fetch_models(self.server.endpoint)

        # Only the strict boolean False is excluded; nothing is guessed by name
        # and every plain OpenAI-compatible entry keeps working.
        self.assertEqual(models, [
            'plain-model', 'chat-model', 'no-chat-key', 'image-looking-name',
            'string-false', 'numeric-zero', 'capabilities-list',
            'capabilities-null', 'capabilities-missing',
        ])
        self.assertNotIn('image-capability', models)
        self.assertNotIn('GPT-Image-2.5-Sunburst', models)
        gets = self.server.matching('GET')
        self.assertEqual(len(gets), 1)
        self.assertEqual(gets[0]['path'], '/v1/models')
        self.assertEqual(self.server.matching('POST'), [])

    def test_models_malformed_payloads_are_friendly_errors(self):
        cases = (
            {'models': []},
            {'data': 'not-a-list'},
            [],
            {'data': [{'id': ''}, {'id': 2}, {'id': 'x' * 201}]},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                self.server.models_json = payload
                self.server.models_raw = None
                with self.assertRaises(ConversationError) as caught:
                    fetch_models(self.server.endpoint)
                text = str(caught.exception)
                self.assertTrue('模型' in text)
                self.assertNotIn('PRIVATE-UPSTREAM-BODY', text)

        self.server.models_json = {'data': []}
        self.server.models_raw = b'{broken'
        with self.assertRaises(ConversationError) as caught:
            fetch_models(self.server.endpoint)
        self.assertIn('格式', str(caught.exception))

        self.server.models_raw = b'x' * (LIMIT + 2)
        with self.assertRaises(ConversationError) as caught:
            fetch_models(self.server.endpoint)
        self.assertIn('过大', str(caught.exception))

    def test_models_status_errors_are_friendly_and_do_not_leak(self):
        key = 'sk-models-status-private'
        cases = {
            401: ('反代访问口令有误',),
            403: ('反代访问口令有误',),
            503: ('学校登录', '反代'),
            429: ('忙', '稍后'),
        }
        for status, phrases in cases.items():
            with self.subTest(status=status):
                self.server.models_status = status
                with self.assertRaises(ConversationError) as caught:
                    fetch_models(self.server.endpoint, key)
                text = str(caught.exception)
                for phrase in phrases:
                    self.assertIn(phrase, text)
                self.assertNotIn(key, text)
                self.assertNotIn('PRIVATE-UPSTREAM-BODY', text)

    def test_http_error_response_is_closed_and_body_never_read(self):
        key = 'sk-error-close-private'
        cases = {401: '反代访问口令', 503: '学校登录', 429: '忙', 500: '500'}
        calls = {
            'chat': lambda: request_reply(
                self.server.endpoint, 'actual-model',
                [{'role': 'user', 'content': '失败测试'}], key),
            'models': lambda: fetch_models(self.server.endpoint, key),
        }
        for name, call in calls.items():
            for status, phrase in cases.items():
                with self.subTest(call=name, status=status):
                    error, response = _tracked_http_error(status)
                    with mock.patch.object(conversation_module, '_opener',
                                           lambda: _RaisingOpener(error)):
                        with self.assertRaises(ConversationError) as caught:
                            call()
                    self.assertTrue(response.closed,
                                    'HTTPError response must be closed')
                    self.assertEqual(response.read_calls, 0,
                                     'HTTPError body must never be read')
                    text = str(caught.exception)
                    self.assertIn(phrase, text)
                    self.assertNotIn(key, text)
                    self.assertNotIn('PRIVATE-UPSTREAM-BODY', text)
        self.assertEqual(self.server.requests, [],
                         'closed-error tests must not touch the stub server')

    def test_models_redirect_is_not_followed_and_key_is_not_forwarded(self):
        key = 'sk-redirect-private'
        self.server.models_status = 302
        self.server.models_location = f'http://127.0.0.1:{self.server.port}/stolen'
        with self.assertRaises(ConversationError) as caught:
            fetch_models(self.server.endpoint, key)
        self.assertIn('重定向', str(caught.exception))
        self.assertNotIn(key, str(caught.exception))
        self.assertNotIn('PRIVATE-UPSTREAM-BODY', str(caught.exception))
        gets = self.server.matching('GET')
        self.assertEqual(len(gets), 1)
        self.assertEqual(gets[0]['path'], '/v1/models')
        self.assertEqual(gets[0]['headers'].get('Authorization'), 'Bearer ' + key)
        self.assertFalse(any(request['path'] == '/stolen' for request in gets))

    def test_chat_redirect_is_not_followed_and_key_is_not_forwarded(self):
        key = 'sk-chat-redirect-private'
        self.server.chat_status = 302
        self.server.chat_location = f'http://127.0.0.1:{self.server.port}/stolen-chat'
        with self.assertRaises(ConversationError) as caught:
            request_reply(self.server.endpoint, 'actual-model',
                          [{'role': 'user', 'content': '重定向测试'}], key)
        self.assertIn('重定向', str(caught.exception))
        self.assertNotIn(key, str(caught.exception))
        self.assertNotIn('PRIVATE-UPSTREAM-BODY', str(caught.exception))
        posts = self.server.matching('POST')
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]['path'], '/v1/chat/completions')
        self.assertEqual(posts[0]['headers'].get('Authorization'), 'Bearer ' + key)
        self.assertFalse(any(request['path'] == '/stolen-chat'
                             for request in self.server.requests))


class RecordingController:
    def __init__(self):
        self.spoken = []

    def say(self, text, force=False):
        self.spoken.append((text, force))


class GenAIQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_application()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'conversation.json'
        self.ctrl = RecordingController()
        self.server = StubGenAI()
        self.addCleanup(self.server.stop)
        self.dialogs = []

    def tearDown(self):
        self.server.release_models()
        for dialog in self.dialogs:
            try:
                self.wait_until(lambda d=dialog: not d.busy and not d.model_busy, 2.0)
            except RuntimeError:
                pass
        self.wait_until(
            lambda: not any(t.name in ('si-models', 'si-local-chat') and t.is_alive()
                            for t in threading.enumerate()),
            2.0,
        )
        for dialog in self.dialogs:
            try: dialog.completed.disconnect()
            except (RuntimeError, TypeError): pass
            try: dialog.models_ready.disconnect()
            except (RuntimeError, TypeError): pass
            try: dialog.close()
            except RuntimeError: pass
            if not getattr(dialog, 'busy', False) and not getattr(dialog, 'model_busy', False):
                try: shiboken6.delete(dialog)
                except RuntimeError: pass
        self.dialogs.clear()
        self.pump(0.05)

    def pump(self, seconds=0.0):
        deadline = time.monotonic() + seconds
        while True:
            self.app.processEvents(QEventLoop.AllEvents, 20)
            if time.monotonic() >= deadline:
                break
            time.sleep(0.005)

    def wait_until(self, predicate, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents(QEventLoop.AllEvents, 20)
            if predicate():
                return True
            time.sleep(0.005)
        self.app.processEvents(QEventLoop.AllEvents, 20)
        return bool(predicate())

    def wait_requests(self, count, timeout=5.0):
        self.assertTrue(self.server.wait_for_requests(count, timeout),
                        f'stub server did not receive request #{count}')

    def wait_models_idle(self, dialog, timeout=5.0):
        self.assertTrue(self.wait_until(lambda: not dialog.model_busy, timeout),
                        'model query did not finish')
        self.pump(0.03)

    def wait_chat_idle(self, dialog, timeout=5.0):
        self.assertTrue(self.wait_until(lambda: not dialog.busy, timeout),
                        'chat request did not finish')
        self.pump(0.03)

    def make_dialog(self, *, show=False):
        # These generic protocol/lifecycle stubs use arbitrary names such as
        # alpha/beta/stub-model.  Select the ordinary local preset explicitly so
        # the GenAI domestic allow-list does not change their original meaning.
        dialog = ConversationDialog(self.ctrl, self.path)
        dialog.service.setCurrentIndex(0)
        self.pump(0.02)
        dialog.endpoint.setText(self.server.endpoint)
        dialog.model.setText('stub-model')
        self.dialogs.append(dialog)
        if show:
            dialog.show()
            self.pump(0.02)
        return dialog

    def make_genai_dialog(self):
        # A fresh dialog starts on GenAI with the verified domestic default.
        dialog = ConversationDialog(self.ctrl, self.path)
        dialog.endpoint.setText(self.server.endpoint)
        self.dialogs.append(dialog)
        return dialog

    def test_new_dialog_defaults_to_genai_but_legacy_settings_are_preserved(self):
        dialog = ConversationDialog(self.ctrl, self.path)
        self.dialogs.append(dialog)
        self.assertEqual(dialog.service.currentData(), 'genai')
        self.assertEqual(dialog.endpoint.text(), DEFAULT_GENAI_ENDPOINT)
        self.assertEqual(dialog.api_key.text(), GENAI_DEFAULT_API_KEY)
        self.assertEqual(dialog.api_key.echoMode(), QLineEdit.EchoMode.Password)
        self.assertEqual(dialog.model.text(), GENAI_DEFAULT_MODEL)
        self.assertEqual(dialog.session.model, GENAI_DEFAULT_MODEL)

        # Showing/opening must not fetch models or send a test conversation.
        dialog.endpoint.setText(self.server.endpoint)
        dialog.show()
        self.pump(0.15)
        self.assertEqual(self.server.requests, [])
        self.assertFalse(dialog.model_busy)
        self.assertFalse(dialog.busy)

        legacy_path = Path(self.tmp.name) / 'legacy.json'
        legacy_path.write_text(json.dumps({
            'turns': [],
            'profile': '旧偏好',
            'endpoint': self.server.endpoint,
            'model': 'legacy-model',
        }, ensure_ascii=False), encoding='utf-8')
        legacy = ConversationDialog(self.ctrl, legacy_path)
        self.dialogs.append(legacy)
        self.assertEqual(legacy.service.currentData(), 'local')
        self.assertEqual(legacy.endpoint.text(), self.server.endpoint)
        self.assertEqual(legacy.model.text(), 'legacy-model')
        self.assertEqual(legacy.profile.toPlainText(), '旧偏好')
        self.assertEqual(legacy.api_key.text(), '')

    def test_preset_switch_fills_documented_values_without_querying(self):
        dialog = ConversationDialog(self.ctrl, self.path)
        self.dialogs.append(dialog)
        dialog.service.setCurrentIndex(0)
        self.pump(0.02)
        self.assertEqual(dialog.endpoint.text(), DEFAULT_LOCAL_ENDPOINT)
        self.assertEqual(dialog.api_key.text(), '')
        self.assertEqual(dialog.provider, 'local')

        dialog.model.setText('typed-model')
        dialog.service.setCurrentIndex(1)
        self.pump(0.02)
        self.assertEqual(dialog.endpoint.text(), DEFAULT_GENAI_ENDPOINT)
        self.assertEqual(dialog.api_key.text(), GENAI_DEFAULT_API_KEY)
        self.assertEqual(dialog.provider, 'genai')
        self.assertEqual(dialog.model.text(), GENAI_DEFAULT_MODEL)
        self.assertEqual(dialog.session.model, GENAI_DEFAULT_MODEL)
        self.assertEqual(self.server.requests, [])

    def test_model_query_is_async_completes_field_and_keeps_draft(self):
        self.server.block_models = True
        dialog = self.make_dialog(show=True)
        dialog.model.setText('already-typed')
        dialog.input.setPlainText('等待时保留的草稿')
        ticks = []
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks.append(time.monotonic()))
        timer.start()
        self.addCleanup(timer.stop)

        started = time.monotonic()
        dialog.get_models.click()
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.0, 'model discovery blocked the main thread')
        self.assertTrue(dialog.model_busy)
        self.assertFalse(dialog.get_models.isEnabled())
        self.wait_requests(1)
        self.assertTrue(
            self.wait_until(lambda: len(ticks) >= 3, 2.0),
            'Qt timer events did not fire while model discovery was pending',
        )
        self.assertEqual(dialog.model.text(), 'already-typed')
        self.assertEqual(dialog.input.toPlainText(), '等待时保留的草稿')
        self.assertEqual(self.server.matching('POST'), [])

        self.server.release_models()
        self.wait_models_idle(dialog)
        self.assertEqual(dialog.available_models, ['alpha', 'beta'])
        self.assertEqual(dialog.model.text(), 'already-typed')
        self.assertEqual(dialog.input.toPlainText(), '等待时保留的草稿')
        self.assertTrue(dialog.get_models.isEnabled())
        self.assertIsNotNone(dialog.model.completer())
        self.assertEqual(dialog.model.completer().model().rowCount(), 2)
        self.assertIn('已获取 2 个模型', dialog.status.text())
        self.assertEqual(self.server.matching('POST'), [])

    def test_repeated_model_clicks_do_not_start_concurrent_queries(self):
        self.server.block_models = True
        dialog = self.make_dialog(show=True)
        dialog.get_models.click()
        self.wait_requests(1)
        self.assertTrue(dialog.model_busy)

        dialog.get_models.setEnabled(True)
        dialog.get_models.click()
        self.pump(0.2)
        self.assertEqual(len(self.server.matching('GET')), 1)

        self.server.release_models()
        self.wait_models_idle(dialog)
        self.assertEqual(len(self.server.matching('GET')), 1)

    def test_late_models_after_close_and_reopen_cannot_overwrite_choice(self):
        self.server.block_models = True
        dialog = self.make_dialog(show=True)
        dialog.get_models.click()
        self.wait_requests(1)
        dialog.model.setText('manual-before-close')
        dialog.close()
        self.pump(0.02)
        self.assertFalse(dialog.model_busy)
        self.assertTrue(dialog.get_models.isEnabled())

        dialog.show()
        self.pump(0.02)
        dialog.model.setText('new-after-reopen')
        self.server.release_models()
        self.assertTrue(self.wait_until(
            lambda: not any(t.name == 'si-models' and t.is_alive()
                            for t in threading.enumerate()),
            2.0,
        ))
        self.pump(0.05)
        self.assertEqual(dialog.model.text(), 'new-after-reopen')
        self.assertEqual(dialog.available_models, [])
        self.assertNotIn('已获取', dialog.status.text())
        self.assertEqual(self.server.matching('POST'), [])

    def test_late_models_after_preset_switch_cannot_replace_new_choice(self):
        self.server.block_models = True
        # Start on GenAI so the switch to the local preset really invalidates
        # the pending generation, then prove a late list cannot touch it.
        dialog = self.make_genai_dialog()
        dialog.show()
        self.pump(0.02)
        dialog.get_models.click()
        self.wait_requests(1)
        dialog.service.setCurrentIndex(0)
        self.pump(0.02)
        dialog.model.setText('local-choice')
        self.assertEqual(dialog.provider, 'local')
        self.assertEqual(dialog.endpoint.text(), DEFAULT_LOCAL_ENDPOINT)

        self.server.release_models()
        self.assertTrue(self.wait_until(
            lambda: not any(t.name == 'si-models' and t.is_alive()
                            for t in threading.enumerate()),
            2.0,
        ))
        self.pump(0.05)
        self.assertEqual(dialog.model.text(), 'local-choice')
        self.assertEqual(dialog.endpoint.text(), DEFAULT_LOCAL_ENDPOINT)
        self.assertEqual(dialog.api_key.text(), '')
        self.assertEqual(dialog.available_models, [])
        self.assertNotIn('已获取', dialog.status.text())

    def test_saved_genai_gpt_or_unlisted_model_migrates_in_window_without_file_change(self):
        for index, saved_model in enumerate(('GPT-6.1-Sol', 'Qwen3-Coder', '')):
            with self.subTest(saved_model=saved_model):
                path = Path(self.tmp.name) / f'genai-legacy-{index}.json'
                path.write_text(json.dumps({
                    'turns': [{'user': '历史问题', 'assistant': '历史回答'}],
                    'profile': '旧偏好',
                    'endpoint': DEFAULT_GENAI_ENDPOINT,
                    'model': saved_model,
                    'provider': 'genai',
                }, ensure_ascii=False), encoding='utf-8')
                before = path.read_bytes()
                dialog = ConversationDialog(self.ctrl, path)
                self.dialogs.append(dialog)
                self.assertEqual(dialog.service.currentData(), 'genai')
                self.assertEqual(dialog.model.text(), GENAI_DEFAULT_MODEL)
                self.assertEqual(dialog.session.model, GENAI_DEFAULT_MODEL)
                self.assertEqual(dialog.session.turns,
                                 [{'user': '历史问题', 'assistant': '历史回答'}])
                self.assertEqual(dialog.profile.toPlainText(), '旧偏好')
                self.assertIn('原文件', dialog.status.text())
                self.assertIn('未修改', dialog.status.text())
                self.assertEqual(path.read_bytes(), before,
                                 'opening the window must not rewrite history/config')
                self.assertEqual(self.server.requests, [])

    def test_saved_local_gpt_is_cleared_in_window_without_touching_file(self):
        path = Path(self.tmp.name) / 'local-legacy.json'
        path.write_text(json.dumps({
            'turns': [{'user': '旧问题', 'assistant': '旧回答'}],
            'profile': '',
            'endpoint': DEFAULT_LOCAL_ENDPOINT,
            'model': 'gpt-4o',
            'provider': 'local',
        }, ensure_ascii=False), encoding='utf-8')
        before = path.read_bytes()
        dialog = ConversationDialog(self.ctrl, path)
        self.dialogs.append(dialog)
        self.assertEqual(dialog.service.currentData(), 'local')
        self.assertEqual(dialog.model.text(), '')
        self.assertEqual(dialog.session.model, '')
        self.assertIn('GPT', dialog.status.text())
        self.assertIn('国内开源', dialog.status.text())
        self.assertIn('未修改', dialog.status.text())
        self.assertEqual(path.read_bytes(), before,
                         'opening the window must not rewrite the old GPT config')
        self.assertEqual(self.server.requests, [])

    def test_genai_manual_gpt_is_rejected_before_thread_or_file_write(self):
        dialog = self.make_genai_dialog()
        dialog.model.setText('GPT-6.1-Sol')
        dialog.input.setPlainText('这句不应发送出去')
        with mock.patch.object(conversation_ui_module, 'request_reply') as fake_reply:
            dialog.send.click()
            self.pump(0.1)
            fake_reply.assert_not_called()
        self.assertFalse(dialog.busy)
        self.assertFalse(self.path.exists(), 'rejected choice must not be saved')
        self.assertEqual(self.server.matching('POST'), [])
        self.assertIn('GPT', dialog.status.text())
        self.assertIn('国内开源', dialog.status.text())

    def test_genai_manual_unlisted_domestic_name_is_rejected_before_thread_or_file_write(self):
        dialog = self.make_genai_dialog()
        dialog.model.setText('Qwen3-Coder')
        dialog.input.setPlainText('未核验的型号也不应发送')
        with mock.patch.object(conversation_ui_module, 'request_reply') as fake_reply:
            dialog.send.click()
            self.pump(0.1)
            fake_reply.assert_not_called()
        self.assertFalse(dialog.busy)
        self.assertFalse(self.path.exists(), 'rejected choice must not be saved')
        self.assertEqual(self.server.matching('POST'), [])
        self.assertIn('国内开源', dialog.status.text())
        self.assertIn('GLM-5.3-Flash', dialog.status.text())

    def test_local_gpt_name_is_rejected_before_thread_or_file_write(self):
        dialog = self.make_dialog()
        dialog.model.setText('openai/gpt-4o')
        dialog.input.setPlainText('本机也不能使用 GPT 命名')
        with mock.patch.object(conversation_ui_module, 'request_reply') as fake_reply:
            dialog.send.click()
            self.pump(0.1)
            fake_reply.assert_not_called()
        self.assertFalse(dialog.busy)
        self.assertFalse(self.path.exists(), 'rejected choice must not be saved')
        self.assertEqual(self.server.matching('POST'), [])
        self.assertIn('GPT', dialog.status.text())
        self.assertIn('国内开源', dialog.status.text())

    def test_genai_domestic_model_sends_normally_with_verified_spelling(self):
        dialog = self.make_genai_dialog()
        dialog.model.setText('glm-5.3-flash')
        dialog.input.setPlainText('这是一句合成验收文本。')
        dialog.send.click()
        self.wait_requests(1)
        self.wait_chat_idle(dialog)

        posts = self.server.matching('POST')
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]['body']['model'], 'GLM-5.3-Flash')
        self.assertEqual(dialog.model.text(), 'GLM-5.3-Flash')
        self.assertEqual(len(dialog.session.turns), 1)
        self.assertEqual(dialog.session.turns[0]['user'], '这是一句合成验收文本。')
        self.assertEqual(dialog.session.turns[0]['assistant'], '好，我在这里。')
        self.assertIn('已保存到本机', dialog.status.text())

        raw = self.path.read_text(encoding='utf-8')
        saved = json.loads(raw)
        self.assertEqual(saved['model'], 'GLM-5.3-Flash')
        self.assertEqual(saved['provider'], 'genai')
        self.assertNotIn(GENAI_DEFAULT_API_KEY, raw)

    def test_local_self_deployed_domestic_model_still_sends(self):
        dialog = self.make_dialog()
        dialog.model.setText('qwen2.5-coder-local')
        dialog.input.setPlainText('本机自建模型也要能聊。')
        dialog.send.click()
        self.wait_requests(1)
        self.wait_chat_idle(dialog)

        posts = self.server.matching('POST')
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]['body']['model'], 'qwen2.5-coder-local')
        self.assertEqual(len(dialog.session.turns), 1)
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(saved['provider'], 'local')
        self.assertEqual(saved['model'], 'qwen2.5-coder-local')

    def test_genai_get_models_click_shows_only_allowed_domestic_models(self):
        self.server.models_json = {'data': [
            {'id': 'DeepSeek-V4.1-Flash'},
            {'id': 'deepseek-v4.1-flash'},
            {'id': 'GLM-5.3-Flash'},
            {'id': 'GPT-6.1-Sol'},
            {'id': 'Qwen3-Coder'},
            {'id': 'Kimi-K2'},
        ]}
        dialog = self.make_genai_dialog()
        dialog.show()
        self.pump(0.02)
        dialog.get_models.click()
        self.wait_models_idle(dialog)

        available = list(dialog.available_models)
        self.assertEqual(
            [name.casefold() for name in available],
            ['deepseek-v4.1-flash', 'glm-5.3-flash'],
        )
        self.assertFalse(any(looks_like_gpt_model(name) for name in available))
        self.assertEqual(dialog.model.completer().model().rowCount(), 2)
        self.assertEqual(dialog.model.text(), GENAI_DEFAULT_MODEL)
        self.assertIn('已核验的国内开源模型', dialog.status.text())
        self.assertEqual(self.server.matching('POST'), [])

    def test_genai_no_matching_domestic_model_shows_notice_without_gpt_fallback(self):
        cases = (
            {'data': [
                {'id': 'GPT-6.1-Sol'},
                {'id': 'GPT-Image-2.5-Sunburst', 'capabilities': {'chat': False}},
                {'id': 'Qwen3-Coder'},
                {'id': 'Kimi-K2'},
            ]},
            {'data': []},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                self.server.models_json = payload
                dialog = self.make_genai_dialog()
                dialog.get_models.click()
                self.wait_models_idle(dialog)

                self.assertEqual(dialog.available_models, [])
                self.assertEqual(dialog.status.text(), GENAI_NO_MATCH_MESSAGE)
                self.assertEqual(dialog.model.text(), GENAI_DEFAULT_MODEL)
                self.assertNotIn('GPT-6.1-Sol', dialog.status.text())
        self.assertEqual(self.server.matching('POST'), [])

    def test_model_failure_keeps_fields_and_chat_with_existing_model_still_works(self):
        key = 'sk-ui-private'
        self.server.models_status = 500
        dialog = self.make_dialog(show=True)
        dialog.api_key.setText(key)
        dialog.model.setText('known-model')
        dialog.input.setPlainText('模型列表失败也要能发送')
        dialog.get_models.click()
        self.wait_models_idle(dialog)

        self.assertIn('500', dialog.status.text())
        self.assertNotIn('PRIVATE-UPSTREAM-BODY', dialog.status.text())
        self.assertNotIn(key, dialog.status.text())
        self.assertEqual(dialog.model.text(), 'known-model')
        self.assertEqual(dialog.input.toPlainText(), '模型列表失败也要能发送')
        self.assertEqual(dialog.api_key.text(), key)
        self.assertEqual(dialog.available_models, [])

        dialog.send.click()
        self.wait_chat_idle(dialog)
        self.assertEqual(len(dialog.session.turns), 1)
        self.assertEqual(dialog.session.turns[0]['user'], '模型列表失败也要能发送')
        self.assertIn('已保存到本机', dialog.status.text())
        posts = self.server.matching('POST')
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]['headers'].get('Authorization'), 'Bearer ' + key)
        saved = self.path.read_text(encoding='utf-8')
        self.assertNotIn(key, saved)
        self.assertIn('known-model', saved)


if __name__ == '__main__':
    unittest.main()
