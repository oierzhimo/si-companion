"""Service-health and opt-in inference probes, using synthetic openers only."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import model_connection as mc
from test_model_connection import FakeOpener


class ServiceProbeTests(unittest.TestCase):
    def test_school_health_checks_upstream_auth_not_just_200(self):
        cases = [
            ({'status': 'ok', 'upstream_auth': 'valid'}, True),
            ({'status': 'degraded', 'upstream_auth': 'expired'}, False),
            ({'status': 'degraded', 'upstream_auth': 'missing'}, False),
            ({'data': [{'id': mc.SCHOOL_MODEL}]}, False),
        ]
        for body, expected in cases:
            with self.subTest(body=body):
                opener = FakeOpener(json.dumps(body).encode())
                ok, message = mc.test_connection(
                    mc.SCHOOL_PROVIDER, mc.SCHOOL_ENDPOINT, mc.SCHOOL_MODEL,
                    mc.SCHOOL_API_KEY, opener=opener)
                self.assertEqual(ok, expected)
                self.assertEqual(len(opener.requests), 1)
                request, _ = opener.requests[0]
                self.assertEqual(request.full_url, 'http://127.0.0.1:8000/health')
                self.assertEqual(request.get_method(), 'GET')
                self.assertIsNone(request.data)
                self.assertIsNone(request.get_header('Authorization'))
                if expected:
                    self.assertIn('尚未验证实际模型回复', message)

    def test_reply_probe_sends_only_declared_synthetic_text(self):
        with mock.patch.object(mc, 'request_reply', return_value='连接成功') as request:
            ok, message = mc.test_reply(mc.GEMINI_PROVIDER, mc.GEMINI_ENDPOINT,
                                        mc.GEMINI_MODEL, 'synthetic-key')
        self.assertTrue(ok)
        request.assert_called_once_with(mc.GEMINI_ENDPOINT, mc.GEMINI_MODEL,
            [{'role': 'user', 'content': '请只回复：连接成功'}], 'synthetic-key',
            provider=mc.GEMINI_PROVIDER)
        self.assertNotIn('synthetic-key', message)

    def test_reply_probe_hides_arbitrary_errors_and_has_no_fallback(self):
        with mock.patch.object(mc, 'request_reply', side_effect=mc.ModelConnectionError('private synthetic server body')) as request:
            ok, message = mc.test_reply(mc.GEMINI_PROVIDER, mc.GEMINI_ENDPOINT,
                                        mc.GEMINI_MODEL, 'synthetic-key')
        self.assertFalse(ok)
        self.assertEqual(message, mc.ERROR_HTTP_TEXT)
        request.assert_called_once()

    def test_gemini_interface_probe_without_key_never_opens_a_connection(self):
        opener = FakeOpener(b'{"data":[{"id":"gemini-3.6-flash"}]}')
        ok, message = mc.test_connection(mc.GEMINI_PROVIDER, mc.GEMINI_ENDPOINT,
                                        mc.GEMINI_MODEL, '', opener=opener)
        self.assertFalse(ok)
        self.assertEqual(message, mc.ERROR_GEMINI_KEY_TEXT)
        self.assertEqual(opener.requests, [])

    def test_gemini_ui_preset_clears_school_key_and_reply_probe_does_not_apply(self):
        from PySide6.QtWidgets import QApplication
        import main
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            connection = mc.ModelConnection.load(Path(directory) / 'model_connection.json')
            apply = mock.Mock()
            dialog = main.ModelConnectionDialog(None, connection, apply)
            dialog.provider_combo.setCurrentIndex(dialog.provider_combo.findData(mc.GEMINI_PROVIDER))
            self.assertEqual(dialog.endpoint_edit.text(), mc.GEMINI_ENDPOINT)
            self.assertEqual(dialog.model_edit.text(), mc.GEMINI_MODEL)
            self.assertEqual(dialog.key_edit.text(), '')
            try:
                with mock.patch.object(main, 'test_reply', return_value=(True, '合成测试完成')) as probe:
                    dialog.reply_test_button.click()
                    import time
                    deadline = time.monotonic() + 2
                    while dialog._test_running and time.monotonic() < deadline:
                        app.processEvents()
                        time.sleep(.01)
                    self.assertFalse(dialog._test_running)
                    probe.assert_called_once_with(mc.GEMINI_PROVIDER, mc.GEMINI_ENDPOINT, mc.GEMINI_MODEL, '')
                    self.assertEqual(dialog.status_label.text(), '合成测试完成')
                    apply.assert_not_called()
                    self.assertEqual(connection.current().provider, mc.SCHOOL_PROVIDER)
            finally:
                dialog.close()
                dialog.deleteLater()
                app.processEvents()


if __name__ == '__main__':
    unittest.main()
