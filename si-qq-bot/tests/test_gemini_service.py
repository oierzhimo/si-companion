"""Synthetic regression checks for the explicitly selected Vercel service.

No external endpoint, user credential, QQ account, or saved user data is used.
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

import model_connection as mc  # noqa: E402


SECRET = 'synthetic-gemini-service-key'
MESSAGES = [{'role': 'user', 'content': '合成测试输入'}]


class FakeResponse:
    def __init__(self, raw):
        self.raw = raw

    def read(self, limit=-1):
        return self.raw if limit < 0 else self.raw[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, text='合成测试答复'):
        self.raw = json.dumps({
            'choices': [{'message': {'content': text}}],
        }, ensure_ascii=False).encode('utf-8')
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        return FakeResponse(self.raw)


class GeminiServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'model_connection.json'

    def snapshot(self, key=SECRET):
        return mc.prepare_snapshot(
            mc.GEMINI_PROVIDER, mc.GEMINI_ENDPOINT, mc.GEMINI_MODEL, key,
        )

    def test_explicit_preset_has_exact_referenced_destination_and_model(self):
        self.assertEqual(mc.GEMINI_PROVIDER, 'gemini')
        self.assertEqual(
            mc.GEMINI_ENDPOINT,
            'https://openai-gemini-nu-two.vercel.app/v1',
        )
        self.assertEqual(mc.GEMINI_MODEL, 'gemini-3.6-flash')
        snapshot = self.snapshot()
        self.assertEqual(snapshot.provider, mc.GEMINI_PROVIDER)
        self.assertEqual(snapshot.endpoint, mc.GEMINI_ENDPOINT)
        self.assertEqual(snapshot.model, mc.GEMINI_MODEL)
        self.assertNotIn(SECRET, repr(snapshot))

    def test_gemini_preset_rejects_other_destinations_and_model_families(self):
        invalid = [
            ('https://different-service.example/v1', mc.GEMINI_MODEL),
            (mc.GEMINI_ENDPOINT + '/different', mc.GEMINI_MODEL),
            (mc.GEMINI_ENDPOINT, 'deepseek-flash'),
            (mc.GEMINI_ENDPOINT, 'gpt-4o'),
            (mc.GEMINI_ENDPOINT, 'gemini-gpt-4o'),
        ]
        for endpoint, model in invalid:
            with self.subTest(endpoint=endpoint, model=model):
                with self.assertRaises(mc.ModelConnectionError):
                    mc.prepare_snapshot(
                        mc.GEMINI_PROVIDER, endpoint, model, SECRET,
                    )

    def test_other_presets_do_not_silently_accept_gemini_or_gpt(self):
        for provider, endpoint in [
            (mc.LOCAL_PROVIDER, mc.LOCAL_DEFAULT_ENDPOINT),
            (mc.CUSTOM_PROVIDER, 'https://domestic.example/v1'),
            (mc.SCHOOL_PROVIDER, mc.SCHOOL_ENDPOINT),
        ]:
            for model in (mc.GEMINI_MODEL, 'gpt-4o'):
                with self.subTest(provider=provider, model=model):
                    with self.assertRaises(mc.ModelConnectionError):
                        mc.prepare_snapshot(provider, endpoint, model, SECRET)

    def test_saved_selection_keeps_gemini_but_never_keeps_the_key(self):
        connection = mc.ModelConnection.load(self.path)
        connection.apply(
            mc.GEMINI_PROVIDER, mc.GEMINI_ENDPOINT, mc.GEMINI_MODEL, SECRET,
        )
        raw = self.path.read_text(encoding='utf-8')
        payload = json.loads(raw)
        self.assertEqual(
            set(payload), {'schema', 'provider', 'endpoint', 'model'},
        )
        self.assertEqual(payload['provider'], mc.GEMINI_PROVIDER)
        self.assertEqual(payload['endpoint'], mc.GEMINI_ENDPOINT)
        self.assertEqual(payload['model'], mc.GEMINI_MODEL)
        self.assertNotIn(SECRET, raw)
        self.assertNotIn(mc.SCHOOL_API_KEY, raw)
        calls = []
        reloaded = mc.ModelConnection.load(
            self.path, request_fn=lambda *args, **kwargs: calls.append(args),
        )
        self.assertEqual(reloaded.current().provider, mc.GEMINI_PROVIDER)
        self.assertEqual(reloaded.current().endpoint, mc.GEMINI_ENDPOINT)
        self.assertEqual(reloaded.current().model, mc.GEMINI_MODEL)
        self.assertEqual(reloaded.current().api_key, '')
        with self.assertRaises(mc.ModelConnectionError):
            reloaded(mc.SCHOOL_ENDPOINT, mc.SCHOOL_MODEL, MESSAGES,
                     mc.SCHOOL_API_KEY)
        self.assertEqual(calls, [])

    def test_blank_gemini_key_blocks_before_the_underlying_request(self):
        fake = mock.Mock(return_value='不应调用')
        connection = mc.ModelConnection(
            self.path, self.snapshot(key=''), request_fn=fake,
        )
        with self.assertRaises(mc.ModelConnectionError):
            connection(mc.SCHOOL_ENDPOINT, mc.SCHOOL_MODEL, MESSAGES,
                       mc.SCHOOL_API_KEY)
        fake.assert_not_called()

    def test_real_requester_gets_provider_from_the_same_captured_snapshot(self):
        fake = mock.Mock(return_value='合成测试答复')
        with mock.patch.object(mc, 'request_reply', fake):
            connection = mc.ModelConnection(self.path, self.snapshot())
            result = connection(
                mc.SCHOOL_ENDPOINT, mc.SCHOOL_MODEL, MESSAGES,
                mc.SCHOOL_API_KEY,
            )
        self.assertEqual(result, '合成测试答复')
        fake.assert_called_once()
        self.assertEqual(fake.call_args.args, (
            mc.GEMINI_ENDPOINT, mc.GEMINI_MODEL, MESSAGES, SECRET,
        ))
        self.assertEqual(
            fake.call_args.kwargs.get('provider'), mc.GEMINI_PROVIDER,
        )

    def test_gemini_post_preserves_model_and_has_room_for_thinking_budget(self):
        opener = FakeOpener()
        result = mc.request_reply(
            mc.GEMINI_ENDPOINT, mc.GEMINI_MODEL, MESSAGES, SECRET,
            provider=mc.GEMINI_PROVIDER, opener=opener,
        )
        self.assertEqual(result, '合成测试答复')
        self.assertEqual(len(opener.requests), 1)
        request, _timeout = opener.requests[0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(
            request.full_url, mc.GEMINI_ENDPOINT + '/chat/completions',
        )
        payload = json.loads(request.data.decode('utf-8'))
        self.assertEqual(payload['model'], mc.GEMINI_MODEL)
        self.assertEqual(payload['messages'], MESSAGES)
        self.assertEqual(payload['max_tokens'], 4096)
        self.assertFalse(payload['stream'])

    def test_deeply_nested_small_config_fails_closed_and_can_be_repaired(self):
        original = (b'[' * 1500) + (b']' * 1500)
        self.assertLess(len(original), mc.MAX_CONFIG_BYTES)
        self.path.write_bytes(original)
        connection = mc.ModelConnection.load(self.path)
        self.assertIsNone(connection.current())
        self.assertEqual(connection.load_error, 'corrupt')
        self.assertEqual(self.path.read_bytes(), original)
        connection.apply(
            mc.GEMINI_PROVIDER, mc.GEMINI_ENDPOINT, mc.GEMINI_MODEL, SECRET,
        )
        self.assertEqual(connection.current().provider, mc.GEMINI_PROVIDER)
        self.assertNotIn(SECRET, self.path.read_text(encoding='utf-8'))


class ProbeFreshnessTests(unittest.TestCase):
    def test_pending_probe_disables_edits_or_discards_results_after_an_edit(self):
        from PySide6.QtWidgets import QApplication
        import main

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            connection = mc.ModelConnection.load(
                Path(directory) / 'model_connection.json',
            )
            dialog = main.ModelConnectionDialog(
                None, connection, lambda *args: (True, '合成应用成功'),
            )
            entered = threading.Event()
            release = threading.Event()
            finished = threading.Event()
            success = '合成旧接口测试成功'

            def blocking_test(*args):
                entered.set()
                release.wait(3)
                finished.set()
                return True, success

            dialog.show()
            app.processEvents()
            edited = False
            try:
                with mock.patch.object(main, 'test_connection', blocking_test):
                    dialog.test_button.click()
                    self.assertTrue(entered.wait(1))
                    fields = (
                        dialog.provider_combo, dialog.endpoint_edit,
                        dialog.model_edit, dialog.key_edit,
                    )
                    if any(field.isEnabled() for field in fields):
                        edited = True
                        dialog.model_edit.setText('deepseek-synthetic-new')
                    release.set()
                    self.assertTrue(finished.wait(1))
                    deadline = time.monotonic() + 1.5
                    while time.monotonic() < deadline:
                        app.processEvents()
                        if not dialog._test_running:
                            break
                        time.sleep(0.01)
                    self.assertFalse(dialog._test_running)
                    if edited:
                        self.assertNotEqual(dialog.status_label.text(), success)
            finally:
                release.set()
                dialog.close()
                dialog.deleteLater()
                app.processEvents()


if __name__ == '__main__':
    unittest.main()
