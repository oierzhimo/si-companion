import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from si_pet.conversation import (
    CUSTOM_DEFAULT_ENDPOINT,
    Conversation,
    ConversationError,
    DEEPSEEK_DEFAULT_ENDPOINT,
    DEEPSEEK_DEFAULT_MODEL,
    LAN_DEFAULT_ENDPOINT,
    LAN_DEFAULT_MODEL,
    REMOTE_DEFAULT_ENDPOINT,
    REMOTE_DEFAULT_MODEL,
    custom_endpoint,
    deepseek_endpoint,
    endpoint_for_provider,
    fetch_models,
    lan_endpoint,
    local_endpoint,
    remote_endpoint,
    request_reply,
)

# One QApplication for the whole test process.  Qt does not support destroying
# the application object and creating a second one later in the same process;
# the old test dropped its local reference, so the later lifecycle tests built a
# second QApplication on top of torn-down platform state.
_QT_APP = None


def qt_application():
    global _QT_APP
    if _QT_APP is None:
        from PySide6.QtWidgets import QApplication
        _QT_APP = QApplication.instance() or QApplication([])
    return _QT_APP


class ConversationTests(unittest.TestCase):
    def test_loopback_only_and_no_embedded_secrets(self):
        self.assertEqual(local_endpoint('http://127.0.0.1:1234'), 'http://127.0.0.1:1234/v1')
        for endpoint in ('https://example.com:443', 'http://192.168.1.2:8000/v1',
                         'http://user:secret@127.0.0.1:1234', 'http://127.0.0.1:1234/v1?key=x'):
            with self.assertRaises(ConversationError): local_endpoint(endpoint)

    def test_roundtrip_context_bounded_and_failed_save_rolls_back(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'conversation.json'
            session = Conversation(path)
            session.profile = '喜欢画画，不希望频繁打断'
            for i in range(15): session.commit(f'问题{i}', f'答复{i}')
            restored = Conversation(path)
            self.assertEqual(len(restored.turns), 12)
            messages = restored.messages('继续这件事')
            self.assertEqual(messages[1]['role'], 'user')
            self.assertIn('问题3', [m['content'] for m in messages])
            self.assertNotIn('问题2', [m['content'] for m in messages])
            before = restored.turns[:]
            with patch.object(restored, 'save', side_effect=ConversationError('disk')):
                with self.assertRaises(ConversationError): restored.commit('新问题', '新答复')
            self.assertEqual(restored.turns, before)
            path.write_text('{broken', encoding='utf-8')
            with self.assertRaises(ConversationError): Conversation(path)
            self.assertEqual(path.read_text(), '{broken')

    def test_real_http_payload_and_redirect_rejected(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                if len(received) == 1:
                    body = json.dumps({'choices': [{'message': {'content': '管理员，慢慢来。'}}]}, ensure_ascii=False).encode()
                    self.send_response(200); self.end_headers(); self.wfile.write(body)
                else:
                    self.send_response(302); self.send_header('Location', 'http://example.com/'); self.end_headers()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            endpoint = f'http://127.0.0.1:{server.server_port}/v1'
            messages = [{'role': 'user', 'content': '我在画画'}]
            self.assertEqual(request_reply(endpoint, 'test-model', messages), '管理员，慢慢来。')
            self.assertEqual(received[0]['messages'], messages)
            self.assertFalse(received[0]['stream'])
            with self.assertRaises(ConversationError): request_reply(endpoint, 'test-model', messages)
        finally: server.shutdown(); server.server_close()

    def test_qt_late_reply_clear_and_failure_keep_input(self):
        from si_pet.conversation_ui import ConversationDialog
        app = qt_application()
        class Controller:
            def __init__(self): self.spoken = []
            def say(self, text, force): self.spoken.append(text)
        with tempfile.TemporaryDirectory() as folder:
            ctrl = Controller()
            dialog = ConversationDialog(ctrl, Path(folder) / 'conversation.json')
            dialog.input.setPlainText('保留这句话')
            dialog.busy = True
            dialog.active_generation = dialog.generation
            dialog.pending_text = '保留这句话'
            dialog.finished_reply(0, '', '连接失败')
            self.assertEqual(dialog.input.toPlainText(), '保留这句话')
            self.assertFalse(dialog.busy)
            dialog.clear_turns()
            dialog.finished_reply(0, '过期回答', '')
            self.assertEqual(dialog.session.turns, [])
            self.assertEqual(ctrl.spoken, [])
            # Destroy the widget now, on the main thread, instead of leaving it
            # to a later cyclic-GC pass while other tests are running.
            import shiboken6
            try: dialog.completed.disconnect()
            except (RuntimeError, TypeError): pass
            dialog.close()
            try: shiboken6.delete(dialog)
            except RuntimeError: pass
            app.processEvents()

    def test_qt_unexpected_reply_error_does_not_escape_the_slot(self):
        from si_pet.conversation_ui import ConversationDialog
        app = qt_application()
        class Controller:
            def __init__(self): self.spoken = []
            def say(self, text, force): self.spoken.append(text)
        with tempfile.TemporaryDirectory() as folder:
            ctrl = Controller()
            dialog = ConversationDialog(ctrl, Path(folder) / 'conversation.json')
            dialog.input.setPlainText('异常时也要留住这句话')
            dialog.busy = True
            dialog.active_generation = dialog.generation
            dialog.pending_text = '异常时也要留住这句话'
            dialog.send.setEnabled(False)
            with patch.object(dialog.session, 'commit', side_effect=RuntimeError('unexpected')):
                try:
                    dialog.finished_reply(dialog.generation, '回答', '')
                except Exception as exc:  # an escape here is the native-abort class
                    self.fail(f'a slot exception escaped into Qt: {exc!r}')
            self.assertFalse(dialog.busy)
            self.assertTrue(dialog.send.isEnabled())
            self.assertEqual(dialog.input.toPlainText(), '异常时也要留住这句话')
            self.assertEqual(dialog.session.turns, [])
            self.assertEqual(ctrl.spoken, [])
            self.assertIn('对话暂时未能完成', dialog.status.text())
            import shiboken6
            try: dialog.completed.disconnect()
            except (RuntimeError, TypeError): pass
            dialog.close()
            try: shiboken6.delete(dialog)
            except RuntimeError: pass
            app.processEvents()

    def test_deepseek_endpoint_validation_and_session_roundtrip(self):
        self.assertEqual(deepseek_endpoint('https://api.deepseek.com'), DEEPSEEK_DEFAULT_ENDPOINT)
        self.assertEqual(deepseek_endpoint('https://API.DEEPSEEK.COM/v1/'), DEEPSEEK_DEFAULT_ENDPOINT)
        self.assertEqual(
            endpoint_for_provider('deepseek', 'https://api.deepseek.com/v1'),
            DEEPSEEK_DEFAULT_ENDPOINT,
        )
        for bad in (
            'http://api.deepseek.com/v1',
            'https://evil.example.com/v1',
            'https://127.0.0.1:443/v1',
            'https://user:pass@api.deepseek.com/v1',
            'https://api.deepseek.com/v1?x=1',
        ):
            with self.assertRaises(ConversationError):
                deepseek_endpoint(bad)

        with self.assertRaises(ConversationError):
            request_reply(DEEPSEEK_DEFAULT_ENDPOINT, DEEPSEEK_DEFAULT_MODEL, [{'role': 'user', 'content': 'hi'}], api_key='', provider='deepseek')
        with self.assertRaises(ConversationError):
            fetch_models(DEEPSEEK_DEFAULT_ENDPOINT, api_key='', provider='deepseek')

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'conversation.json'
            session = Conversation(path)
            session.provider = 'deepseek'
            session.endpoint = DEEPSEEK_DEFAULT_ENDPOINT
            session.model = DEEPSEEK_DEFAULT_MODEL
            session.commit('你好', '管理员，我在。')
            raw_saved = json.loads(path.read_text(encoding='utf-8'))
            self.assertNotIn('api_key', raw_saved)

            reloaded = Conversation(path)
            self.assertEqual(reloaded.provider, 'deepseek')
            self.assertEqual(reloaded.endpoint, DEEPSEEK_DEFAULT_ENDPOINT)
            self.assertEqual(reloaded.model, DEEPSEEK_DEFAULT_MODEL)

    def test_deepseek_request_reply_and_fetch_models_use_remote_tls_opener(self):
        import io
        class FakeResp:
            def __init__(self, payload):
                self._data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            def read(self, n=-1):
                return self._data[:n] if n >= 0 else self._data
            def __enter__(self): return self
            def __exit__(self, *args): return False

        captured_reqs = []
        class FakeOpener:
            def open(self, req, timeout=0):
                captured_reqs.append((req.full_url, dict(req.header_items()), req.get_method(), timeout))
                if req.get_method() == 'GET':
                    return FakeResp({'data': [{'id': 'deepseek-chat'}, {'id': 'deepseek-reasoner'}]})
                return FakeResp({'choices': [{'message': {'content': '管理员，连上 DeepSeek 了。'}}]})

        with patch('si_pet.conversation._opener', return_value=FakeOpener()) as opener_mock:
            models = fetch_models('https://api.deepseek.com', api_key='sk-test-secret', provider='deepseek')
            self.assertEqual(models, ['deepseek-chat', 'deepseek-reasoner'])
            opener_mock.assert_called_with(remote=True)

            reply = request_reply(
                'https://api.deepseek.com/v1',
                'deepseek-chat',
                [{'role': 'user', 'content': '测试'}],
                api_key='sk-test-secret',
                provider='deepseek',
            )
            self.assertEqual(reply, '管理员，连上 DeepSeek 了。')
            self.assertEqual(captured_reqs[0][0], 'https://api.deepseek.com/v1/models')
            self.assertEqual(captured_reqs[1][0], 'https://api.deepseek.com/v1/chat/completions')
            self.assertEqual(captured_reqs[1][1].get('Authorization'), 'Bearer sk-test-secret')

    def test_conversation_dialog_switches_between_local_and_deepseek(self):
        from si_pet.conversation_ui import ConversationDialog
        app = qt_application()
        class Controller:
            def __init__(self): self.spoken = []
            def say(self, text, force): self.spoken.append(text)
        with tempfile.TemporaryDirectory() as folder:
            dialog = ConversationDialog(Controller(), Path(folder) / 'conversation.json')
            ds_idx = dialog.service.findData('deepseek')
            self.assertGreaterEqual(ds_idx, 0)
            dialog.service.setCurrentIndex(ds_idx)
            app.processEvents()
            self.assertEqual(dialog.provider, 'deepseek')
            self.assertEqual(dialog.endpoint.text(), DEEPSEEK_DEFAULT_ENDPOINT)
            self.assertEqual(dialog.model.text(), DEEPSEEK_DEFAULT_MODEL)
            self.assertEqual(dialog.api_key.text(), '')

            # Submitting without API key is refused immediately before networking
            dialog.input.setPlainText('测试发送')
            dialog.submit()
            self.assertFalse(dialog.busy)
            self.assertIn('API Key', dialog.status.text())

            import shiboken6
            try: dialog.completed.disconnect()
            except (RuntimeError, TypeError): pass
            dialog.close()
            try: shiboken6.delete(dialog)
            except RuntimeError: pass
            app.processEvents()

    def test_lan_endpoint_validation_and_roundtrip(self):
        self.assertEqual(lan_endpoint('http://10.19.73.160:11434'), 'http://10.19.73.160:11434/v1')
        self.assertEqual(lan_endpoint('http://192.168.1.88:11434/v1/'), 'http://192.168.1.88:11434/v1')
        self.assertEqual(lan_endpoint('http://gpu-host.local:11434'), 'http://gpu-host.local:11434/v1')
        self.assertEqual(
            endpoint_for_provider('lan', 'http://172.16.10.2:11434/v1'),
            'http://172.16.10.2:11434/v1',
        )
        for bad in (
            'http://8.8.8.8:11434/v1',
            'http://example.com:11434/v1',
            'http://0.0.0.0:11434/v1',
            'http://169.254.1.10:11434/v1',
            'http://203.0.113.1:11434/v1',
            'https://192.168.1.2:11434/v1',
            'http://192.168.1.2/v1',
        ):
            with self.assertRaises(ConversationError):
                lan_endpoint(bad)

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'conversation.json'
            session = Conversation(path)
            session.provider = 'lan'
            session.endpoint = 'http://10.19.73.160:11434/v1'
            session.model = LAN_DEFAULT_MODEL
            session.commit('你好', '管理员，我在显卡主机上。')
            reloaded = Conversation(path)
            self.assertEqual(reloaded.provider, 'lan')
            self.assertEqual(reloaded.endpoint, 'http://10.19.73.160:11434/v1')
            self.assertEqual(reloaded.model, LAN_DEFAULT_MODEL)

        from si_pet.conversation_ui import ConversationDialog
        app = qt_application()
        class Controller:
            def __init__(self): self.spoken = []
            def say(self, text, force): self.spoken.append(text)
        with tempfile.TemporaryDirectory() as folder:
            dialog = ConversationDialog(Controller(), Path(folder) / 'conversation.json')
            remote_idx = dialog.service.findData('remote')
            self.assertGreaterEqual(remote_idx, 0)
            dialog.service.setCurrentIndex(remote_idx)
            app.processEvents()
            self.assertEqual(dialog.provider, 'remote')
            self.assertEqual(dialog.endpoint.text(), REMOTE_DEFAULT_ENDPOINT)
            self.assertEqual(dialog.model.text(), REMOTE_DEFAULT_MODEL)

            import shiboken6
            try: dialog.completed.disconnect()
            except (RuntimeError, TypeError): pass
            dialog.close()
            try: shiboken6.delete(dialog)
            except RuntimeError: pass
            app.processEvents()

    def test_cross_network_remote_endpoint_and_roundtrip(self):
        self.assertEqual(
            remote_endpoint('https://abc-gpu.trycloudflare.com'),
            'https://abc-gpu.trycloudflare.com/v1',
        )
        self.assertEqual(
            remote_endpoint('https://gpu.example.com:8443/ollama/v1/'),
            'https://gpu.example.com:8443/ollama/v1',
        )
        self.assertEqual(
            remote_endpoint('http://100.88.12.34:11434'),
            'http://100.88.12.34:11434/v1',
        )
        self.assertEqual(
            endpoint_for_provider('remote', 'http://123.45.67.89:11434/v1'),
            'http://123.45.67.89:11434/v1',
        )
        for bad in (
            'ftp://gpu.example.com/v1',
            'https://user:pass@gpu.example.com/v1',
            'https://gpu.example.com/v1?token=1',
            'http://0.0.0.0:11434/v1',
            'http://169.254.1.5:11434/v1',
            'https://gpu.example.com/badpath',
        ):
            with self.assertRaises(ConversationError):
                remote_endpoint(bad)

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'conversation.json'
            session = Conversation(path)
            session.provider = 'remote'
            session.endpoint = 'https://abc-gpu.trycloudflare.com/v1'
            session.model = REMOTE_DEFAULT_MODEL
            session.commit('测试跨网', '管理员，我在远程显卡主机上。')
            reloaded = Conversation(path)
            self.assertEqual(reloaded.provider, 'remote')
            self.assertEqual(reloaded.endpoint, 'https://abc-gpu.trycloudflare.com/v1')
            self.assertEqual(reloaded.model, REMOTE_DEFAULT_MODEL)

    def test_custom_endpoint_validation_and_school_api_roundtrip(self):
        self.assertEqual(
            custom_endpoint('https://genai.school.edu.cn'),
            'https://genai.school.edu.cn/v1',
        )
        self.assertEqual(
            custom_endpoint('https://genai.school.edu.cn/api/v1/'),
            'https://genai.school.edu.cn/api/v1',
        )
        self.assertEqual(
            custom_endpoint('https://genai.school.edu.cn/openai/chat/completions'),
            'https://genai.school.edu.cn/openai',
        )
        self.assertEqual(
            custom_endpoint('https://genai.school.edu.cn/v1/models'),
            'https://genai.school.edu.cn/v1',
        )
        self.assertEqual(
            endpoint_for_provider('custom', 'http://10.15.8.2:8080/gateway'),
            'http://10.15.8.2:8080/gateway',
        )
        for bad in (
            '',
            'ftp://genai.school.edu.cn/v1',
            'https://user:pass@genai.school.edu.cn/v1',
            'https://genai.school.edu.cn/v1?k=1',
            'http://0.0.0.0:8080/v1',
            'http://169.254.1.2:8080/v1',
        ):
            with self.assertRaises(ConversationError):
                custom_endpoint(bad)

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'conversation.json'
            session = Conversation(path)
            session.provider = 'custom'
            session.endpoint = 'https://genai.school.edu.cn/api/v1'
            session.model = 'Qwen3-32B'
            session.commit('测试学校接口', '管理员，已收到。')
            reloaded = Conversation(path)
            self.assertEqual(reloaded.provider, 'custom')
            self.assertEqual(reloaded.endpoint, 'https://genai.school.edu.cn/api/v1')
            self.assertEqual(reloaded.model, 'Qwen3-32B')

        from si_pet.conversation_ui import ConversationDialog
        app = qt_application()
        class Controller:
            def __init__(self): self.spoken = []
            def say(self, text, force): self.spoken.append(text)
        with tempfile.TemporaryDirectory() as folder:
            dialog = ConversationDialog(Controller(), Path(folder) / 'conversation.json')
            custom_idx = dialog.service.findData('custom')
            self.assertGreaterEqual(custom_idx, 0)
            dialog.service.setCurrentIndex(custom_idx)
            app.processEvents()
            self.assertEqual(dialog.provider, 'custom')
            self.assertEqual(dialog.endpoint.text(), CUSTOM_DEFAULT_ENDPOINT)
            self.assertEqual(dialog.model.text(), '')

            import shiboken6
            try: dialog.completed.disconnect()
            except (RuntimeError, TypeError): pass
            dialog.close()
            try: shiboken6.delete(dialog)
            except RuntimeError: pass
            app.processEvents()


if __name__ == '__main__': unittest.main()
