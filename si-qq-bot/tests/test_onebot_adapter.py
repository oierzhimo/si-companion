"""Offline tests for the loopback OneBot bridge; no real QQ or model access."""
import asyncio
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import QQCore  # noqa: E402
from onebot_adapter import (  # noqa: E402
    OneBotAdapter, _connect_local, validate_ws_url, _delivery_outcome,
    _MODEL_FAILED_AUTHENTICATION_TEXT,
    _MODEL_FAILED_SCHOOL_LOGIN_TEXT,
    _MODEL_FAILED_BUSY_TEXT,
    _MODEL_FAILED_UNAVAILABLE_TEXT,
)


class FakeCore:
    def __init__(self, reply=None, diagnostics=()):
        self.reply = reply or SimpleNamespace(
            text="好的。", kind="chat", delivery_token="fake-delivery-token"
        )
        self.calls = []
        self.finalized = []
        self.unknown = []
        # Fixed codes this fake reports through the adapter-installed hook.
        self.diagnostic_codes = list(diagnostics)
        self.emitted_codes = []
        self._diagnostic_callback = None

    def set_diagnostic_callback(self, callback):
        self._diagnostic_callback = callback

    def _emit_configured_diagnostics(self):
        for code in self.diagnostic_codes:
            self.emitted_codes.append(code)
            callback = self._diagnostic_callback
            if callback is not None:
                callback(code)

    def handle_private(self, sender, message_id, text):
        self.calls.append((sender, message_id, text))
        self._emit_configured_diagnostics()
        return self.reply

    def finalize_reply(self, token, delivered):
        self.finalized.append((token, delivered))
        return True

    def mark_delivery_unknown(self, token):
        self.unknown.append(token)
        return True


class LegacyCore:
    """Core-like object without the optional diagnostic hook."""

    def __init__(self):
        self.finalized = []

    def handle_private(self, sender, message_id, text):
        return SimpleNamespace(
            text="好的。", kind="chat", delivery_token="legacy-token"
        )

    def finalize_reply(self, token, delivered):
        self.finalized.append((token, delivered))
        return True


class FinalizeFailCore(FakeCore):
    """Confirmed send whose local state transaction keeps failing."""

    def finalize_reply(self, token, delivered):
        self.finalized.append((token, delivered))
        return False


class BlockingCore(FakeCore):
    """Fake core whose model call cannot be interrupted by task cancellation."""

    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def handle_private(self, sender, message_id, text):
        self.calls.append((sender, message_id, text))
        self.entered.set()
        self.release.wait(10)
        return self.reply


class FakeWS:
    def __init__(self, loop, ack=True, send_delay=0, send_error=False,
                 ack_secret=None):
        self.loop = loop
        self.ack = ack
        self.send_delay = send_delay
        self.send_error = send_error
        self.ack_secret = ack_secret
        self.incoming = asyncio.Queue()
        self.sent = []
        self.closed = False

    async def send(self, raw):
        packet = json.loads(raw)
        self.sent.append(packet)
        action = packet["action"]
        if action == "get_login_info":
            response = {
                "status": "ok", "retcode": 0,
                "data": {"user_id": 123456}, "echo": packet["echo"],
            }
        elif action == "send_private_msg" and self.send_error:
            # A string send_error models a synthetic sensitive exception.
            raise ConnectionError(
                self.send_error if isinstance(self.send_error, str)
                else "synthetic send failure"
            )
        elif self.ack:
            if self.send_delay:
                await asyncio.sleep(self.send_delay)
            data = {"message_id": 999} if self.ack is True else {}
            if self.ack_secret is not None:
                data = dict(data, debug_token=self.ack_secret)
            response = {
                "status": "ok" if self.ack is True else "failed",
                "retcode": 0 if self.ack is True else 1404,
                "data": data,
                "echo": packet["echo"],
            }
        else:
            return
        await self.incoming.put(json.dumps(response))

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.incoming.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self):
        self.closed = True
        await self.incoming.put(None)

    def inject(self, packet):
        if not self.loop.is_closed():
            self.loop.call_soon_threadsafe(
                self.incoming.put_nowait, json.dumps(packet)
            )


class FakeConnector:
    def __init__(self, ack=True, send_delay=0, send_error=False,
                 ack_secret=None):
        self.ack = ack
        self.send_delay = send_delay
        self.send_error = send_error
        self.ack_secret = ack_secret
        self.ws = None

    async def __aenter__(self):
        self.ws = FakeWS(
            asyncio.get_running_loop(), self.ack, self.send_delay,
            self.send_error, self.ack_secret,
        )
        return self.ws

    async def __aexit__(self, exc_type, exc, tb):
        await self.ws.close()


class AdapterTests(unittest.TestCase):
    def make_adapter(self, *, ack=True, callback=None, send_error=False,
                     core=None, ack_secret=None):
        connector = FakeConnector(
            ack=ack, send_error=send_error, ack_secret=ack_secret
        )
        core = core if core is not None else FakeCore()
        factories = []
        statuses = []

        def factory(bot_id):
            factories.append(bot_id)
            return core

        def status(code, message):
            statuses.append((code, message))
            if callback:
                callback(code, message)

        adapter = OneBotAdapter(
            "ws://127.0.0.1:3001/", "test-token-never-log",
            factory, status, connect_factory=lambda *_: connector,
        )
        return adapter, connector, core, factories, statuses

    def wait_for(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return predicate()

    def test_stale_core_diagnostics_are_ignored_after_stop(self):
        adapter, connector, core, _, statuses = self.make_adapter()
        adapter.start()
        self.assertTrue(self.wait_for(lambda: adapter.verified_bot_id() is not None))
        callback = core._diagnostic_callback
        self.assertTrue(adapter.stop())
        before = list(statuses)
        callback("model_failed")
        time.sleep(0.05)
        self.assertEqual(statuses, before)

    def test_send_ack_classification_requires_consistent_fields(self):
        self.assertEqual(_delivery_outcome({"status":"ok", "retcode":0,
                                           "data":{"message_id":999}}), "delivered")
        self.assertEqual(_delivery_outcome({"status":"failed", "retcode":1404}), "failed")
        for ack in (None, {}, {"status":"ok", "retcode":False, "data":{"message_id":1}},
                    {"status":"ok", "retcode":0, "data":{}},
                    {"status":"failed", "retcode":0},
                    {"status":"ok", "retcode":1404, "data":{"message_id":1}},
                    {"status":"failed", "retcode":"1404"}):
            self.assertEqual(_delivery_outcome(ack), "unknown", repr(ack))

    def test_malformed_ack_blocks_send_until_explicit_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = QQCore(Path(tmp), "synthetic-bot",
                          request_reply=lambda *args: "synthetic reply")
            code = core.begin_pairing()
            self.assertEqual(core.handle_private("654321", "bind-1", f"绑定 {code}").kind, "bound")
            adapter, connector, core, _, statuses = self.make_adapter(core=core)
            original_api = adapter._api
            async def malformed_api(ws, pending, action, params):
                result = await original_api(ws, pending, action, params)
                if action == "send_private_msg":
                    return {"status":"ok", "retcode":0, "data":{}}
                return result
            adapter._api = malformed_api
            adapter.start()
            self.assertTrue(self.wait_for(lambda: adapter.verified_bot_id() is not None))
            event = {"post_type":"message", "message_type":"private", "sub_type":"friend",
                     "self_id":123456, "user_id":654321, "message_id":61, "message":"hello"}
            connector.ws.inject(event)
            self.assertTrue(self.wait_for(lambda: core.local_status()["pending_recovery_required"]))
            self.assertIn("delivery_unknown", [item[0] for item in statuses])
            self.assertNotIn("reply_acknowledged", [item[0] for item in statuses])
            self.assertNotIn("delivery_rejected", [item[0] for item in statuses])
            sent = lambda: len([item for item in connector.ws.sent if item["action"] == "send_private_msg"])
            self.assertEqual(sent(), 1)
            connector.ws.inject(dict(event, message_id=62))
            connector.ws.inject(event)
            time.sleep(0.1)
            self.assertEqual(sent(), 1)
            self.assertTrue(core.discard_uncertain_pending())
            connector.ws.inject(event)
            time.sleep(0.1)
            self.assertEqual(sent(), 1)
            adapter._api = original_api
            connector.ws.inject(dict(event, message_id=63))
            self.assertTrue(self.wait_for(lambda: sent() == 2))
            self.assertTrue(self.wait_for(lambda: "reply_acknowledged" in [item[0] for item in statuses]))
            self.assertTrue(adapter.stop())

    def test_endpoint_allowlist(self):
        self.assertTrue(validate_ws_url("ws://127.0.0.1:3001/"))
        for value in (
            "ws://localhost:3001/", "ws://192.168.1.2:3001/",
            "wss://127.0.0.1:3001/", "ws://127.0.0.1:3002/",
            "ws://user@127.0.0.1:3001/", "ws://127.0.0.1:3001/?token=x",
            "ws://[::1]:3001/", "ws://127.0.0.1:3001/path",
        ):
            self.assertFalse(validate_ws_url(value), value)

    def test_real_connector_is_loopback_direct_and_rejects_redirects(self):
        connector = _connect_local("ws://127.0.0.1:3001/", "memory-only-token")
        self.assertIsNone(connector.proxy)
        self.assertEqual(
            connector.additional_headers["Authorization"],
            "Bearer memory-only-token",
        )
        marker = RuntimeError("redirect marker")
        self.assertIs(connector.process_redirect(marker), marker)

    def test_constructor_does_not_connect_or_create_core(self):
        calls = []
        connect_calls = []
        connector = FakeConnector()
        adapter = OneBotAdapter(
            "ws://127.0.0.1:3001/", "test-token",
            lambda bot_id: (calls.append(bot_id), object())[1],
            connect_factory=lambda *_: (connect_calls.append(True), connector)[1],
        )
        self.assertEqual(calls, [])
        self.assertEqual(connect_calls, [])
        self.assertIsNone(adapter.verified_bot_id())
        self.assertTrue(adapter.start())
        self.assertTrue(self.wait_for(lambda: adapter.verified_bot_id() == "123456"))
        adapter.stop()

    def test_only_friend_private_plain_text_for_confirmed_bot_reaches_core(self):
        adapter, connector, core, factories, _ = self.make_adapter()
        self.assertTrue(adapter.start())
        self.assertTrue(self.wait_for(lambda: adapter.verified_bot_id() is not None))
        base = {
            "post_type": "message", "message_type": "private",
            "sub_type": "friend", "self_id": 123456,
            "user_id": 654321, "message_id": 7,
            "message": [{"type": "text", "data": {"text": "你好"}}],
        }
        invalid = [
            dict(base, message_type="group"),
            dict(base, sub_type="group"),
            dict(base, self_id=999),
            dict(base, user_id=123456),
            dict(base, message=[{"type": "image", "data": {"file": "x"}}]),
            dict(base, message=[{"type": "text", "data": {"text": " "}},
                                {"type": "image", "data": {"file": "x"}}]),
            dict(base, message_id="bad id"),
        ]
        for event in invalid:
            connector.ws.inject(event)
        self.assertTrue(self.wait_for(lambda: connector.ws.incoming.empty()))
        time.sleep(0.05)
        self.assertEqual(core.calls, [])

        connector.ws.inject(base)
        self.assertTrue(self.wait_for(lambda: len(core.finalized) == 1))
        self.assertEqual(factories, ["123456"])
        self.assertEqual(core.calls, [("654321", "7", "你好")])
        self.assertEqual(core.finalized, [("fake-delivery-token", True)])
        send = [item for item in connector.ws.sent
                if item["action"] == "send_private_msg"]
        self.assertEqual(len(send), 1)
        self.assertEqual(send[0]["params"]["message"], [
            {"type": "text", "data": {"text": "好的。"}}
        ])
        adapter.stop()

    def test_duplicate_event_is_processed_once_and_stop_is_idempotent(self):
        adapter, connector, core, _, _ = self.make_adapter()
        adapter.start()
        self.assertTrue(self.wait_for(lambda: adapter.verified_bot_id() is not None))
        event = {
            "post_type": "message", "message_type": "private",
            "sub_type": "friend", "self_id": "123456",
            "user_id": "654321", "message_id": "duplicate-1",
            "message": "hello",
        }
        connector.ws.inject(event)
        self.assertTrue(self.wait_for(lambda: len(core.finalized) == 1))
        connector.ws.inject(event)
        time.sleep(0.05)
        self.assertEqual(len(core.calls), 1)
        adapter.stop()
        adapter.stop()
        before = len(connector.ws.sent)
        connector.ws.inject(event)
        time.sleep(0.05)
        self.assertEqual(len(connector.ws.sent), before)
        self.assertIsNone(adapter.verified_bot_id())

    def test_cancelled_send_is_marked_unknown_without_resend(self):
        adapter, connector, core, _, statuses = self.make_adapter(ack=False)
        adapter.start()
        self.assertTrue(self.wait_for(lambda: adapter.verified_bot_id() is not None))
        event = {
            "post_type": "message", "message_type": "private",
            "sub_type": "friend", "self_id": 123456,
            "user_id": 654321, "message_id": 10, "message": "hello",
        }
        connector.ws.inject(event)
        self.assertTrue(self.wait_for(lambda: connector.ws.sent and
                                      connector.ws.sent[-1]["action"] == "send_private_msg"))
        # No ack means the outcome is still unknown while the request waits.
        time.sleep(0.05)
        self.assertEqual(core.finalized, [])
        self.assertEqual(core.unknown, [])
        sent_count = len([x for x in connector.ws.sent
                          if x["action"] == "send_private_msg"])

        self.assertTrue(adapter.stop())
        # Cancellation may already have put the request on the wire, so the
        # reply is neither delivered nor failed; the local recovery path opens.
        self.assertEqual(core.finalized, [])
        self.assertEqual(core.unknown, ["fake-delivery-token"])
        self.assertEqual(len([x for x in connector.ws.sent
                              if x["action"] == "send_private_msg"]), sent_count)
        status_codes = [code for code, _ in statuses]
        self.assertIn("disconnected", status_codes)
        # A cancelled send is never announced as a safe stop.
        self.assertNotIn("stopped", status_codes)
        self.assertTrue(adapter.is_quiescent())

        # Replaying the same event can never resend it.
        connector.ws.inject(event)
        time.sleep(0.05)
        self.assertEqual(len([x for x in connector.ws.sent
                              if x["action"] == "send_private_msg"]), sent_count)
        self.assertEqual(core.unknown, ["fake-delivery-token"])

    def test_send_exception_is_marked_unknown_and_not_retried(self):
        adapter, connector, core, _, statuses = self.make_adapter(send_error=True)
        adapter.start()
        self.assertTrue(self.wait_for(lambda: adapter.verified_bot_id() is not None))
        event = {
            "post_type": "message", "message_type": "private",
            "sub_type": "friend", "self_id": 123456,
            "user_id": 654321, "message_id": 12, "message": "hello",
        }
        connector.ws.inject(event)
        self.assertTrue(self.wait_for(
            lambda: core.unknown == ["fake-delivery-token"]
            and "delivery_unknown" in [code for code, _ in statuses]
        ))
        self.assertEqual(core.finalized, [])
        sent_count = len([x for x in connector.ws.sent
                          if x["action"] == "send_private_msg"])
        connector.ws.inject(event)
        time.sleep(0.05)
        self.assertEqual(len([x for x in connector.ws.sent
                              if x["action"] == "send_private_msg"]), sent_count)
        self.assertEqual(core.unknown, ["fake-delivery-token"])
        self.assertTrue(adapter.stop())

    def test_timeout_exception_is_marked_unknown_not_failed(self):
        adapter, connector, core, _, statuses = self.make_adapter()
        adapter.start()
        self.assertTrue(self.wait_for(lambda: adapter.verified_bot_id() is not None))
        original_api = adapter._api

        async def flaky_api(ws, pending_api, action, params):
            if action == "send_private_msg":
                raise asyncio.TimeoutError()
            return await original_api(ws, pending_api, action, params)

        adapter._api = flaky_api
        connector.ws.inject({
            "post_type": "message", "message_type": "private",
            "sub_type": "friend", "self_id": 123456,
            "user_id": 654321, "message_id": 13, "message": "hello",
        })
        self.assertTrue(self.wait_for(
            lambda: core.unknown == ["fake-delivery-token"]
            and "delivery_unknown" in [code for code, _ in statuses]
        ))
        self.assertEqual(core.finalized, [])
        self.assertTrue(adapter.stop())

    def test_confirmed_api_failure_finalizes_chat_false(self):
        adapter, connector, core, _, statuses = self.make_adapter(ack="negative")
        adapter.start()
        self.assertTrue(self.wait_for(lambda: adapter.verified_bot_id() is not None))
        connector.ws.inject({
            "post_type": "message", "message_type": "private",
            "sub_type": "friend", "self_id": 123456,
            "user_id": 654321, "message_id": 11, "message": "hello",
        })
        self.assertTrue(self.wait_for(lambda: len(core.finalized) == 1))
        self.assertEqual(core.finalized, [("fake-delivery-token", False)])
        self.assertEqual(core.unknown, [])
        codes = [code for code, _ in statuses]
        self.assertIn("delivery_rejected", codes)
        self.assertNotIn("reply_acknowledged", codes)
        adapter.stop()

    def test_processing_and_confirmed_ack_are_reported_for_a_chat_reply(self):
        core = FakeCore(diagnostics=["model_processing"])
        adapter, connector, core, _, statuses = self.make_adapter(core=core)
        adapter.start()
        self.assertTrue(self.wait_for(lambda: adapter.verified_bot_id() is not None))
        user_text = "synthetic-processing-content"
        connector.ws.inject({
            "post_type": "message", "message_type": "private",
            "sub_type": "friend", "self_id": 123456,
            "user_id": 654321, "message_id": 31, "message": user_text,
        })
        self.assertTrue(self.wait_for(lambda: len(core.finalized) == 1))
        self.assertEqual(core.finalized, [("fake-delivery-token", True)])
        codes = [code for code, _ in statuses]
        self.assertIn("model_processing", codes)
        self.assertIn("reply_acknowledged", codes)
        self.assertLess(
            codes.index("model_processing"), codes.index("reply_acknowledged")
        )
        self.assertNotIn("delivery_rejected", codes)
        self.assertNotIn("model_failed", codes)
        # Status text is fixed and never echoes the message or reply.
        rendered = repr(statuses)
        self.assertNotIn(user_text, rendered)
        self.assertNotIn("好的。", rendered)
        self.assertNotIn("654321", rendered)
        self.assertNotIn("fake-delivery-token", rendered)
        adapter.stop()

    def test_conversation_error_codes_map_to_fixed_messages_without_leak(self):
        known_codes = [
            "model_failed_authentication",
            "model_failed_school_login",
            "model_failed_busy",
            "model_failed_unavailable", "model_prepare_failed",
            "model_failed_conversation", "model_failed_runtime", "model_failed_value",
            "model_failed_type", "model_failed_lookup", "model_failed_attribute", "model_failed_os",
            "model_http_400", "model_http_404", "model_http_500", "model_http_502",
            "model_http_1xx", "model_http_2xx", "model_http_3xx", "model_http_4xx", "model_http_5xx",
        ]
        unknown = "SYNTHETIC-UNKNOWN-DIAGNOSTIC-SECRET"
        core = FakeCore(diagnostics=known_codes + [unknown])
        adapter, connector, core, _, statuses = self.make_adapter(core=core)
        adapter.start()
        self.assertTrue(self.wait_for(lambda: adapter.verified_bot_id() is not None))
        user_text = "synthetic-conversation-status-content"
        connector.ws.inject({
            "post_type": "message", "message_type": "private",
            "sub_type": "friend", "self_id": 123456,
            "user_id": 654321, "message_id": 37, "message": user_text,
        })
        self.assertTrue(self.wait_for(
            lambda: "reply_acknowledged" in [code for code, _ in statuses]
        ))
        emitted = [code for code, _ in statuses]
        for code in known_codes:
            self.assertIn(code, emitted)
        # Unknown codes from any other core implementation are dropped, never
        # rendered to the local UI.
        self.assertNotIn(unknown, emitted)
        messages = dict(statuses)
        self.assertEqual(
            messages["model_failed_authentication"],
            _MODEL_FAILED_AUTHENTICATION_TEXT,
        )
        self.assertEqual(
            messages["model_failed_school_login"],
            _MODEL_FAILED_SCHOOL_LOGIN_TEXT,
        )
        self.assertEqual(
            messages["model_failed_busy"], _MODEL_FAILED_BUSY_TEXT
        )
        self.assertEqual(
            messages["model_failed_unavailable"],
            _MODEL_FAILED_UNAVAILABLE_TEXT,
        )
        rendered = repr(statuses)
        for value in (unknown, user_text, "654321", "123456",
                      "fake-delivery-token", "37"):
            self.assertNotIn(value, rendered)
        adapter.stop()

    def test_nonchat_unknown_delivery_does_not_offer_recovery(self):
        for kind, failure in (("error", "timeout"), ("bound", "malformed")):
            with self.subTest(kind=kind, failure=failure):
                core = FakeCore(reply=SimpleNamespace(text="synthetic notice", kind=kind,
                                                       delivery_token=None))
                adapter, connector, core, _, statuses = self.make_adapter(core=core)
                original_api = adapter._api
                async def uncertain_api(ws, pending, action, params):
                    if action == "send_private_msg":
                        if failure == "timeout":
                            raise asyncio.TimeoutError()
                        await original_api(ws, pending, action, params)
                        return {"status":"ok", "retcode":0, "data":{}}
                    return await original_api(ws, pending, action, params)
                adapter._api = uncertain_api
                adapter.start()
                self.assertTrue(self.wait_for(lambda: adapter.verified_bot_id() is not None))
                connector.ws.inject({"post_type":"message", "message_type":"private",
                                     "sub_type":"friend", "self_id":123456,
                                     "user_id":654321, "message_id":69, "message":"hello"})
                self.assertTrue(self.wait_for(lambda: any(code == "delivery_unknown"
                                                          for code, _ in statuses)))
                text = [message for code, message in statuses if code == "delivery_unknown"][-1]
                self.assertIn("查看 QQ 私聊", text)
                self.assertNotIn("恢复", text)
                self.assertEqual(core.unknown, [])
                self.assertEqual(core.finalized, [])
                self.assertTrue(adapter.stop())

    def test_error_fallback_ack_is_never_reported_as_a_model_reply(self):
        fallback = SimpleNamespace(
            text="本机模型暂时无法回应，请稍后再试。",
            kind="error", delivery_token=None,
        )
        core = FakeCore(
            reply=fallback, diagnostics=["model_processing", "model_failed"]
        )
        adapter, connector, core, _, statuses = self.make_adapter(core=core)
        adapter.start()
        self.assertTrue(self.wait_for(lambda: adapter.verified_bot_id() is not None))
        connector.ws.inject({
            "post_type": "message", "message_type": "private",
            "sub_type": "friend", "self_id": 123456,
            "user_id": 654321, "message_id": 32, "message": "会失败的合成消息",
        })
        self.assertTrue(self.wait_for(
            lambda: any(x["action"] == "send_private_msg"
                        for x in connector.ws.sent)
        ))
        time.sleep(0.05)
        codes = [code for code, _ in statuses]
        self.assertIn("model_processing", codes)
        self.assertIn("model_failed", codes)
        # The fallback was acknowledged by QQ, but it is not a model reply.
        self.assertNotIn("reply_acknowledged", codes)
        self.assertNotIn("delivery_rejected", codes)
        self.assertEqual(core.finalized, [])
        adapter.stop()

    def test_invalid_chat_reply_is_reported_and_finalized_false(self):
        core = FakeCore(reply=SimpleNamespace(
            text="", kind="chat", delivery_token="invalid-token"
        ))
        adapter, connector, core, _, statuses = self.make_adapter(core=core)
        adapter.start()
        self.assertTrue(self.wait_for(lambda: adapter.verified_bot_id() is not None))
        connector.ws.inject({
            "post_type": "message", "message_type": "private",
            "sub_type": "friend", "self_id": 123456,
            "user_id": 654321, "message_id": 33, "message": "hello",
        })
        self.assertTrue(self.wait_for(
            lambda: core.finalized == [("invalid-token", False)]
        ))
        self.assertIn(
            "model_response_invalid", [code for code, _ in statuses]
        )
        self.assertFalse(any(x["action"] == "send_private_msg"
                             for x in connector.ws.sent))
        adapter.stop()

    def test_send_exception_text_never_appears_in_statuses(self):
        secret = "SYNTHETIC-EXCEPTION SECRET Authorization: Bearer hidden"
        adapter, connector, core, _, statuses = self.make_adapter(
            send_error=secret
        )
        adapter.start()
        self.assertTrue(self.wait_for(lambda: adapter.verified_bot_id() is not None))
        connector.ws.inject({
            "post_type": "message", "message_type": "private",
            "sub_type": "friend", "self_id": 123456,
            "user_id": 654321, "message_id": 34,
            "message": "synthetic-secret-message-content",
        })
        self.assertTrue(self.wait_for(
            lambda: core.unknown == ["fake-delivery-token"]
            and "delivery_unknown" in [code for code, _ in statuses]
        ))
        rendered = repr(statuses)
        for value in (secret, "SYNTHETIC-EXCEPTION", "Authorization",
                      "Bearer", "hidden", "synthetic-secret-message-content",
                      "654321", "123456", "34", "fake-delivery-token"):
            self.assertNotIn(value, rendered)
        adapter.stop()

    def test_acknowledgement_payload_never_appears_in_statuses(self):
        secret = "SYNTHETIC-ACK-PAYLOAD-SECRET"
        adapter, connector, core, _, statuses = self.make_adapter(
            ack_secret=secret
        )
        adapter.start()
        self.assertTrue(self.wait_for(lambda: adapter.verified_bot_id() is not None))
        connector.ws.inject({
            "post_type": "message", "message_type": "private",
            "sub_type": "friend", "self_id": 123456,
            "user_id": 654321, "message_id": 35, "message": "hello",
        })
        self.assertTrue(self.wait_for(
            lambda: "reply_acknowledged" in [code for code, _ in statuses]
        ))
        self.assertNotIn(secret, repr(statuses))
        adapter.stop()

    def test_core_without_diagnostic_hook_still_replies(self):
        core = LegacyCore()
        adapter, connector, core, _, statuses = self.make_adapter(core=core)
        adapter.start()
        self.assertTrue(self.wait_for(lambda: adapter.verified_bot_id() is not None))
        connector.ws.inject({
            "post_type": "message", "message_type": "private",
            "sub_type": "friend", "self_id": 123456,
            "user_id": 654321, "message_id": 36, "message": "hello",
        })
        self.assertTrue(self.wait_for(
            lambda: core.finalized == [("legacy-token", True)]
        ))
        self.assertNotIn(
            "model_processing", [code for code, _ in statuses]
        )
        self.assertTrue(any(x["action"] == "send_private_msg"
                            for x in connector.ws.sent))
        adapter.stop()

    def test_real_core_model_exception_is_diagnosed_without_leaking(self):
        secret = "SYNTHETIC-MODEL-EXCEPTION Authorization: Bearer hidden-body"
        user_text = "synthetic-real-core-message"

        def failing_request(endpoint, model, messages, api_key):
            raise RuntimeError(secret)

        with tempfile.TemporaryDirectory() as tmp:
            real_core = QQCore(Path(tmp), "synthetic-bot",
                               request_reply=failing_request)
            code = real_core.begin_pairing()
            bound = real_core.handle_private("654321", "bind-1", f"绑定 {code}")
            self.assertEqual(bound.kind, "bound")
            self.assertEqual(real_core._turns, [])

            connector = FakeConnector()
            statuses = []
            adapter = OneBotAdapter(
                "ws://127.0.0.1:3001/", "test-token",
                lambda bot_id: real_core,
                lambda item_code, message: statuses.append((item_code, message)),
                connect_factory=lambda *_: connector,
            )
            adapter.start()
            self.assertTrue(
                self.wait_for(lambda: adapter.verified_bot_id() is not None)
            )
            connector.ws.inject({
                "post_type": "message", "message_type": "private",
                "sub_type": "friend", "self_id": 123456,
                "user_id": 654321, "message_id": 41, "message": user_text,
            })
            self.assertTrue(self.wait_for(
                lambda: "model_failed_runtime" in [code for code, _ in statuses]
            ))
            self.assertTrue(self.wait_for(
                lambda: any(x["action"] == "send_private_msg"
                            for x in connector.ws.sent)
            ))
            time.sleep(0.05)

            codes = [code for code, _ in statuses]
            self.assertIn("model_processing", codes)
            self.assertIn("model_failed_runtime", codes)
            # QQ acknowledged the generic fallback, but no model reply was
            # produced and no successful turn may be recorded.
            self.assertNotIn("reply_acknowledged", codes)
            self.assertEqual(real_core._turns, [])
            self.assertIsNone(real_core._pending)

            rendered = repr(statuses)
            for value in (secret, "SYNTHETIC-MODEL-EXCEPTION",
                          "Authorization", "Bearer", "hidden-body",
                          user_text, "654321", "123456"):
                self.assertNotIn(value, rendered)
            self.assertTrue(adapter.stop())
            self.assertTrue(adapter.is_quiescent())

    def test_reply_too_long_for_onebot_is_finalized_false(self):
        core_reply = SimpleNamespace(
            text="长" * 12001, kind="chat", delivery_token="long-token"
        )
        connector = FakeConnector()
        core = FakeCore(reply=core_reply)
        adapter = OneBotAdapter(
            "ws://127.0.0.1:3001/", "test-token",
            lambda bot_id: core, connect_factory=lambda *_: connector,
        )
        adapter.start()
        self.assertTrue(self.wait_for(lambda: adapter.verified_bot_id() is not None))
        connector.ws.inject({
            "post_type": "message", "message_type": "private",
            "sub_type": "friend", "self_id": 123456,
            "user_id": 654321, "message_id": 14, "message": "hello",
        })
        self.assertTrue(self.wait_for(lambda: core.finalized == [("long-token", False)]))
        self.assertEqual(core.unknown, [])
        self.assertFalse(any(x["action"] == "send_private_msg"
                             for x in connector.ws.sent))
        self.assertTrue(adapter.stop())

    def test_finalize_failure_is_reported_and_never_retries_the_qq_send(self):
        connector = FakeConnector()
        core = FinalizeFailCore()
        statuses = []
        adapter = OneBotAdapter(
            "ws://127.0.0.1:3001/", "test-token",
            lambda bot_id: core,
            lambda code, message: statuses.append((code, message)),
            connect_factory=lambda *_: connector,
        )
        adapter.start()
        self.assertTrue(self.wait_for(lambda: adapter.verified_bot_id() is not None))
        connector.ws.inject({
            "post_type": "message", "message_type": "private",
            "sub_type": "friend", "self_id": 123456,
            "user_id": 654321, "message_id": 21, "message": "hello",
        })
        self.assertTrue(self.wait_for(
            lambda: "storage_error" in [code for code, _ in statuses]
        ))
        self.assertEqual(
            core.finalized,
            [("fake-delivery-token", True)] * 3,
        )
        sends = [x for x in connector.ws.sent
                 if x["action"] == "send_private_msg"]
        self.assertEqual(len(sends), 1)
        self.assertNotIn("reply_acknowledged", [code for code, _ in statuses])
        self.assertEqual(core.unknown, [])
        self.assertTrue(adapter.stop())

    def test_stop_is_bounded_and_blocks_reconnect_until_worker_ends(self):
        connector = FakeConnector()
        core = BlockingCore()
        factories = []
        statuses = []

        def factory(bot_id):
            factories.append(bot_id)
            return core

        adapter = OneBotAdapter(
            "ws://127.0.0.1:3001/", "test-token", factory,
            lambda code, message: statuses.append((code, message)),
            connect_factory=lambda *_: connector,
        )
        self.assertTrue(adapter.start())
        self.assertTrue(self.wait_for(lambda: adapter.verified_bot_id() is not None))
        connector.ws.inject({
            "post_type": "message", "message_type": "private",
            "sub_type": "friend", "self_id": 123456,
            "user_id": 654321, "message_id": 20, "message": "slow",
        })
        self.assertTrue(core.entered.wait(2))

        # The model call cannot be cancelled.  stop() must return within its
        # bound instead of waiting for the model, and must not claim success.
        started = time.monotonic()
        self.assertFalse(adapter.stop(timeout=0.15))
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertFalse(adapter.is_quiescent())
        # No second core instance may be created while the old call is live.
        self.assertFalse(adapter.start())
        self.assertEqual(factories, ["123456"])
        self.assertEqual(core.finalized, [])
        self.assertFalse(any(x["action"] == "send_private_msg"
                             for x in connector.ws.sent))
        self.assertNotIn("stopped", [code for code, _ in statuses])
        self.assertIn("disconnected", [code for code, _ in statuses])

        core.release.set()
        self.assertTrue(self.wait_for(lambda: adapter.is_quiescent(), timeout=3.0))
        # No send happened, so the abandoned reply is exactly not delivered.
        self.assertEqual(core.finalized, [("fake-delivery-token", False)])
        self.assertEqual(core.unknown, [])
        self.assertFalse(any(x["action"] == "send_private_msg"
                             for x in connector.ws.sent))

        # Only now is a new connection (and a new core) allowed.
        self.assertTrue(adapter.start())
        self.assertTrue(self.wait_for(lambda: len(factories) == 2))
        self.assertTrue(core.entered.is_set())
        self.assertTrue(adapter.stop(timeout=1.5))
        self.assertTrue(adapter.is_quiescent())


if __name__ == "__main__":
    unittest.main()
