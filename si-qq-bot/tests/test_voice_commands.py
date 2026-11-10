"""Offline core tests for administrator reply-mode commands and record notices.

All identifiers are synthetic, every file lives in a temporary directory, and
no QQ, network, credential, or real user data is involved.  The model is a
local callable that fails the test if it is ever invoked.
"""
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import reply_settings  # noqa: E402
from core import (  # noqa: E402
    QQCore, _MODE_FAILED_TEXT, _MODE_TEXT_TEXT,
    _MODE_VOICE_TEXT,
)

ADMIN = "654321"
BOT = "123456"
OTHER = "777777"


class VoiceCommandTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.data = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)
        self.model_calls = []
        # These tests cover command persistence, not live TTS. Never load a
        # real voice engine while running the offline command suite.
        self.voice_patch = mock.patch('voice_reply.prepare_reply')
        prepared = self.voice_patch.start().return_value
        prepared.voice_ready = False
        self.addCleanup(self.voice_patch.stop)

    def make_core(self, model_reply="模型答复。"):
        def request_reply(*args):
            self.model_calls.append(args)
            return model_reply

        return QQCore(self.data, BOT, request_reply=request_reply)

    def bind(self, core):
        code = core.begin_pairing()
        self.assertIsNotNone(code)
        bound = core.handle_private(ADMIN, "bind-1", f"绑定 {code}")
        self.assertEqual(bound.kind, "bound")
        self.assertIsNone(core._pending)
        return core

    def settings_path(self):
        return reply_settings.path_for(self.data)

    def read_settings_bytes(self):
        return self.settings_path().read_bytes()

    def read_state(self, core):
        return json.loads(core._state_path.read_text(encoding="utf-8"))

    # -- authorized mode commands ---------------------------------------

    def test_authorized_commands_persist_and_never_reach_the_model(self):
        core = self.bind(self.make_core())
        notified = []
        core.set_reply_mode_callback(notified.append)

        reply = core.handle_private(ADMIN, "cmd-1", "/语音")
        self.assertEqual(reply.kind, "mode")
        self.assertEqual(reply.text, _MODE_VOICE_TEXT)
        self.assertEqual(getattr(reply, "delivery_token", None), None)
        self.assertEqual(notified, ["voice"])
        self.assertEqual(self.model_calls, [])
        self.assertEqual(core._turns, [])
        self.assertIsNone(core._pending)
        self.assertEqual(
            self.settings_path().read_bytes(),
            b'{"schema":1,"reply_mode":"voice"}',
        )
        state = self.read_state(core)
        self.assertEqual(state["turns"], [])
        self.assertIsNone(state["pending"])
        self.assertIn("cmd-1", state["processed_message_ids"])
        # The command is not persisted as conversation text.
        self.assertNotIn("/语音", json.dumps(state, ensure_ascii=False))
        self.assertNotIn(ADMIN, self.settings_path().read_text(encoding="utf-8"))

        reply = core.handle_private(ADMIN, "cmd-2", "/文字")
        self.assertEqual(reply.kind, "mode")
        self.assertEqual(reply.text, _MODE_TEXT_TEXT)
        self.assertEqual(notified, ["voice", "text"])
        self.assertEqual(
            self.settings_path().read_bytes(),
            b'{"schema":1,"reply_mode":"text"}',
        )
        self.assertEqual(self.model_calls, [])

    def test_only_exact_commands_from_the_bound_admin_change_mode(self):
        unbound = self.make_core()
        # Unbound: silently ignored, no file, no model.
        self.assertIsNone(unbound.handle_private(ADMIN, "u-1", "/语音"))
        self.assertFalse(self.settings_path().exists())
        # The bot's own account is never authorized, bound or not.
        self.assertIsNone(unbound.handle_private(BOT, "u-2", "/语音"))
        self.assertFalse(self.settings_path().exists())

        core = self.bind(self.make_core())
        notified = []
        core.set_reply_mode_callback(notified.append)
        malformed = (
            "/语音 你好",
            "/语音！",
            "/文字\n/语音",
            "/语音/文字",
            "/语音abc",
            "/文字。",
        )
        for index, text in enumerate(malformed):
            with self.subTest(text=text):
                # Combined or malformed command-like text never changes the
                # mode, reaches the model, or produces command output.
                self.assertIsNone(
                    core.handle_private(ADMIN, f"bad-{index}", text)
                )
        self.assertEqual(reply_settings.read_mode(self.data),
                         (False, "text"))
        self.assertEqual(notified, [])
        self.assertEqual(self.model_calls, [])

        # Surrounding whitespace is trimmed before the exact match.
        self.assertEqual(
            core.handle_private(ADMIN, "ws-1", "  /语音  ").kind, "mode"
        )
        self.assertEqual(notified, ["voice"])

        # Text that merely mentions a command later in the sentence is an
        # ordinary chat message, not a command; it cannot change the setting.
        chat = core.handle_private(ADMIN, "mention-1", "请/语音")
        self.assertEqual(chat.kind, "chat")
        self.assertEqual(reply_settings.read_mode(self.data), (True, "voice"))
        self.assertEqual(notified, ["voice"])
        self.assertEqual(len(self.model_calls), 1)
        self.assertTrue(core.finalize_reply(chat.delivery_token, True))

        # Other users never change the setting.
        mode_before = reply_settings.read_mode(self.data)
        self.assertIsNone(core.handle_private(OTHER, "other-1", "/文字"))
        self.assertEqual(reply_settings.read_mode(self.data), mode_before)

    def test_other_users_and_bot_cannot_change_an_existing_setting(self):
        core = self.bind(self.make_core())
        self.assertEqual(
            core.handle_private(ADMIN, "set-1", "/语音").kind, "mode"
        )
        self.assertEqual(reply_settings.read_mode(self.data), (True, "voice"))
        notified = []
        core.set_reply_mode_callback(notified.append)
        self.assertIsNone(core.handle_private(OTHER, "other-2", "/文字"))
        self.assertIsNone(core.handle_private(BOT, "bot-2", "/文字"))
        self.assertIsNone(core.handle_private("", "bad-2", "/文字"))
        self.assertIsNone(core.handle_private(ADMIN, "bad-3", None))
        self.assertEqual(reply_settings.read_mode(self.data), (True, "voice"))
        self.assertEqual(notified, [])
        self.assertEqual(self.model_calls, [])

    def test_repeated_command_and_restart_do_not_repeat_the_effect(self):
        core = self.bind(self.make_core())
        notified = []
        core.set_reply_mode_callback(notified.append)
        self.assertEqual(
            core.handle_private(ADMIN, "cmd-1", "/语音").kind, "mode"
        )
        self.assertEqual(notified, ["voice"])
        before = self.read_settings_bytes()

        # A replayed event in the same process is ignored.
        self.assertIsNone(core.handle_private(ADMIN, "cmd-1", "/文字"))
        self.assertEqual(notified, ["voice"])
        self.assertEqual(self.read_settings_bytes(), before)

        # A restarted core loads the processed ID and ignores it as well.
        restarted = QQCore(self.data, BOT)
        notified_after = []
        restarted.set_reply_mode_callback(notified_after.append)
        self.assertIsNone(restarted.handle_private(ADMIN, "cmd-1", "/文字"))
        self.assertEqual(notified_after, [])
        self.assertEqual(self.read_settings_bytes(), before)
        # A genuinely new message is still processed.
        self.assertEqual(
            restarted.handle_private(ADMIN, "cmd-9", "/文字").kind, "mode"
        )
        self.assertEqual(notified_after, ["text"])
        self.assertEqual(
            reply_settings.read_mode(self.data), (True, "text")
        )

    def test_commit_failure_returns_failure_notice_without_writing(self):
        core = self.bind(self.make_core())
        notified = []
        core.set_reply_mode_callback(notified.append)
        with mock.patch.object(
                core, "_commit", side_effect=OSError("synthetic")):
            reply = core.handle_private(ADMIN, "cmd-fail", "/语音")
        self.assertEqual(reply.kind, "mode")
        self.assertEqual(reply.text, _MODE_FAILED_TEXT)
        self.assertFalse(self.settings_path().exists())
        self.assertEqual(notified, [])
        self.assertEqual(self.model_calls, [])

    def test_corrupt_settings_are_preserved_and_command_fails_closed(self):
        original = b'{"schema":1,"reply_mode":'
        self.settings_path().write_bytes(original)
        core = self.bind(self.make_core())
        notified = []
        core.set_reply_mode_callback(notified.append)
        reply = core.handle_private(ADMIN, "cmd-corrupt", "/语音")
        self.assertEqual(reply.kind, "mode")
        self.assertEqual(reply.text, _MODE_FAILED_TEXT)
        self.assertEqual(self.read_settings_bytes(), original)
        self.assertEqual(notified, [])
        self.assertEqual(self.model_calls, [])

    def test_settings_write_failure_keeps_the_previous_mode(self):
        self.settings_path().write_bytes(b'{"schema":1,"reply_mode":"text"}')
        core = self.bind(self.make_core())
        notified = []
        core.set_reply_mode_callback(notified.append)
        with mock.patch.object(
                reply_settings, "write_mode",
                side_effect=OSError("synthetic")):
            reply = core.handle_private(ADMIN, "cmd-write", "/语音")
        self.assertEqual(reply.kind, "mode")
        self.assertEqual(reply.text, _MODE_FAILED_TEXT)
        self.assertEqual(
            self.settings_path().read_bytes(),
            b'{"schema":1,"reply_mode":"text"}',
        )
        self.assertEqual(notified, [])
        # The processed ID was durably recorded before the failed write, so a
        # retry of the same event does not repeatedly try or send.
        self.assertIn(
            "cmd-write", self.read_state(core)["processed_message_ids"]
        )
        self.assertIsNone(core.handle_private(ADMIN, "cmd-write", "/语音"))
        self.assertEqual(notified, [])

    def test_atomic_writer_leaves_no_partial_file_on_replace_failure(self):
        path = self.settings_path()
        path.write_bytes(b'{"schema":1,"reply_mode":"text"}')
        with mock.patch.object(
                reply_settings.os, "replace", side_effect=OSError("synthetic")):
            with self.assertRaises(OSError):
                reply_settings.write_mode(self.data, "voice")
        self.assertEqual(path.read_bytes(), b'{"schema":1,"reply_mode":"text"}')
        leftovers = [item.name for item in self.data.iterdir()
                     if item.name != reply_settings.FILENAME]
        self.assertEqual(leftovers, [])

    def test_callback_failure_is_ignored_and_receives_only_the_mode(self):
        core = self.bind(self.make_core())
        received = []

        def failing(mode):
            received.append(mode)
            raise RuntimeError("synthetic callback failure")

        core.set_reply_mode_callback(failing)
        reply = core.handle_private(ADMIN, "cmd-cb", "/语音")
        self.assertEqual(reply.kind, "mode")
        self.assertEqual(reply.text, _MODE_VOICE_TEXT)
        self.assertEqual(received, ["voice"])
        self.assertEqual(reply_settings.read_mode(self.data), (True, "voice"))

    def test_lock_contention_serializes_with_a_gui_style_writer(self):
        core = self.bind(self.make_core())
        lock = reply_settings.mode_lock(self.data)
        result = {}

        def command():
            result["reply"] = core.handle_private(ADMIN, "cmd-lock", "/语音")

        with lock:
            worker = threading.Thread(target=command)
            worker.start()
            worker.join(0.15)
            self.assertTrue(worker.is_alive())
            self.assertEqual(reply_settings.read_mode(self.data),
                             (False, "text"))
        worker.join(3.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result["reply"].kind, "mode")
        self.assertEqual(reply_settings.read_mode(self.data), (True, "voice"))

        # A GUI-style read-check-write under the same lock cannot be lost.
        with lock:
            found, mode = reply_settings.read_mode(self.data)
            self.assertEqual((found, mode), (True, "voice"))
            reply_settings.write_mode(self.data, "text")
        self.assertEqual(reply_settings.read_mode(self.data), (True, "text"))

    def test_pending_delivery_blocks_mode_changes_until_resolved(self):
        core = self.bind(self.make_core())
        chat = core.handle_private(ADMIN, "chat-1", "合成管理员消息")
        self.assertEqual(chat.kind, "chat")
        self.assertIsNotNone(core._pending)
        notified = []
        core.set_reply_mode_callback(notified.append)

        self.assertIsNone(core.handle_private(ADMIN, "cmd-blocked", "/语音"))
        self.assertEqual(reply_settings.read_mode(self.data),
                         (False, "text"))
        self.assertEqual(notified, [])
        self.assertEqual(len(core._turns), 0)

        self.assertTrue(core.finalize_reply(chat.delivery_token, True))
        self.assertEqual(
            core.handle_private(ADMIN, "cmd-blocked", "/语音").kind, "mode"
        )
        self.assertEqual(notified, ["voice"])
        self.assertEqual(len(core._turns), 1)

    # -- record and mixed notices ---------------------------------------






if __name__ == "__main__":
    unittest.main()
