"""Lifecycle regression tests for the local conversation dialog.

The tests drive the real ``ConversationDialog`` through a real Qt event loop
against a controlled loopback HTTP server.  The server deliberately blocks
until the test releases it, so the assertions can observe the pending state
instead of racing a fast response.

These tests do NOT use, start, or validate a real local model.  They do not
measure desktop-pet animation, speech-bubble layout, audio, or real-machine
latency.  The local server is a protocol stub, not a model-quality oracle.

Run from the project root:

    $env:QT_QPA_PLATFORM = 'offscreen'
    E:\\create\\si-desktop-pet\\.venv\\Scripts\\python.exe -m unittest discover -s tests -p test_conversation_lifecycle.py
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
import shiboken6

from si_pet.conversation import Conversation, ConversationError
from si_pet.conversation_ui import ConversationDialog

TIMEOUT_MESSAGE = '本机模型未连接或等待超时。请确认服务已启动，再重试。'


class StubLoopbackChat:
    """Minimal OpenAI-compatible loopback server with a test-controlled gate.

    ``block=True`` makes each POST wait for :meth:`release_all` before the
    response is written.  That is what keeps the client request pending long
    enough for the Qt event loop and the UI to be inspected.
    """

    def __init__(self, *, block=True, http_status=200, reply='好，我在这里。'):
        self.block = block
        self.http_status = http_status
        self.reply = reply
        self.requests = []
        self.condition = threading.Condition()
        # ``release`` is the release-all latch; ``gates`` holds one event per
        # received request so a single test can let the abandoned request finish
        # while keeping a newer one pending.
        self.release = threading.Event()
        self.gates = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.0'

            def log_message(self, *args):  # keep unittest output clean
                pass

            def do_POST(self):
                length = int(self.headers.get('Content-Length', '0'))
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw.decode('utf-8'))
                except (UnicodeDecodeError, ValueError):
                    body = None
                with owner.condition:
                    gate = threading.Event()
                    if owner.release.is_set():
                        gate.set()
                    owner.gates.append(gate)
                    owner.requests.append({
                        'path': self.path,
                        'headers': dict(self.headers),
                        'body': body,
                    })
                    owner.condition.notify_all()
                if owner.block:
                    gate.wait(15.0)
                try:
                    if owner.http_status != 200:
                        self.send_response(owner.http_status)
                        self.send_header('Content-Length', '0')
                        self.end_headers()
                        return
                    payload = json.dumps(
                        {'choices': [{'message': {'content': owner.reply}}]},
                        ensure_ascii=False,
                    ).encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    # The client may already have timed out or closed; that is
                    # exactly the late-result scenario under test.
                    pass

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(
            target=self.httpd.serve_forever,
            name='lifecycle-stub-http',
            daemon=True,
        )
        self.thread.start()
        self.endpoint = f'http://127.0.0.1:{self.httpd.server_port}/v1'

    def wait_for_requests(self, count, timeout=5.0):
        deadline = time.monotonic() + timeout
        with self.condition:
            while len(self.requests) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(remaining)
        return True

    def release_request(self, index):
        with self.condition:
            if index < len(self.gates):
                self.gates[index].set()

    def release_all(self):
        self.release.set()
        with self.condition:
            for gate in self.gates:
                gate.set()

    def stop(self):
        self.release_all()
        self.httpd.shutdown()
        self.httpd.server_close()


class RecordingController:
    """Stand-in for the desktop pet controller; records speech requests."""

    def __init__(self):
        self.spoken = []

    def say(self, text, force=False):
        self.spoken.append((text, force))


class ConversationLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'conversation.json'
        self.ctrl = RecordingController()
        self.server = StubLoopbackChat()
        self.addCleanup(self.server.stop)
        self.dialogs = []

    def tearDown(self):
        # Let every worker from a failed/early test finish *before* the dialog
        # references are dropped, so no worker can emit into a widget that is
        # being deleted.
        self.server.release_all()
        for dialog in self.dialogs:
            try:
                self.wait_until(lambda d=dialog: not d.busy, 2.0)
            except RuntimeError:
                pass
        # A closed-while-pending dialog releases ``busy`` immediately while its
        # worker can still be in flight; wait for the workers themselves before
        # the C++ widget is deleted.
        self.wait_until(
            lambda: not any(t.name == 'si-local-chat' and t.is_alive()
                            for t in threading.enumerate()),
            2.0,
        )
        for dialog in self.dialogs:
            # Destroy each widget here, on the main thread, at a deterministic
            # point instead of leaving it to a later cyclic-GC pass during
            # another test (which is where the native abort was observed).
            # ``shiboken6.delete`` removes the C++ object immediately; pending
            # queued events targeting it are dropped by Qt with it.
            try:
                dialog.completed.disconnect()
            except (RuntimeError, TypeError):
                pass
            try:
                dialog.close()
            except RuntimeError:
                pass
            if not getattr(dialog, 'busy', False):
                try:
                    shiboken6.delete(dialog)
                except RuntimeError:
                    pass
        self.dialogs.clear()
        self.pump(0.05)

    # -- event-loop helpers -------------------------------------------------

    def pump(self, seconds=0.0):
        """Run the real Qt event dispatcher for at least one pass."""
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

    def wait_idle(self, dialog, timeout=6.0):
        self.assertTrue(
            self.wait_until(lambda: not dialog.busy, timeout),
            'dialog stayed busy; the worker or queued Qt signal did not finish',
        )
        # Let the rest of the reply slot (commit/render/status) run after the
        # busy flag flips inside finished_reply().
        self.pump(0.03)

    def wait_requests(self, count, timeout=5.0):
        self.assertTrue(
            self.server.wait_for_requests(count, timeout),
            f'stub server did not receive request #{count}',
        )

    def make_dialog(self, *, show=False):
        dialog = ConversationDialog(self.ctrl, self.path)
        # This local HTTP stub exercises lifecycle behavior rather than a
        # school-model choice; use the ordinary local-service preset explicitly.
        dialog.service.setCurrentIndex(0)
        dialog.endpoint.setText(self.server.endpoint)
        dialog.model.setText('lifecycle-stub-model')
        self.dialogs.append(dialog)
        if show:
            dialog.show()
            self.pump(0.02)
        return dialog

    def post_body(self, index):
        with self.server.condition:
            return self.server.requests[index]['body']

    def assert_file_turns(self, expected):
        self.assertTrue(self.path.exists(), 'conversation.json was not written')
        data = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(data['turns'], expected)

    # -- tests --------------------------------------------------------------

    def test_submit_runs_in_worker_and_event_loop_stays_responsive(self):
        dialog = self.make_dialog()
        dialog.input.setPlainText('等回答时界面不要卡住')

        ticks = []
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks.append(time.monotonic()))
        timer.start()
        self.addCleanup(timer.stop)

        started = time.monotonic()
        dialog.send.click()
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 1.0, 'submit() blocked the main thread')
        self.assertTrue(dialog.busy)
        self.assertFalse(dialog.send.isEnabled())
        self.wait_requests(1)

        # The request is still gated in the server handler.  A real Qt timer
        # must keep firing on the main thread during that window.
        self.assertTrue(
            self.wait_until(lambda: len(ticks) >= 3, 2.0),
            'Qt timer events did not fire while the request was pending',
        )
        self.assertTrue(dialog.busy)
        self.assertFalse(dialog.send.isEnabled())
        self.assertTrue(
            any(t.name == 'si-local-chat' and t.is_alive()
                for t in threading.enumerate()),
            'request did not run on the named worker thread',
        )

        self.server.release_all()
        self.wait_idle(dialog)
        self.assertTrue(dialog.send.isEnabled())
        self.assertEqual(len(dialog.session.turns), 1)

    def test_success_commits_context_persists_and_clears_input(self):
        seeded = Conversation(self.path)
        seeded.endpoint = self.server.endpoint
        seeded.model = 'lifecycle-stub-model'
        seeded.profile = '喜欢安静'
        seeded.commit('旧问题', '旧回答')

        dialog = self.make_dialog()
        dialog.profile.setPlainText('喜欢安静，也喜欢画画')
        dialog.input.setPlainText('新问题')
        dialog.send.click()
        self.wait_requests(1)

        body = self.post_body(0)
        self.assertEqual(body['model'], 'lifecycle-stub-model')
        self.assertFalse(body['stream'])
        self.assertEqual(body['messages'][-1], {'role': 'user', 'content': '新问题'})
        self.assertIn(
            '管理员自述偏好（资料）：喜欢安静，也喜欢画画',
            body['messages'][1]['content'],
        )
        self.assertEqual(body['messages'][2], {'role': 'user', 'content': '旧问题'})
        self.assertEqual(body['messages'][3], {'role': 'assistant', 'content': '旧回答'})

        self.server.release_all()
        self.wait_idle(dialog)

        expected_turn = {'user': '新问题', 'assistant': '好，我在这里。'}
        self.assertEqual(dialog.session.turns[-1], expected_turn)
        self.assertEqual(len(dialog.session.turns), 2)
        self.assert_file_turns(dialog.session.turns)
        self.assertEqual(dialog.input.toPlainText(), '')
        self.assertIn('新问题', dialog.transcript.toPlainText())
        self.assertIn('好，我在这里。', dialog.transcript.toPlainText())
        self.assertEqual(self.ctrl.spoken, [('好，我在这里。', True)])
        self.assertIn('已保存到本机', dialog.status.text())
        self.assertTrue(dialog.send.isEnabled())

        restored = Conversation(self.path)
        self.assertEqual(restored.turns, dialog.session.turns)
        self.assertEqual(restored.profile, '喜欢安静，也喜欢画画')
        self.assertEqual(restored.model, 'lifecycle-stub-model')

    def test_failure_keeps_input_and_does_not_write_history_or_speak(self):
        self.server.http_status = 500
        self.server.block = False
        dialog = self.make_dialog()
        dialog.input.setPlainText('失败时不要吞掉这句话')
        dialog.send.click()
        self.wait_requests(1)
        self.wait_idle(dialog)

        self.assertEqual(dialog.input.toPlainText(), '失败时不要吞掉这句话')
        self.assertEqual(dialog.session.turns, [])
        self.assert_file_turns([])
        self.assertEqual(self.ctrl.spoken, [])
        self.assertIn('500', dialog.status.text())
        self.assertFalse(dialog.busy)
        self.assertTrue(dialog.send.isEnabled())

    def test_double_submit_while_busy_sends_only_once(self):
        dialog = self.make_dialog()
        dialog.input.setPlainText('只应发送一次')
        dialog.send.click()
        self.assertTrue(dialog.busy)
        self.assertFalse(dialog.send.isEnabled())

        # Re-enable the button to defeat the disabled-state guard and prove the
        # independent busy guard in submit() also rejects the second click.
        dialog.send.setEnabled(True)
        dialog.send.click()
        self.wait_requests(1)
        self.pump(0.2)
        self.assertEqual(len(self.server.requests), 1)

        self.server.release_all()
        self.wait_idle(dialog)
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(len(dialog.session.turns), 1)
        self.assertEqual(self.ctrl.spoken, [('好，我在这里。', True)])

    def test_clear_while_pending_discards_late_reply(self):
        dialog = self.make_dialog()
        dialog.input.setPlainText('清除前的请求')
        dialog.send.click()
        self.wait_requests(1)
        self.assertTrue(dialog.busy)

        generation = dialog.generation
        dialog.clear.click()
        self.assertEqual(dialog.generation, generation + 1)
        self.assertEqual(dialog.session.turns, [])
        self.assert_file_turns([])
        self.assertEqual(dialog.transcript.toPlainText(), '')
        self.assertIn('已清除', dialog.status.text())
        # Known behavior: clearing does not cancel the in-flight request, so
        # the dialog stays busy and Send stays disabled until it returns.
        self.assertTrue(dialog.busy)
        self.assertFalse(dialog.send.isEnabled())

        self.server.release_all()
        self.wait_idle(dialog)
        self.pump(0.05)

        self.assertEqual(dialog.session.turns, [])
        self.assert_file_turns([])
        self.assertEqual(dialog.transcript.toPlainText(), '')
        self.assertEqual(self.ctrl.spoken, [])
        self.assertIn('已清除', dialog.status.text())
        self.assertNotIn('已保存到本机', dialog.status.text())

    def test_close_while_pending_discards_late_reply_and_reopen_has_no_ghost_turn(self):
        dialog = self.make_dialog(show=True)
        dialog.input.setPlainText('关闭前的请求')
        dialog.send.click()
        self.wait_requests(1)

        generation = dialog.generation
        dialog.close()
        self.pump(0.02)
        self.assertFalse(dialog.isVisible())
        self.assertEqual(dialog.generation, generation + 1)

        self.server.release_all()
        self.wait_idle(dialog)
        # Closing already released the busy flag; wait for the abandoned worker
        # to actually deliver its late reply before checking it was ignored.
        self.assertTrue(
            self.wait_until(
                lambda: not any(t.name == 'si-local-chat' and t.is_alive()
                                for t in threading.enumerate()),
                2.0,
            ),
            'the abandoned request did not finish',
        )
        self.pump(0.05)

        self.assertEqual(dialog.session.turns, [])
        self.assert_file_turns([])
        self.assertEqual(self.ctrl.spoken, [])

        reopened = ConversationDialog(self.ctrl, self.path)
        self.dialogs.append(reopened)
        self.assertEqual(reopened.session.turns, [])
        self.assertEqual(reopened.transcript.toPlainText(), '')
        self.assertEqual(reopened.input.toPlainText(), '')
        self.assertEqual(reopened.model.text(), 'lifecycle-stub-model')
        self.assertEqual(reopened.endpoint.text(), self.server.endpoint)

    def test_close_while_pending_reopens_ready_and_old_reply_cannot_reset_new_request(self):
        dialog = self.make_dialog(show=True)
        dialog.input.setPlainText('关闭前的旧请求')
        dialog.send.click()
        self.wait_requests(1)
        self.assertTrue(dialog.busy)

        dialog.close()
        self.pump(0.02)
        self.assertFalse(dialog.isVisible())
        # Closing abandons the wait for UI purposes.  urllib cannot cancel the
        # request, but reopening must not show a disabled Send until it returns.
        self.assertFalse(dialog.busy)
        self.assertTrue(dialog.send.isEnabled())

        dialog.show()
        self.pump(0.02)
        dialog.input.setPlainText('重新打开后的新请求')
        dialog.send.click()
        self.wait_requests(2)
        self.assertTrue(dialog.busy)
        self.assertFalse(dialog.send.isEnabled())

        # Release only the abandoned request.  Its worker finishes and delivers
        # the reply, which must be ignored without clearing the newer request's
        # busy state.
        self.server.release_request(0)
        self.assertTrue(
            self.wait_until(
                lambda: sum(1 for t in threading.enumerate()
                            if t.name == 'si-local-chat') <= 1,
                2.0,
            ),
            'the abandoned request did not finish',
        )
        self.pump(0.1)
        self.assertTrue(
            dialog.busy,
            'an abandoned reply re-enabled Send while a newer request was pending',
        )
        self.assertFalse(dialog.send.isEnabled())
        self.assertEqual(dialog.session.turns, [])
        self.assertEqual(self.ctrl.spoken, [])
        self.assertNotIn('已保存到本机', dialog.status.text())

        self.server.release_request(1)
        self.wait_idle(dialog)
        self.assertEqual(
            [turn['user'] for turn in dialog.session.turns],
            ['重新打开后的新请求'],
        )
        self.assertEqual(dialog.session.turns[0]['assistant'], '好，我在这里。')
        self.assert_file_turns(dialog.session.turns)
        self.assertEqual(self.ctrl.spoken, [('好，我在这里。', True)])
        self.assertIn('已保存到本机', dialog.status.text())

    def test_reopen_restores_context_and_next_request_uses_it(self):
        first = self.make_dialog()
        first.input.setPlainText('第一问')
        first.send.click()
        self.wait_requests(1)
        self.server.release_all()
        self.wait_idle(first)
        first.close()
        self.pump(0.02)

        reopened = ConversationDialog(self.ctrl, self.path)
        self.dialogs.append(reopened)
        self.assertEqual(
            reopened.session.turns,
            [{'user': '第一问', 'assistant': '好，我在这里。'}],
        )
        self.assertIn('第一问', reopened.transcript.toPlainText())
        self.assertIn('好，我在这里。', reopened.transcript.toPlainText())
        self.assertEqual(reopened.input.toPlainText(), '')
        self.assertEqual(reopened.model.text(), 'lifecycle-stub-model')
        self.assertEqual(reopened.endpoint.text(), self.server.endpoint)

        reopened.input.setPlainText('第二问')
        reopened.send.click()
        self.wait_requests(2)
        second_body = self.post_body(1)
        contents = [m['content'] for m in second_body['messages']]
        self.assertIn('第一问', contents)
        self.assertIn('好，我在这里。', contents)
        self.assertEqual(second_body['messages'][-1], {'role': 'user', 'content': '第二问'})

        self.server.release_all()
        self.wait_idle(reopened)
        self.assertEqual(len(reopened.session.turns), 2)
        self.assert_file_turns(reopened.session.turns)

    def test_editing_while_waiting_keeps_draft_and_commits_original_text(self):
        dialog = self.make_dialog()
        dialog.input.setPlainText('原始问题')
        dialog.send.click()
        self.wait_requests(1)

        dialog.input.setPlainText('等待时写下的新草稿')
        self.server.release_all()
        self.wait_idle(dialog)

        self.assertEqual(dialog.session.turns[0]['user'], '原始问题')
        self.assertEqual(dialog.input.toPlainText(), '等待时写下的新草稿')
        self.assertIn('原始问题', dialog.transcript.toPlainText())
        self.assertNotIn('等待时写下的新草稿', dialog.transcript.toPlainText())
        self.assertEqual(self.ctrl.spoken, [('好，我在这里。', True)])
        self.assert_file_turns(dialog.session.turns)

    def test_accelerated_socket_timeout_keeps_input_and_reports_timeout(self):
        dialog = self.make_dialog()
        dialog.input.setPlainText('超时也要留住这句话')

        original_open = urllib.request.OpenerDirector.open

        def accelerated_open(opener, req, timeout=None):
            # Production passes timeout=90.  Shorten only the socket wait so
            # the real timeout exception path runs without a 90 s test.
            return original_open(opener, req, timeout=0.5)

        with patch.object(urllib.request.OpenerDirector, 'open', accelerated_open):
            dialog.send.click()
            self.wait_requests(1)
            self.wait_idle(dialog, timeout=5.0)

        self.assertEqual(dialog.input.toPlainText(), '超时也要留住这句话')
        self.assertEqual(dialog.session.turns, [])
        self.assert_file_turns([])
        self.assertEqual(self.ctrl.spoken, [])
        self.assertIn(TIMEOUT_MESSAGE, dialog.status.text())
        self.assertFalse(dialog.busy)
        self.assertTrue(dialog.send.isEnabled())

    def test_success_but_save_failure_rolls_back_without_speaking(self):
        dialog = self.make_dialog()
        dialog.input.setPlainText('保存失败也不能丢')
        dialog.send.click()
        self.wait_requests(1)

        with patch.object(
            dialog.session,
            'save',
            side_effect=ConversationError('模拟存档不可写'),
        ):
            self.server.release_all()
            self.wait_idle(dialog)

        self.assertEqual(dialog.session.turns, [])
        self.assert_file_turns([])
        self.assertEqual(dialog.input.toPlainText(), '保存失败也不能丢')
        self.assertEqual(self.ctrl.spoken, [])
        self.assertIn('模拟存档不可写', dialog.status.text())
        self.assertTrue(dialog.send.isEnabled())

    def test_long_reply_is_committed_but_not_spoken_on_desktop(self):
        long_reply = '长' * 161
        self.server.reply = long_reply
        self.server.block = False
        dialog = self.make_dialog()
        dialog.input.setPlainText('请给一段长回答')
        dialog.send.click()
        self.wait_requests(1)
        self.wait_idle(dialog)

        self.assertEqual(dialog.session.turns[-1]['assistant'], long_reply)
        self.assertEqual(self.ctrl.spoken, [])
        self.assertIn('已保存到本机', dialog.status.text())


if __name__ == '__main__':
    unittest.main()
