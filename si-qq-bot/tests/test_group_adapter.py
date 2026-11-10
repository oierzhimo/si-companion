"""Offline fake-OneBot tests for the explicit group-mention path.

The fake WebSocket and fake private core never touch QQ, NapCat, the model
service, credentials, or real user data.  The group ledger is real; only its
model requester and network peer are replaced.
"""
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

import group_core  # noqa: E402
from group_core import (  # noqa: E402
    GROUP_ERROR_TEXT, GROUP_SYSTEM_ADMIN, GroupChat,
)
from onebot_adapter import (  # noqa: E402
    OneBotAdapter, _group_message_text,
    _MEDIA_NOT_PROVIDED_MARKER, _MENTION_ONLY_MARKER,
)


BOT = '100012345'
GROUP = '300000001'
SENDER = '200054321'
OTHER = '200054322'
PRIVATE_SECRET = 'SECRET-PRIVATE-DATA-MUST-NOT-LEAK'


class Recorder:
    def __init__(self, reply='我在。'):
        self.reply = reply
        self.calls = []
        self.error = None

    def __call__(self, endpoint, model, messages, api_key):
        self.calls.append({
            'endpoint': endpoint,
            'model': model,
            'messages': [dict(item) for item in messages],
            'api_key': api_key,
        })
        if self.error is not None:
            raise self.error
        return self.reply


class FakePrivateCore:
    """Private surface with a private profile that must never reach a group."""

    profile = PRIVATE_SECRET

    def __init__(self):
        self.private_calls = []
        self.private_finalized = []
        self.unknown = []
        self.diagnostic_codes = []
        self._diagnostic_callback = None

    def set_diagnostic_callback(self, callback):
        self._diagnostic_callback = callback

    def is_administrator(self, account_id):
        return account_id == SENDER

    def handle_private(self, sender, message_id, text):
        self.private_calls.append((sender, message_id, text))
        for code in self.diagnostic_codes:
            callback = self._diagnostic_callback
            if callback is not None:
                callback(code)
        return SimpleNamespace(
            text='私聊回复。', kind='chat', delivery_token='private-token'
        )

    def finalize_reply(self, token, delivered):
        self.private_finalized.append((token, delivered))
        return True

    def mark_delivery_unknown(self, token):
        self.unknown.append(token)
        return True


class BlockingGroupCore:
    """Group ledger stand-in whose model call cannot be cancelled."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finalized = []
        self.unknown = []

    def set_diagnostic_callback(self, callback):
        self.callback = callback

    def handle_group(self, group_id, sender_id, message_id, text):
        self.entered.set()
        self.release.wait(10)
        return SimpleNamespace(
            text='群聊回复。', kind='group_chat',
            delivery_token='blocking-group-token',
        )

    def finalize_group(self, token, delivered):
        self.finalized.append((token, delivered))
        return True

    def mark_delivery_unknown(self, token):
        self.unknown.append(token)
        return True

    def local_status(self):
        return {'pending_recovery_required': False}


class GroupWS:
    """Fake OneBot endpoint; only loopback-style JSON is exchanged in memory."""

    def __init__(self, loop, ack='ok', gate=None, timeout_after_send=False):
        self.loop = loop
        self.ack = ack
        self.gate = gate
        self.timeout_after_send = timeout_after_send
        self.incoming = asyncio.Queue()
        self.sent = []
        self.group_sends = []
        self.private_sends = []

    async def send(self, raw):
        packet = json.loads(raw)
        self.sent.append(packet)
        action = packet['action']
        if action == 'get_login_info':
            response = {
                'status': 'ok', 'retcode': 0,
                'data': {'user_id': int(BOT)}, 'echo': packet['echo'],
            }
        elif action == 'send_group_msg':
            self.group_sends.append(packet['params'])
            if self.timeout_after_send:
                raise asyncio.TimeoutError('synthetic timeout')
            if self.gate is not None:
                await asyncio.get_running_loop().run_in_executor(
                    None, self.gate.wait, 5
                )
            if self.ack == 'ok':
                response = {
                    'status': 'ok', 'retcode': 0,
                    'data': {'message_id': 700}, 'echo': packet['echo'],
                }
            elif self.ack == 'failed':
                response = {
                    'status': 'failed', 'retcode': 1404,
                    'data': {}, 'echo': packet['echo'],
                }
            else:  # malformed success: no usable message id
                response = {
                    'status': 'ok', 'retcode': 0,
                    'data': {}, 'echo': packet['echo'],
                }
        elif action == 'send_private_msg':
            self.private_sends.append(packet['params'])
            response = {
                'status': 'ok', 'retcode': 0,
                'data': {'message_id': 701}, 'echo': packet['echo'],
            }
        else:
            response = {
                'status': 'ok', 'retcode': 0, 'data': {},
                'echo': packet['echo'],
            }
        await self.incoming.put(json.dumps(response))

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.incoming.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self):
        await self.incoming.put(None)

    def inject(self, packet):
        if not self.loop.is_closed():
            self.loop.call_soon_threadsafe(
                self.incoming.put_nowait, json.dumps(packet)
            )


class GroupConnector:
    def __init__(self, ack='ok', gate=None, timeout_after_send=False):
        self.ack = ack
        self.gate = gate
        self.timeout_after_send = timeout_after_send
        self.ws = None

    async def __aenter__(self):
        self.ws = GroupWS(
            asyncio.get_running_loop(), self.ack, self.gate,
            self.timeout_after_send,
        )
        return self.ws

    async def __aexit__(self, exc_type, exc, tb):
        await self.ws.close()


class StreamWS:
    """Finite fake stream used to exercise the reader without a connection."""

    def __init__(self, packets):
        self.packets = list(packets)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.packets:
            raise StopAsyncIteration
        return json.dumps(self.packets.pop(0))


def mention(text, account=BOT):
    return [
        {'type': 'at', 'data': {'qq': account}},
        {'type': 'text', 'data': {'text': text}},
    ]


def group_event(message_id=1, *, sender=SENDER, group=GROUP,
                segments=None, sub_type='normal', self_id=BOT):
    return {
        'post_type': 'message',
        'message_type': 'group',
        'sub_type': sub_type,
        'self_id': int(self_id),
        'group_id': int(group),
        'user_id': int(sender),
        'message_id': message_id,
        'message': mention('你好') if segments is None else segments,
    }


def private_event(message_id=1, *, sender=SENDER, text='私聊消息'):
    return {
        'post_type': 'message',
        'message_type': 'private',
        'sub_type': 'friend',
        'self_id': int(BOT),
        'user_id': int(sender),
        'message_id': message_id,
        'message': [{'type': 'text', 'data': {'text': text}}],
    }


class GroupAdapterTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.data = Path(self._temp.name)
        self.recorder = Recorder()
        self.private = FakePrivateCore()
        self.adapter = None
        self.connector = None
        self.chat = None

    def tearDown(self):
        if self.adapter is not None:
            self.adapter.stop(timeout=3.0)
        self._temp.cleanup()

    def start(self, *, ack='ok', enabled=True, gate=None,
              timeout_after_send=False, group_core=None):
        self.connector = GroupConnector(ack, gate, timeout_after_send)
        if group_core is None:
            self.chat = GroupChat(
                self.data, BOT, request_reply=self.recorder,
                is_admin=self.private.is_administrator,
            )
            group_core = self.chat
        holder = {'enabled': bool(enabled)}
        statuses = []
        self.statuses = statuses

        def status(code, message):
            statuses.append((code, message))

        self.adapter = OneBotAdapter(
            'ws://127.0.0.1:3001/', 'synthetic-token',
            lambda bot_id: self.private,
            status_callback=status,
            connect_factory=lambda *_: self.connector,
            group_factory=lambda bot_id, private: group_core,
            group_enabled=lambda: holder['enabled'],
        )
        self.adapter.start()
        self.assertTrue(self.wait_for(
            lambda: self.adapter.verified_bot_id() == BOT
        ))
        return holder

    def wait_for(self, predicate, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return predicate()

    def inject(self, packet):
        self.connector.ws.inject(packet)

    def state(self):
        path = self.data / group_core._STATE_FILENAME
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (PermissionError, FileNotFoundError):
            # Windows can briefly deny a concurrent observer while the
            # worker atomically replaces the file. The polling predicate
            # must retry; a persistent failure still fails at its deadline.
            return None

    def wait_status(self, code, timeout=3.0):
        return self.wait_for(
            lambda: any(item[0] == code for item in self.statuses),
            timeout,
        )

    # -- filtering and the switch -------------------------------------

    def test_group_mention_parsing_requires_an_array_at_segment(self):
        self.assertEqual(_group_message_text(mention(' 你好 '), BOT), '你好')
        self.assertIsNone(_group_message_text('@BOT 你好', BOT))
        self.assertIsNone(_group_message_text(mention('@all', 'all'), BOT))
        self.assertIsNone(_group_message_text(
            [{'type': 'at', 'data': {'qq': OTHER}},
             {'type': 'text', 'data': {'text': '你好'}}], BOT
        ))
        # A standalone target mention is a valid targeted message, not an
        # ignored one: the fixed marker lets the model respond naturally.
        self.assertEqual(
            _group_message_text(
                [{'type': 'at', 'data': {'qq': BOT}}], BOT
            ),
            _MENTION_ONLY_MARKER,
        )
        # Media-only target mentions are valid too, and explicitly tell the
        # model that the media itself was not provided.
        prompt = _group_message_text(
            [{'type': 'at', 'data': {'qq': BOT}},
             {'type': 'image', 'data': {
                 'file': 'synthetic.jpg',
                 'url': 'https://example.invalid/synthetic.jpg',
             }}], BOT
        )
        self.assertEqual(
            prompt, _MENTION_ONLY_MARKER + ' ' + _MEDIA_NOT_PROVIDED_MARKER
        )
        self.assertNotIn('synthetic.jpg', prompt)
        self.assertNotIn('example.invalid', prompt)
        # Native text plus a target mention still works, with the media note
        # appended and no original media data copied.
        prompt = _group_message_text(
            [{'type': 'at', 'data': {'qq': BOT}},
             {'type': 'image', 'data': {
                 'file': 'synthetic.jpg',
                 'url': 'https://example.invalid/synthetic.jpg',
             }},
             {'type': 'text', 'data': {'text': '看图'}}], BOT
        )
        self.assertEqual(prompt, '看图 ' + _MEDIA_NOT_PROVIDED_MARKER)
        self.assertNotIn('synthetic.jpg', prompt)
        self.assertNotIn('example.invalid', prompt)

    def test_non_target_group_events_never_reach_model_or_storage(self):
        self.start()
        events = [
            group_event(1, segments=[
                {'type': 'at', 'data': {'qq': OTHER}},
                {'type': 'text', 'data': {'text': '不是叫我'}}]),
            group_event(2, segments=[
                {'type': 'at', 'data': {'qq': 'all'}},
                {'type': 'text', 'data': {'text': '@全体'}}]),
            group_event(3, segments=[
                {'type': 'text', 'data': {'text': '@' + BOT + ' 假提及'}}]),
            group_event(4, segments='@' + BOT + ' 字符串假提及'),
            group_event(5, sender=BOT),
            # Media without a target mention is still unrelated traffic.
            group_event(6, segments=[
                {'type': 'at', 'data': {'qq': OTHER}},
                {'type': 'image', 'data': {'file': 'other.jpg'}}]),
            group_event(7, segments=[
                {'type': 'at', 'data': {'qq': 'all'}},
                {'type': 'image', 'data': {'file': 'all.jpg'}}]),
            group_event(8, sub_type='notice'),
        ]
        for event in events:
            self.inject(event)
        # Allow the reader and any accidental worker to run.
        self.wait_for(lambda: False, timeout=0.6)
        self.assertEqual(self.recorder.calls, [])
        self.assertEqual(self.connector.ws.group_sends, [])
        self.assertIsNone(self.state())

    def test_switch_off_drops_mentions_and_takes_effect_without_reconnect(self):
        holder = self.start(enabled=False)
        self.inject(group_event(1))
        self.wait_for(lambda: False, timeout=0.6)
        self.assertEqual(self.recorder.calls, [])
        self.assertEqual(self.connector.ws.group_sends, [])
        self.assertIsNone(self.state())

        holder['enabled'] = True
        self.inject(group_event(2))
        self.assertTrue(self.wait_for(
            lambda: len(self.connector.ws.group_sends) == 1
        ))
        self.assertEqual(len(self.recorder.calls), 1)

    # -- standalone mention and unsupported media ---------------------

    def test_mention_only_event_gets_synthetic_prompt_and_is_replied(self):
        self.start()
        self.inject(group_event(30, segments=[
            {'type': 'at', 'data': {'qq': BOT}},
        ]))
        self.assertTrue(self.wait_for(
            lambda: len(self.connector.ws.group_sends) == 1
        ))
        self.assertTrue(self.wait_for(
            lambda: (self.state() or {}).get('pending', 'missing') is None
        ))

        call = self.recorder.calls[0]
        self.assertEqual(call['messages'][-1], {
            'role': 'user', 'content': _MENTION_ONLY_MARKER,
        })
        self.assertEqual(self.connector.ws.group_sends[0]['message'], [
            {'type': 'at', 'data': {'qq': SENDER}},
            {'type': 'text', 'data': {'text': ' 我在。'}},
        ])
        state = self.state()
        turns = list(state['pairs'].values())[0]['turns']
        self.assertEqual(turns[0]['user'], _MENTION_ONLY_MARKER)

    def test_media_only_event_gets_fixed_not_provided_marker(self):
        secrets = {
            'file': 'SECRET-media-file.jpg',
            'url': 'https://secret.invalid/private-image',
            'path': r'C:\secret\private-image.jpg',
        }
        self.start()
        self.inject(group_event(31, segments=[
            {'type': 'at', 'data': {'qq': BOT}},
            {'type': 'image', 'data': dict(secrets)},
        ]))
        self.assertTrue(self.wait_for(
            lambda: len(self.connector.ws.group_sends) == 1
        ))
        self.assertTrue(self.wait_for(
            lambda: (self.state() or {}).get('pending', 'missing') is None
        ))

        content = self.recorder.calls[0]['messages'][-1]['content']
        self.assertIn(_MENTION_ONLY_MARKER, content)
        self.assertIn('本轮含同一发送者', content)
        for value in secrets.values():
            self.assertNotIn(value, content)
            self.assertNotIn(
                value, json.dumps(self.connector.ws.sent, ensure_ascii=False)
            )
        raw_state = (self.data / group_core._STATE_FILENAME).read_text(
            encoding='utf-8'
        )
        for value in secrets.values():
            self.assertNotIn(value, raw_state)

    def test_text_plus_media_keeps_native_text_and_adds_media_marker(self):
        self.start()
        self.inject(group_event(32, segments=[
            {'type': 'at', 'data': {'qq': BOT}},
            {'type': 'image', 'data': {
                'file': 'SECRET-image.jpg',
                'url': 'https://secret.invalid/image',
            }},
            {'type': 'text', 'data': {'text': '这张图里是什么？'}},
        ]))
        self.assertTrue(self.wait_for(
            lambda: len(self.connector.ws.group_sends) == 1
        ))
        content = self.recorder.calls[0]['messages'][-1]['content']
        self.assertTrue(content.startswith('这张图里是什么？'))
        self.assertIn('本轮含同一发送者', content)
        self.assertNotIn('SECRET-image.jpg', content)
        self.assertNotIn('secret.invalid', content)

    def test_audio_only_target_mention_is_not_silently_dropped(self):
        self.start()
        self.inject(group_event(33, segments=[
            {'type': 'at', 'data': {'qq': BOT}},
            {'type': 'record', 'data': {'file': 'SECRET-audio.amr'}},
        ]))
        self.assertTrue(self.wait_for(
            lambda: len(self.connector.ws.group_sends) == 1
        ))
        content = self.recorder.calls[0]['messages'][-1]['content']
        self.assertIn(_MENTION_ONLY_MARKER, content)
        self.assertIn('本轮含同一发送者', content)
        self.assertNotIn('SECRET-audio.amr', content)

    # -- diagnostics ---------------------------------------------------

    def test_group_model_diagnostics_are_prefixed(self):
        self.start()
        self.inject(group_event(34))
        self.assertTrue(self.wait_status('model_processing'))
        messages = [
            message for code, message in self.statuses
            if code == 'model_processing'
        ]
        self.assertTrue(messages)
        self.assertTrue(all(
            message.startswith('群聊：') for message in messages
        ))
        self.assertIn(
            '群聊：已收到消息，正在请求本机模型。', messages
        )

    def test_private_model_diagnostics_keep_existing_unprefixed_text(self):
        self.private.diagnostic_codes = ['model_processing']
        self.start()
        self.inject(private_event(35))
        self.assertTrue(self.wait_for(
            lambda: len(self.connector.ws.private_sends) == 1
        ))
        self.assertTrue(self.wait_status('model_processing'))
        messages = [
            message for code, message in self.statuses
            if code == 'model_processing'
        ]
        self.assertIn('已收到消息，正在请求本机模型。', messages)
        self.assertFalse(any(
            message.startswith('群聊：') for message in messages
        ))

    def test_stale_group_diagnostic_after_stop_and_reconnect_is_dropped(self):
        self.start()
        # ``verified_bot_id`` becomes visible just before the diagnostic hook
        # is installed, so wait for the hook itself rather than racing it.
        self.assertTrue(self.wait_for(
            lambda: self.chat._diagnostic_callback is not None
        ))
        old_callback = self.chat._diagnostic_callback
        old_statuses = self.statuses
        self.assertIsNotNone(old_callback)
        self.assertTrue(self.adapter.stop(timeout=3.0))
        self.adapter = None
        before = list(old_statuses)

        # Reconnect through a fresh adapter/ledger, then deliver an old
        # callback.  It must not reach any local status line.
        self.start()
        old_callback('model_failed')
        time.sleep(0.1)
        self.assertEqual(old_statuses, before)
        self.assertFalse(any(
            '群聊：本机模型请求失败。' == message
            for _code, message in self.statuses
        ))

    def test_diagnostic_from_a_foreign_core_is_rejected(self):
        self.start()
        before = list(self.statuses)
        # Neither the private core nor the current group ledger.
        self.adapter._core_diagnostic(object(), 'model_failed')
        time.sleep(0.05)
        self.assertEqual(self.statuses, before)

    # -- confirmed delivery -------------------------------------------

    def test_mention_is_replied_once_with_at_sender_and_group_id(self):
        self.start()
        self.inject(group_event(11, segments=mention('今天怎么样？')))
        self.assertTrue(self.wait_for(
            lambda: len(self.connector.ws.group_sends) == 1
        ))
        params = self.connector.ws.group_sends[0]
        self.assertEqual(params['group_id'], GROUP)
        self.assertEqual(params['message'], [
            {'type': 'at', 'data': {'qq': SENDER}},
            {'type': 'text', 'data': {'text': ' 我在。'}},
        ])
        self.assertTrue(self.wait_status('group_reply_acknowledged'))

        self.assertEqual(len(self.recorder.calls), 1)
        call = self.recorder.calls[0]
        self.assertEqual(call['messages'][1]['content'], GROUP_SYSTEM_ADMIN)
        encoded = json.dumps(call['messages'], ensure_ascii=False)
        self.assertNotIn(PRIVATE_SECRET, encoded)
        self.assertNotIn(BOT, encoded)
        self.assertNotIn(GROUP, encoded)
        self.assertNotIn(SENDER, encoded)

        state = self.state()
        self.assertIsNone(state['pending'])
        self.assertEqual(len(state['pairs']), 1)
        turns = list(state['pairs'].values())[0]['turns']
        self.assertEqual(turns, [{
            'user': '今天怎么样？', 'assistant': '我在。',
        }])
        self.assertEqual(self.connector.ws.private_sends, [])

    def test_context_is_not_written_before_the_ack(self):
        gate = threading.Event()
        self.start(gate=gate)
        self.inject(group_event(12, segments=mention('等确认。')))
        self.assertTrue(self.wait_for(
            lambda: len(self.connector.ws.group_sends) == 1
        ))
        state = self.state()
        self.assertIsNotNone(state['pending'])
        self.assertEqual(state['pairs'], {})

        gate.set()
        self.assertTrue(self.wait_status('group_reply_acknowledged'))
        state = self.state()
        self.assertIsNone(state['pending'])
        self.assertEqual(len(state['pairs']), 1)

    def test_duplicate_message_id_calls_model_and_sends_once(self):
        self.start()
        event = group_event(13)
        self.inject(event)
        self.inject(event)
        self.assertTrue(self.wait_for(
            lambda: len(self.connector.ws.group_sends) == 1
        ))
        self.wait_for(lambda: False, timeout=0.5)
        self.assertEqual(len(self.connector.ws.group_sends), 1)
        self.assertEqual(len(self.recorder.calls), 1)

    def test_rejected_group_send_is_not_claimed_delivered(self):
        self.start(ack='failed')
        self.inject(group_event(20))
        self.assertTrue(self.wait_status('group_delivery_rejected'))
        # The rejection status is emitted before the finalize transaction; wait
        # for the durable clear rather than assuming it already happened.
        self.assertTrue(self.wait_for(
            lambda: (self.state() or {}).get('pending', 'missing') is None
        ))
        self.assertFalse(any(
            code == 'group_reply_acknowledged' for code, _ in self.statuses
        ))
        self.assertIn(
            '未确认', dict(self.statuses)['group_delivery_rejected']
        )
        state = self.state()
        self.assertIsNone(state['pending'])
        self.assertEqual(state['pairs'], {})

    def test_model_failure_sends_fixed_notice_without_internals(self):
        self.recorder.error = RuntimeError(PRIVATE_SECRET)
        self.start()
        self.inject(group_event(14))
        self.assertTrue(self.wait_for(
            lambda: len(self.connector.ws.group_sends) == 1
        ))
        params = self.connector.ws.group_sends[0]
        self.assertEqual(params['message'][1]['data']['text'], ' '+GROUP_ERROR_TEXT)
        self.assertNotIn(PRIVATE_SECRET, json.dumps(params, ensure_ascii=False))
        self.assertNotIn(
            PRIVATE_SECRET,
            json.dumps(self.connector.ws.sent, ensure_ascii=False),
        )
        self.assertNotIn(
            PRIVATE_SECRET, json.dumps(self.statuses, ensure_ascii=False)
        )

        self.inject(group_event(14))
        self.wait_for(lambda: False, timeout=0.5)
        self.assertEqual(len(self.connector.ws.group_sends), 1)
        self.assertEqual(len(self.recorder.calls), 1)
        state = self.state()
        self.assertIsNone(state['pending'])

    # -- unknown delivery and restart ---------------------------------

    def test_malformed_ack_is_unknown_and_never_resends(self):
        self.start(ack='malformed')
        self.inject(group_event(15))
        self.assertTrue(self.wait_status('group_delivery_unknown'))
        self.assertEqual(len(self.connector.ws.group_sends), 1)
        state = self.state()
        self.assertIsNotNone(state['pending'])

        self.wait_for(lambda: False, timeout=0.5)
        self.assertEqual(len(self.connector.ws.group_sends), 1)

        self.assertTrue(self.adapter.stop(timeout=3.0))
        self.adapter = None
        restarted = GroupChat(
            self.data, BOT, request_reply=self.recorder,
            is_admin=self.private.is_administrator,
        )
        self.assertTrue(
            restarted.local_status()['pending_recovery_required']
        )
        self.assertIsNone(restarted.handle_group(
            GROUP, SENDER, 'after-restart', '新的问题。'
        ))
        self.assertEqual(len(self.recorder.calls), 1)
        self.assertTrue(restarted.discard_uncertain_group_pending())
        # The original inbound message remains deduplicated: no replay.
        self.assertIsNone(restarted.handle_group(
            GROUP, SENDER, '15', '你好'
        ))
        self.assertEqual(len(self.recorder.calls), 1)
        self.assertEqual(len(self.connector.ws.group_sends), 1)

    def test_timeout_after_send_keeps_unresolved_pending(self):
        self.start(timeout_after_send=True)
        self.inject(group_event(16))
        self.assertTrue(self.wait_status('group_delivery_unknown'))
        self.assertEqual(len(self.connector.ws.group_sends), 1)
        state = self.state()
        self.assertIsNotNone(state['pending'])
        self.wait_for(lambda: False, timeout=0.5)
        self.assertEqual(len(self.connector.ws.group_sends), 1)

    # -- concurrency, stop, private compatibility ---------------------

    def test_group_model_work_does_not_block_private_chat(self):
        blocking = BlockingGroupCore()
        self.addCleanup(blocking.release.set)
        self.start(group_core=blocking)
        self.inject(group_event(17))
        self.assertTrue(blocking.entered.wait(3))
        self.inject(private_event(18))
        self.assertTrue(self.wait_for(
            lambda: len(self.connector.ws.private_sends) == 1
        ))
        self.assertEqual(len(self.connector.ws.group_sends), 0)

        # The stop cannot claim quiescence while the model worker still runs.
        self.assertFalse(self.adapter.stop(timeout=0.05))
        self.assertFalse(self.adapter.is_quiescent())
        self.assertFalse(self.adapter.start())
        blocking.release.set()
        self.assertTrue(self.wait_for(
            lambda: self.adapter.is_quiescent(), timeout=3.0
        ))
        self.assertTrue(self.adapter.stop(timeout=2.0))

    def test_private_path_is_unchanged_when_group_is_configured(self):
        self.start()
        self.inject(private_event(19, text='只走私聊'))
        self.assertTrue(self.wait_for(
            lambda: len(self.connector.ws.private_sends) == 1
        ))
        self.assertEqual(self.connector.ws.private_sends[0], {
            'user_id': SENDER,
            'message': [{'type': 'text', 'data': {'text': '私聊回复。'}}],
        })
        self.assertEqual(self.connector.ws.group_sends, [])
        self.assertEqual(self.recorder.calls, [])
        self.assertIsNone(self.state())

    # -- reader-side filtering and the bounded queue ------------------

    def test_reader_drops_unmentioned_before_queue_and_bounds_group_queue(self):
        adapter = OneBotAdapter(
            'ws://127.0.0.1:3001/', 'synthetic-token',
            lambda bot_id: None,
        )
        adapter._running = True
        adapter._bot_id = BOT
        adapter._group_core = object()
        adapter._group_enabled = lambda: True

        packets = []
        for index in range(40):
            packets.append(group_event(1000 + index, segments=[
                {'type': 'at', 'data': {'qq': OTHER}},
                {'type': 'text', 'data': {'text': '不是叫我'}}]))
        valid_ids = list(range(2000, 2030))
        for index in valid_ids:
            packets.append(group_event(index))

        async def run_reader():
            event_queue = asyncio.Queue(maxsize=64)
            group_queue = asyncio.Queue(maxsize=16)
            await adapter._reader(
                StreamWS(packets), {}, event_queue, group_queue
            )
            queued = []
            while not group_queue.empty():
                queued.append(group_queue.get_nowait())
            sentinel = event_queue.get_nowait()
            return queued, sentinel, event_queue.empty()

        queued, sentinel, private_empty = asyncio.run(run_reader())
        self.assertIsNone(sentinel)
        self.assertTrue(private_empty)
        self.assertEqual(len(queued), 16)
        self.assertEqual(
            [event['message_id'] for event in queued],
            valid_ids[:16],
        )


    def test_full_private_queue_does_not_block_send_acknowledgements(self):
        """API outcomes still arrive while application messages are backlogged."""
        async def run_reader(action):
            adapter = OneBotAdapter(
                'ws://127.0.0.1:3001/', 'synthetic-token',
                lambda _bot_id: self.private,
            )
            ws = GroupWS(asyncio.get_running_loop())
            event_queue = asyncio.Queue(maxsize=64)
            group_queue = asyncio.Queue(maxsize=16)
            pending_api = {}
            # These stand for a burst arriving while a slow model job prevents
            # the application consumer from taking any more private events.
            for message_id in range(80):
                await ws.incoming.put(json.dumps(private_event(message_id)))
            send = asyncio.create_task(adapter._api(
                ws, pending_api, action,
                {'group_id': GROUP, 'user_id': SENDER, 'message': []},
            ))
            await asyncio.sleep(0)  # register the waiter and queue its ACK
            reader = asyncio.create_task(adapter._reader(
                ws, pending_api, event_queue, group_queue,
            ))
            try:
                reply = await asyncio.wait_for(send, timeout=1.0)
                self.assertEqual(reply['status'], 'ok')
                self.assertEqual(reply['retcode'], 0)
                self.assertEqual(event_queue.qsize(), 64)
                self.assertFalse(pending_api)
            finally:
                reader.cancel()
                send.cancel()
                await asyncio.gather(reader, send, return_exceptions=True)

        for action in ('send_group_msg', 'send_private_msg'):
            with self.subTest(action=action):
                asyncio.run(run_reader(action))


if __name__ == '__main__':
    unittest.main()
