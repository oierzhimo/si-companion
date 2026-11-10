"""Offline tests for the replaceable model-connection adapter.

No real HTTP service, TLS peer, QQ process, credential, user setting, or chat
data is used.  Every opener is a synthetic object or the real urllib opener
inspected without opening a socket.
"""
import json
import ssl
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
import group_core  # noqa: E402
import model_connection as mc  # noqa: E402
from core import QQCore  # noqa: E402
from group_core import GROUP_ERROR_TEXT, GroupChat  # noqa: E402


SECRET = 'sk-synthetic-model-secret'
ACTIVE_ENDPOINT = 'https://models.example.test/v1'
ACTIVE_MODEL = 'deepseek-v4-pro'
MESSAGES = [{'role': 'user', 'content': '合成输入'}]


class FakeResponse:
    def __init__(self, raw):
        self.raw = raw

    def read(self, limit=-1):
        if limit is None or limit < 0:
            return self.raw
        return self.raw[:limit]

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


class FakeOpener:
    def __init__(self, raw=b'', error=None):
        self.raw = raw
        self.error = error
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        if self.error is not None:
            raise self.error
        return FakeResponse(self.raw)


def header(request, name):
    for key, value in request.header_items():
        if key.lower() == name.lower():
            return value
    return None


def reply_body(text='合成答复'):
    return json.dumps(
        {'choices': [{'message': {'content': text}}]}, ensure_ascii=False
    ).encode('utf-8')


class TempTest(unittest.TestCase):
    def setUp(self):
        temp_root = ROOT / 'qa-test-temp'
        temp_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=str(temp_root))
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / mc.MODEL_CONNECTION_FILENAME


class EndpointValidationTests(unittest.TestCase):
    def test_loopback_http_and_https_base_paths_are_accepted(self):
        cases = {
            'http://127.0.0.1:8000/v1': 'http://127.0.0.1:8000/v1',
            'http://127.0.0.1:8000/v1/': 'http://127.0.0.1:8000/v1',
            'http://127.0.0.1:8000': 'http://127.0.0.1:8000',
            'http://localhost:1234/v1': 'http://localhost:1234/v1',
            'http://127.2.3.4:8000/api/v1': 'http://127.2.3.4:8000/api/v1',
            'http://[::1]:1234/v1': 'http://[::1]:1234/v1',
            'https://models.example.com/v1': 'https://models.example.com/v1',
            'https://models.example.com/openai/v1/':
                'https://models.example.com/openai/v1',
            '  https://models.example.com/v1  ':
                'https://models.example.com/v1',
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(mc.validate_endpoint(raw), expected)

    def test_remote_http_and_unsafe_url_parts_are_rejected(self):
        bad = [
            'http://models.example.com/v1',
            'http://192.168.1.5/v1',
            'http://127.0.0.1.evil/v1',
            'https://user:pass@models.example.com/v1',
            'https://models.example.com/v1?x=1',
            'https://models.example.com/v1#frag',
            'https://models.example.com/v1\n',
            'https://models.example.com/v1\x00',
            'ftp://models.example.com/v1',
            'https://models.example.com//v1',
            'https://models.example.com/v1/../admin',
            'https://models.example.com/%2e%2e/admin',
            'https://models.example.com\\v1',
            'https://models.example.com/v1;param',
            'https://models.example.com:70000/v1',
            'https://models.example.com:abc/v1',
            'https://' + ('a' * 3000) + '.example/v1',
            '',
            None,
        ]
        for raw in bad:
            with self.subTest(raw=raw):
                with self.assertRaises(mc.ModelConnectionError):
                    mc.validate_endpoint(raw)

    def test_normalised_school_endpoint_matches_the_documented_default(self):
        self.assertEqual(
            mc.validate_endpoint(mc.SCHOOL_ENDPOINT), mc.SCHOOL_ENDPOINT
        )


class ModelValidationTests(unittest.TestCase):
    def test_domestic_families_are_case_insensitive_and_spelling_is_kept(self):
        names = [
            'DeepSeek-V4.1-Flash', 'GLM-5.3-Flash', 'deepseek-flash',
            'deepseek-v4-pro', 'DeepSeek-Flash', 'Qwen2.5-72B-Instruct',
            'qwen2.5-7b', 'glm-4.6', 'kimi-k2', 'moonshot-v1-8k',
            'internlm2.5-7b', 'MiniCPM-V-2.6',
        ]
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(mc.validate_model(name), name)
        self.assertEqual(
            mc.validate_model('  deepseek-flash  '), 'deepseek-flash'
        )

    def test_gpt_openai_branding_is_rejected_even_hidden_inside_a_name(self):
        bad = [
            'gpt-4o', 'GPT-4O', 'chatgpt', 'ChatGPT-4', 'openai/gpt-4',
            'deepseek-gpt4o', 'deepseekv4gpt', 'deepseek-gpt',
            'qwen-openai', 'glm-chatgpt', 'my-openai-deepseek',
        ]
        for name in bad:
            with self.subTest(name=name):
                with self.assertRaises(mc.ModelConnectionError):
                    mc.validate_model(name)

    def test_non_domestic_or_malformed_names_are_rejected(self):
        bad = ['', '   ', 'llama-3.1', 'mistral-small', 'deepseek\nflash',
               'deepseek-' + ('a' * 250)]
        for name in bad:
            with self.subTest(name=name):
                with self.assertRaises(mc.ModelConnectionError):
                    mc.validate_model(name)


class ApiKeyAndSnapshotTests(unittest.TestCase):
    def test_key_validation_and_repr_exclusion(self):
        self.assertEqual(mc.validate_api_key(''), '')
        self.assertEqual(mc.validate_api_key(SECRET), SECRET)
        self.assertEqual(mc.validate_api_key('with space'), 'with space')
        for bad in ('line\nbreak', 'carriage\rreturn', 'null\x00key',
                    'x' * 513, '密钥'):
            with self.subTest(bad=bad):
                with self.assertRaises(mc.ModelConnectionError):
                    mc.validate_api_key(bad)
        snapshot = mc.prepare_snapshot(
            mc.CUSTOM_PROVIDER, ACTIVE_ENDPOINT, ACTIVE_MODEL, SECRET
        )
        self.assertNotIn(SECRET, repr(snapshot))
        self.assertNotIn(SECRET, str(snapshot))
        payload = mc.snapshot_payload(snapshot)
        self.assertEqual(
            set(payload), {'schema', 'provider', 'endpoint', 'model'}
        )
        self.assertNotIn(SECRET, json.dumps(payload))

    def test_school_preset_is_exact_and_never_silently_substituted(self):
        snapshot = mc.prepare_snapshot(
            mc.SCHOOL_PROVIDER, mc.SCHOOL_ENDPOINT, mc.SCHOOL_MODEL,
            mc.SCHOOL_API_KEY,
        )
        self.assertEqual(snapshot.endpoint, mc.SCHOOL_ENDPOINT)
        self.assertEqual(snapshot.model, mc.SCHOOL_MODEL)
        with self.assertRaises(mc.ModelConnectionError) as caught:
            mc.prepare_snapshot(
                mc.SCHOOL_PROVIDER, mc.SCHOOL_ENDPOINT, 'deepseek-flash',
                mc.SCHOOL_API_KEY,
            )
        self.assertEqual(
            mc.fixed_message(caught.exception, ''),
            mc.ERROR_SCHOOL_PRESET_TEXT,
        )
        with self.assertRaises(mc.ModelConnectionError):
            mc.prepare_snapshot(
                mc.SCHOOL_PROVIDER, mc.SCHOOL_ENDPOINT, mc.SCHOOL_MODEL, ''
            )

    def test_provider_scheme_boundaries(self):
        with self.assertRaises(mc.ModelConnectionError):
            mc.prepare_snapshot(
                mc.LOCAL_PROVIDER, ACTIVE_ENDPOINT, 'deepseek-flash', ''
            )
        with self.assertRaises(mc.ModelConnectionError):
            mc.prepare_snapshot(
                mc.CUSTOM_PROVIDER, mc.SCHOOL_ENDPOINT, 'deepseek-flash', ''
            )
        local = mc.prepare_snapshot(
            mc.LOCAL_PROVIDER, mc.LOCAL_DEFAULT_ENDPOINT,
            mc.LOCAL_DEFAULT_MODEL, '',
        )
        self.assertEqual(local.provider, mc.LOCAL_PROVIDER)
        custom = mc.prepare_snapshot(
            mc.CUSTOM_PROVIDER, ACTIVE_ENDPOINT, ACTIVE_MODEL, SECRET
        )
        self.assertEqual(custom.provider, mc.CUSTOM_PROVIDER)
        with self.assertRaises(mc.ModelConnectionError):
            mc.prepare_snapshot('unknown', ACTIVE_ENDPOINT, ACTIVE_MODEL, '')


class PersistenceTests(TempTest):
    def test_missing_file_defaults_to_school_without_creating_anything(self):
        nested = Path(self.temp.name) / 'missing' / mc.MODEL_CONNECTION_FILENAME
        connection = mc.ModelConnection.load(nested)
        snapshot = connection.current()
        self.assertEqual(snapshot.provider, mc.SCHOOL_PROVIDER)
        self.assertEqual(snapshot.endpoint, mc.SCHOOL_ENDPOINT)
        self.assertEqual(snapshot.model, mc.SCHOOL_MODEL)
        self.assertEqual(snapshot.api_key, mc.SCHOOL_API_KEY)
        self.assertIsNone(connection.load_error)
        self.assertFalse(nested.parent.exists())

    def test_apply_persists_only_provider_endpoint_and_model(self):
        connection = mc.ModelConnection.load(self.path)
        connection.apply(
            mc.SCHOOL_PROVIDER, mc.SCHOOL_ENDPOINT, mc.SCHOOL_MODEL, SECRET
        )
        raw = self.path.read_text(encoding='utf-8')
        payload = json.loads(raw)
        self.assertEqual(
            set(payload), {'schema', 'provider', 'endpoint', 'model'}
        )
        self.assertEqual(payload['provider'], mc.SCHOOL_PROVIDER)
        self.assertNotIn(SECRET, raw)
        self.assertNotIn(mc.SCHOOL_API_KEY, raw)
        self.assertEqual(connection.current().api_key, SECRET)

        reloaded = mc.ModelConnection.load(self.path)
        self.assertEqual(reloaded.current().provider, mc.SCHOOL_PROVIDER)
        self.assertEqual(reloaded.current().api_key, mc.SCHOOL_API_KEY)

    def test_custom_key_is_memory_only_and_absent_after_reload(self):
        connection = mc.ModelConnection.load(self.path)
        connection.apply(
            mc.CUSTOM_PROVIDER, ACTIVE_ENDPOINT, ACTIVE_MODEL, SECRET
        )
        self.assertEqual(connection.current().api_key, SECRET)
        self.assertNotIn(SECRET, self.path.read_text(encoding='utf-8'))
        reloaded = mc.ModelConnection.load(self.path)
        self.assertEqual(reloaded.current().endpoint, ACTIVE_ENDPOINT)
        self.assertEqual(reloaded.current().model, ACTIVE_MODEL)
        self.assertEqual(reloaded.current().api_key, '')

    def test_external_change_after_load_refuses_the_save(self):
        connection = mc.ModelConnection.load(self.path)
        connection.apply(
            mc.CUSTOM_PROVIDER, ACTIVE_ENDPOINT, ACTIVE_MODEL, SECRET
        )
        external = json.dumps(
            {
                'schema': 1,
                'provider': mc.LOCAL_PROVIDER,
                'endpoint': mc.LOCAL_DEFAULT_ENDPOINT,
                'model': 'qwen2.5-7b',
            },
            ensure_ascii=False, separators=(',', ':'),
        ).encode('utf-8')
        self.path.write_bytes(external)
        with self.assertRaises(mc.ModelConnectionError) as caught:
            connection.apply(
                mc.CUSTOM_PROVIDER, 'https://other.example/v1',
                'deepseek-v4-pro', '',
            )
        self.assertEqual(
            mc.fixed_message(caught.exception, ''),
            mc.ERROR_CONFIG_STALE_TEXT,
        )
        self.assertEqual(self.path.read_bytes(), external)
        self.assertEqual(connection.current().endpoint, ACTIVE_ENDPOINT)

    def test_file_appearing_after_a_missing_load_is_not_overwritten(self):
        connection = mc.ModelConnection.load(self.path)
        external = b'{"schema":1,"provider":"local","endpoint":' \
                   b'"http://localhost:1234/v1","model":"deepseek-flash"}'
        self.path.write_bytes(external)
        with self.assertRaises(mc.ModelConnectionError) as caught:
            connection.apply(
                mc.CUSTOM_PROVIDER, ACTIVE_ENDPOINT, ACTIVE_MODEL, ''
            )
        self.assertEqual(
            mc.fixed_message(caught.exception, ''),
            mc.ERROR_CONFIG_STALE_TEXT,
        )
        self.assertEqual(self.path.read_bytes(), external)

    def test_corrupt_file_fails_closed_and_never_calls_the_model(self):
        original = b'{"schema":1,'
        self.path.write_bytes(original)
        calls = []

        def fake(*args):
            calls.append(args)
            return '不应调用'

        connection = mc.ModelConnection.load(self.path, request_fn=fake)
        self.assertIsNone(connection.current())
        self.assertEqual(connection.load_error, 'corrupt')
        self.assertEqual(self.path.read_bytes(), original)
        with self.assertRaises(mc.ModelConnectionError) as caught:
            connection(
                mc.SCHOOL_ENDPOINT, mc.SCHOOL_MODEL, MESSAGES,
                mc.SCHOOL_API_KEY,
            )
        self.assertEqual(
            mc.fixed_message(caught.exception, ''),
            mc.ERROR_CONFIG_UNAVAILABLE_TEXT,
        )
        self.assertEqual(calls, [])

    def test_explicit_apply_replaces_unchanged_corrupt_bytes(self):
        self.path.write_bytes(b'not-json')
        connection = mc.ModelConnection.load(self.path)
        connection.apply(
            mc.CUSTOM_PROVIDER, ACTIVE_ENDPOINT, ACTIVE_MODEL, SECRET
        )
        self.assertEqual(connection.current().provider, mc.CUSTOM_PROVIDER)
        self.assertNotIn(SECRET, self.path.read_text(encoding='utf-8'))

    def test_changed_corrupt_bytes_are_never_overwritten(self):
        self.path.write_bytes(b'not-json')
        connection = mc.ModelConnection.load(self.path)
        self.path.write_bytes(b'changed-by-another-program')
        with self.assertRaises(mc.ModelConnectionError) as caught:
            connection.apply(
                mc.CUSTOM_PROVIDER, ACTIVE_ENDPOINT, ACTIVE_MODEL, ''
            )
        self.assertEqual(
            mc.fixed_message(caught.exception, ''),
            mc.ERROR_CONFIG_STALE_TEXT,
        )
        self.assertEqual(
            self.path.read_bytes(), b'changed-by-another-program'
        )
        self.assertIsNone(connection.current())

    def test_schema_extra_key_and_preset_mismatch_fail_closed(self):
        variants = [
            {
                'schema': 1, 'provider': mc.SCHOOL_PROVIDER,
                'endpoint': mc.SCHOOL_ENDPOINT, 'model': mc.SCHOOL_MODEL,
                'api_key': SECRET,
            },
            {
                'schema': 2, 'provider': mc.SCHOOL_PROVIDER,
                'endpoint': mc.SCHOOL_ENDPOINT, 'model': mc.SCHOOL_MODEL,
            },
            {
                'schema': 1, 'provider': 'unknown',
                'endpoint': ACTIVE_ENDPOINT, 'model': ACTIVE_MODEL,
            },
            {
                'schema': 1, 'provider': mc.CUSTOM_PROVIDER,
                'endpoint': mc.SCHOOL_ENDPOINT, 'model': mc.SCHOOL_MODEL,
            },
            {
                'schema': 1, 'provider': mc.LOCAL_PROVIDER,
                'endpoint': ACTIVE_ENDPOINT, 'model': 'deepseek-flash',
            },
            {
                'schema': 1, 'provider': mc.SCHOOL_PROVIDER,
                'endpoint': mc.SCHOOL_ENDPOINT, 'model': 'deepseek-flash',
            },
        ]
        for payload in variants:
            with self.subTest(payload=payload):
                raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
                self.path.write_bytes(raw)
                connection = mc.ModelConnection.load(self.path)
                self.assertIsNone(connection.current())
                self.assertEqual(connection.load_error, 'corrupt')
                self.assertEqual(self.path.read_bytes(), raw)

    def test_unreadable_file_fails_closed_and_refuses_apply(self):
        valid = json.dumps({
            'schema': 1, 'provider': mc.LOCAL_PROVIDER,
            'endpoint': mc.LOCAL_DEFAULT_ENDPOINT, 'model': 'deepseek-flash',
        }).encode('utf-8')
        self.path.write_bytes(valid)
        with mock.patch.object(
                mc, '_read_config_bytes',
                side_effect=OSError('synthetic permission failure')):
            connection = mc.ModelConnection.load(self.path)
        self.assertIsNone(connection.current())
        self.assertEqual(connection.load_error, 'unreadable')
        with self.assertRaises(mc.ModelConnectionError) as caught:
            connection.apply(
                mc.LOCAL_PROVIDER, mc.LOCAL_DEFAULT_ENDPOINT,
                'deepseek-flash', '',
            )
        self.assertEqual(
            mc.fixed_message(caught.exception, ''),
            mc.ERROR_CONFIG_STALE_TEXT,
        )
        self.assertEqual(self.path.read_bytes(), valid)

    def test_save_failure_does_not_activate_or_retain_the_new_key(self):
        connection = mc.ModelConnection.load(self.path)
        previous = connection.current()
        with mock.patch.object(
                mc, '_atomic_write_json',
                side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(mc.ModelConnectionError) as caught:
                connection.apply(
                    mc.CUSTOM_PROVIDER, ACTIVE_ENDPOINT, ACTIVE_MODEL, SECRET
                )
        self.assertEqual(
            mc.fixed_message(caught.exception, ''),
            mc.ERROR_CONFIG_SAVE_TEXT,
        )
        self.assertIs(connection.current(), previous)
        self.assertNotIn(SECRET, repr(connection.current()))
        self.assertFalse(self.path.exists())


class OpenerTests(unittest.TestCase):
    def test_http_opener_creates_no_tls_context_and_disables_proxy(self):
        called = []

        def factory():
            called.append(True)
            raise AssertionError('TLS must not be initialised for HTTP')

        opener = mc.build_opener_for('http', tls_context_factory=factory)
        self.assertEqual(called, [])
        self.assertFalse(any(
            isinstance(handler, urllib.request.HTTPSHandler)
            for handler in opener.handlers
        ))
        class LocalHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'synthetic-direct-response')

        # Empty ProxyHandler has no handler methods, so urllib omits it from
        # the handler list. Verify the actual network behavior instead.
        server = ThreadingHTTPServer(('127.0.0.1', 0), LocalHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with mock.patch.dict('os.environ', {
                'http_proxy': 'http://127.0.0.1:9',
                'HTTP_PROXY': 'http://127.0.0.1:9',
                'no_proxy': '', 'NO_PROXY': '',
            }):
                with opener.open(f'http://127.0.0.1:{server.server_port}/probe', timeout=2) as response:
                    self.assertEqual(response.read(), b'synthetic-direct-response')
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)
        self.assertTrue(any(
            isinstance(handler, mc.NoRedirect)
            for handler in opener.handlers
        ))

    def test_https_opener_creates_one_verified_context(self):
        created = []

        def factory():
            context = ssl.create_default_context()
            created.append(context)
            return context

        opener = mc.build_opener_for('https', tls_context_factory=factory)
        self.assertEqual(len(created), 1)
        self.assertTrue(any(
            isinstance(handler, urllib.request.HTTPSHandler)
            for handler in opener.handlers
        ))
        self.assertEqual(created[0].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(created[0].check_hostname)

    def test_tls_unavailable_is_a_clear_fixed_error(self):
        def factory():
            raise RuntimeError('synthetic TLS initialisation failure')

        with self.assertRaises(mc.ModelConnectionError) as caught:
            mc.build_opener_for('https', tls_context_factory=factory)
        self.assertEqual(
            mc.fixed_message(caught.exception, ''),
            mc.ERROR_TLS_UNAVAILABLE_TEXT,
        )
        with self.assertRaises(mc.ModelConnectionError):
            mc.build_opener_for('ftp')

    def test_redirect_handler_refuses_instead_of_following(self):
        handler = mc.NoRedirect()
        with self.assertRaises(mc.ModelConnectionError) as caught:
            handler.redirect_request(
                None, None, 302, 'Found', {}, 'https://elsewhere.example/'
            )
        self.assertEqual(
            mc.fixed_message(caught.exception, ''),
            mc.ERROR_REDIRECT_TEXT,
        )


class RequestTests(TempTest):
    def make_connection(self, calls=None):
        def fake(endpoint, model, messages, api_key):
            if calls is not None:
                calls.append((endpoint, model, api_key))
            return '合成答复'

        return mc.ModelConnection(
            self.path,
            mc.prepare_snapshot(
                mc.CUSTOM_PROVIDER, ACTIVE_ENDPOINT, ACTIVE_MODEL, SECRET
            ),
            request_fn=fake,
        )

    def test_request_posts_to_the_selected_endpoint_once(self):
        opener = FakeOpener(reply_body('  合成答复  '))
        reply = mc.request_reply(
            ACTIVE_ENDPOINT + '/', 'DeepSeek-V4-Pro', MESSAGES, SECRET,
            opener=opener,
        )
        self.assertEqual(reply, '合成答复')
        self.assertEqual(len(opener.requests), 1)
        request, timeout = opener.requests[0]
        self.assertEqual(
            request.full_url,
            ACTIVE_ENDPOINT + '/chat/completions',
        )
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(
            header(request, 'Authorization'), 'Bearer ' + SECRET
        )
        self.assertEqual(timeout, mc.MODEL_REQUEST_TIMEOUT)
        body = json.loads(request.data.decode('utf-8'))
        self.assertEqual(body['model'], 'DeepSeek-V4-Pro')
        self.assertFalse(body['stream'])
        self.assertEqual(body['messages'], MESSAGES)

    def test_empty_key_sends_no_authorization_header(self):
        opener = FakeOpener(reply_body())
        mc.request_reply(
            mc.LOCAL_DEFAULT_ENDPOINT, 'deepseek-flash', MESSAGES, '',
            opener=opener,
        )
        request, _timeout = opener.requests[0]
        self.assertIsNone(header(request, 'Authorization'))

    def test_invalid_selection_is_rejected_before_any_request(self):
        cases = [
            ('http://models.example.com/v1', 'deepseek-flash', ''),
            (ACTIVE_ENDPOINT, 'gpt-4o', SECRET),
            (ACTIVE_ENDPOINT, ACTIVE_MODEL, 'bad\nkey'),
        ]
        for endpoint, model, key in cases:
            with self.subTest(model=model):
                opener = FakeOpener(reply_body())
                with self.assertRaises(mc.ModelConnectionError):
                    mc.request_reply(
                        endpoint, model, MESSAGES, key, opener=opener
                    )
                self.assertEqual(opener.requests, [])

    def test_auth_failure_is_one_fixed_category_without_leaks(self):
        error = urllib.error.HTTPError(
            ACTIVE_ENDPOINT, 401, 'body ' + SECRET, {'X': SECRET}, None
        )
        opener = FakeOpener(error=error)
        with self.assertRaises(mc.ModelConnectionError) as caught:
            mc.request_reply(
                ACTIVE_ENDPOINT, ACTIVE_MODEL, MESSAGES, SECRET,
                opener=opener,
            )
        message = str(caught.exception)
        self.assertEqual(mc.fixed_message(caught.exception, ''),
                         mc._LEGACY_AUTH_MESSAGE)
        self.assertNotIn(SECRET, message)
        self.assertNotIn(ACTIVE_ENDPOINT, message)
        self.assertEqual(len(opener.requests), 1)

    def test_generic_http_status_is_attempted_once(self):
        error = urllib.error.HTTPError(
            ACTIVE_ENDPOINT, 400, 'server body ' + SECRET, {}, None
        )
        opener = FakeOpener(error=error)
        with self.assertRaises(mc.ModelConnectionError) as caught:
            mc.request_reply(
                ACTIVE_ENDPOINT, ACTIVE_MODEL, MESSAGES, SECRET,
                opener=opener,
            )
        self.assertIn('400', str(caught.exception))
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertEqual(len(opener.requests), 1)

    def test_network_timeout_and_tls_failures_are_not_retried(self):
        cases = [
            (TimeoutError(), TimeoutError),
            (urllib.error.URLError(OSError('synthetic')), ConnectionError),
            (urllib.error.URLError(ssl.SSLError('certificate')), ConnectionError),
        ]
        for error, expected in cases:
            with self.subTest(error=type(error).__name__):
                opener = FakeOpener(error=error)
                with self.assertRaises(expected) as caught:
                    mc.request_reply(
                        ACTIVE_ENDPOINT, ACTIVE_MODEL, MESSAGES, SECRET,
                        opener=opener,
                    )
                self.assertEqual(len(opener.requests), 1)
                if isinstance(caught.exception, ConnectionError):
                    self.assertNotIn(SECRET, str(caught.exception))
                    self.assertNotIn(ACTIVE_ENDPOINT, str(caught.exception))

    def test_tls_failure_uses_a_fixed_message(self):
        opener = FakeOpener(
            error=urllib.error.URLError(ssl.SSLError('synthetic cert'))
        )
        with self.assertRaises(ConnectionError) as caught:
            mc.request_reply(
                ACTIVE_ENDPOINT, ACTIVE_MODEL, MESSAGES, SECRET,
                opener=opener,
            )
        self.assertEqual(str(caught.exception), mc.ERROR_TLS_FAILURE_TEXT)

    def test_invalid_and_oversized_bodies_use_fixed_categories(self):
        for raw, expected in [
            (b'{}', mc.ERROR_RESPONSE_TEXT),
            (b'x' * (mc.MAX_RESPONSE_BYTES + 1), mc.ERROR_TOO_LARGE_TEXT),
        ]:
            with self.subTest(size=len(raw)):
                opener = FakeOpener(raw)
                with self.assertRaises(mc.ModelConnectionError) as caught:
                    mc.request_reply(
                        ACTIVE_ENDPOINT, ACTIVE_MODEL, MESSAGES, SECRET,
                        opener=opener,
                    )
                self.assertEqual(
                    mc.fixed_message(caught.exception, ''), expected
                )
                self.assertEqual(len(opener.requests), 1)


class ConnectionTestTests(TempTest):
    def test_test_uses_only_get_models_and_never_chat(self):
        raw = json.dumps({
            'data': [
                {'id': 'deepseek-flash'},
                {'id': 'qwen2.5-7b'},
            ]
        }).encode('utf-8')
        opener = FakeOpener(raw)
        ok, message = mc.test_connection(
            mc.CUSTOM_PROVIDER, ACTIVE_ENDPOINT, ACTIVE_MODEL, SECRET,
            opener=opener,
        )
        self.assertTrue(ok)
        self.assertEqual(
            message, mc.TEST_SUCCESS_TEXT[mc.CUSTOM_PROVIDER]
        )
        self.assertIn('模型列表', message)
        self.assertIn('未发送聊天内容', message)
        self.assertNotIn('模型调用成功', message)
        self.assertNotIn('deepseek-flash', message)
        self.assertNotIn(ACTIVE_ENDPOINT, message)
        self.assertNotIn(SECRET, message)
        self.assertEqual(len(opener.requests), 1)
        request, timeout = opener.requests[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(request.full_url, ACTIVE_ENDPOINT + '/models')
        self.assertIsNone(request.data)
        self.assertEqual(
            header(request, 'Authorization'), 'Bearer ' + SECRET
        )
        self.assertEqual(timeout, mc.MODEL_TEST_TIMEOUT)

    def test_school_health_distinguishes_expired_login_from_absence(self):
        login_error = urllib.error.HTTPError(
            mc.SCHOOL_ENDPOINT + '/models', 401, 'body', {}, None
        )
        ok, login_message = mc.test_connection(
            mc.SCHOOL_PROVIDER, mc.SCHOOL_ENDPOINT, mc.SCHOOL_MODEL,
            mc.SCHOOL_API_KEY, opener=FakeOpener(error=login_error),
        )
        self.assertFalse(ok)
        self.assertEqual(login_message, mc.ERROR_SCHOOL_AUTH_TEXT)
        self.assertIn('登录', login_message)

        absent = FakeOpener(error=urllib.error.URLError(ConnectionRefusedError()))
        ok, absent_message = mc.test_connection(
            mc.SCHOOL_PROVIDER, mc.SCHOOL_ENDPOINT, mc.SCHOOL_MODEL,
            mc.SCHOOL_API_KEY, opener=absent,
        )
        self.assertFalse(ok)
        self.assertEqual(absent_message, mc.ERROR_LOCAL_ABSENT_TEXT)
        self.assertIn('本机', absent_message)
        self.assertNotEqual(login_message, absent_message)

    def test_school_503_reports_login_or_proxy_unavailable(self):
        error = urllib.error.HTTPError(
            mc.SCHOOL_ENDPOINT + '/models', 503, 'body', {}, None
        )
        ok, message = mc.test_connection(
            mc.SCHOOL_PROVIDER, mc.SCHOOL_ENDPOINT, mc.SCHOOL_MODEL,
            mc.SCHOOL_API_KEY, opener=FakeOpener(error=error),
        )
        self.assertFalse(ok)
        self.assertEqual(message, mc.ERROR_SCHOOL_UNAVAILABLE_TEXT)
        self.assertIn('登录', message)

    def test_test_does_not_accept_an_unappliable_selection(self):
        opener = FakeOpener(b'{"data":[{"id":"deepseek-flash"}]}')
        ok, message = mc.test_connection(
            mc.CUSTOM_PROVIDER, mc.SCHOOL_ENDPOINT, 'deepseek-flash', '',
            opener=opener,
        )
        self.assertFalse(ok)
        self.assertEqual(message, mc.ERROR_CUSTOM_HTTPS_TEXT)
        self.assertEqual(opener.requests, [])

    def test_remote_and_redirect_and_format_categories_are_fixed(self):
        ok, message = mc.test_connection(
            mc.CUSTOM_PROVIDER, ACTIVE_ENDPOINT, ACTIVE_MODEL, SECRET,
            opener=FakeOpener(error=urllib.error.URLError(OSError('x'))),
        )
        self.assertFalse(ok)
        self.assertEqual(message, mc.ERROR_REMOTE_CONNECTION_TEXT)

        ok, message = mc.test_connection(
            mc.CUSTOM_PROVIDER, ACTIVE_ENDPOINT, ACTIVE_MODEL, SECRET,
            opener=FakeOpener(error=mc.ModelConnectionError(
                mc.ERROR_REDIRECT_TEXT
            )),
        )
        self.assertFalse(ok)
        self.assertEqual(message, mc.ERROR_REDIRECT_TEXT)

        for raw, expected in [
            (b'{}', mc.ERROR_MODELS_FORMAT_TEXT),
            (b'{"data":[]}', mc.ERROR_MODELS_EMPTY_TEXT),
        ]:
            ok, message = mc.test_connection(
                mc.CUSTOM_PROVIDER, ACTIVE_ENDPOINT, ACTIVE_MODEL, SECRET,
                opener=FakeOpener(raw),
            )
            self.assertFalse(ok)
            self.assertEqual(message, expected)


class SnapshotConsistencyTests(TempTest):
    def test_callable_ignores_legacy_core_arguments(self):
        calls = []

        def fake(endpoint, model, messages, api_key):
            calls.append((endpoint, model, messages, api_key))
            return '合成答复'

        snapshot = mc.prepare_snapshot(
            mc.CUSTOM_PROVIDER, ACTIVE_ENDPOINT, ACTIVE_MODEL, SECRET
        )
        connection = mc.ModelConnection(
            self.path, snapshot, request_fn=fake
        )
        result = connection(
            mc.SCHOOL_ENDPOINT, mc.SCHOOL_MODEL, MESSAGES,
            mc.SCHOOL_API_KEY,
        )
        self.assertEqual(result, '合成答复')
        self.assertEqual(
            calls,
            [(ACTIVE_ENDPOINT, ACTIVE_MODEL, MESSAGES, SECRET)],
        )
        self.assertNotIn(mc.SCHOOL_ENDPOINT, repr(calls))

    def test_in_flight_call_keeps_its_captured_snapshot(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def fake(endpoint, model, messages, api_key):
            calls.append((endpoint, model, api_key))
            if len(calls) == 1:
                entered.set()
                release.wait(3.0)
            return '合成答复'

        old = mc.prepare_snapshot(
            mc.LOCAL_PROVIDER, mc.LOCAL_DEFAULT_ENDPOINT,
            mc.LOCAL_DEFAULT_MODEL, '',
        )
        connection = mc.ModelConnection(
            self.path, old, request_fn=fake
        )
        worker = threading.Thread(
            target=connection,
            args=(mc.SCHOOL_ENDPOINT, mc.SCHOOL_MODEL, MESSAGES,
                  mc.SCHOOL_API_KEY),
        )
        worker.start()
        try:
            self.assertTrue(entered.wait(1.0))
            new = mc.prepare_snapshot(
                mc.CUSTOM_PROVIDER, ACTIVE_ENDPOINT, ACTIVE_MODEL, SECRET
            )
            connection.commit(new)
            self.assertEqual(connection(mc.SCHOOL_ENDPOINT,
                                        mc.SCHOOL_MODEL, MESSAGES), '合成答复')
            self.assertEqual(calls, [
                (old.endpoint, old.model, old.api_key),
                (new.endpoint, new.model, new.api_key),
            ])
        finally:
            release.set()
            worker.join(2.0)
        self.assertFalse(worker.is_alive())

    def test_fail_closed_connection_never_calls_the_underlying_requester(self):
        calls = []

        def fake(*args):
            calls.append(args)
            return '不应调用'

        connection = mc.ModelConnection(
            self.path, None, load_error='corrupt', request_fn=fake
        )
        with self.assertRaises(mc.ModelConnectionError):
            connection(ACTIVE_ENDPOINT, ACTIVE_MODEL, MESSAGES, SECRET)
        self.assertEqual(calls, [])


class SharedRequesterIntegrationTests(TempTest):
    def test_private_and_group_use_the_selected_requester_without_cross_context(self):
        calls = []

        def fake(endpoint, model, messages, api_key):
            calls.append({
                'endpoint': endpoint,
                'model': model,
                'api_key': api_key,
                'messages': [dict(item) for item in messages],
            })
            return '合成答复'

        snapshot = mc.prepare_snapshot(
            mc.CUSTOM_PROVIDER, ACTIVE_ENDPOINT, ACTIVE_MODEL, SECRET
        )
        connection = mc.ModelConnection(
            self.path, snapshot, request_fn=fake
        )
        data = Path(self.temp.name) / 'data'
        data.mkdir()

        private = QQCore(data, '100012345', request_reply=connection)
        private._set_profile('私聊偏好秘密')
        code = private.begin_pairing()
        self.assertIsNotNone(private.handle_private(
            '200054321', 'private-bind', '绑定 ' + code
        ))
        private_reply = private.handle_private(
            '200054321', 'private-chat', '私聊机密内容'
        )
        self.assertIsNotNone(private_reply)
        self.assertEqual(private_reply.kind, 'chat')

        group = GroupChat(data, '100012345', request_reply=connection)
        group_reply = group.handle_group(
            '300000001', '200054322', 'group-chat', '群聊公开内容'
        )
        self.assertIsNotNone(group_reply)
        self.assertEqual(group_reply.kind, 'group_chat')
        self.assertNotEqual(group_reply.text, GROUP_ERROR_TEXT)

        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertEqual(call['endpoint'], ACTIVE_ENDPOINT)
            self.assertEqual(call['model'], ACTIVE_MODEL)
            self.assertEqual(call['api_key'], SECRET)

        private_text = '\n'.join(
            str(item.get('content', ''))
            for item in calls[0]['messages']
        )
        group_text = '\n'.join(
            str(item.get('content', ''))
            for item in calls[1]['messages']
        )
        self.assertIn('私聊机密内容', private_text)
        self.assertIn('私聊偏好秘密', private_text)
        self.assertNotIn('私聊机密内容', group_text)
        self.assertNotIn('私聊偏好秘密', group_text)
        self.assertNotIn('群聊公开内容', private_text)
        self.assertIn('群聊公开内容', group_text)

        self.assertTrue((data / 'si_qq_core_state.json').is_file())
        self.assertTrue((data / 'si_qq_group_state.json').is_file())
        for path in data.glob('*.json'):
            raw = path.read_text(encoding='utf-8')
            self.assertNotIn(SECRET, raw)
            self.assertNotIn(ACTIVE_ENDPOINT, raw)


if __name__ == '__main__':
    unittest.main()
