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
from si_pet.conversation import Conversation, ConversationError, local_endpoint, request_reply

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


if __name__ == '__main__': unittest.main()
