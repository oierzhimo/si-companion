"""Offline tests for long semantic reply splitting and sequential delivery.

Everything here is synthetic: a temporary data directory, a fake in-memory
OneBot peer, and an injected model requester.  No QQ message, NapCat socket,
model service, credential, or real user data is used.
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

import core  # noqa: E402
import group_core  # noqa: E402
import reply_chunks  # noqa: E402
from core import QQCore  # noqa: E402
from group_core import GroupChat  # noqa: E402
from onebot_adapter import (  # noqa: E402
    OneBotAdapter, _DELIVERY_PARTIAL_TEXT, _GROUP_DELIVERY_PARTIAL_TEXT,
    _REPLY_ACKNOWLEDGED_TEXT,
)
from reply_chunks import (  # noqa: E402
    HARD_LIMIT, MAX_CHUNK_CHARS, MAX_CHUNKS, MAX_REPLY_CHARS, SOFT_LIMIT,
    split_reply,
)


BOT = '100012345'
GROUP = '300000001'
SENDER = '200054321'
TOKEN = 'synthetic-long-delivery-token'


def long_chinese_reply(count=120):
    """Deterministic multi-sentence Chinese text longer than 2000 chars."""
    return ''.join(
        f'第{i}段内容需要完整保留，不能因为旧上限而被截断。' for i in range(count)
    )


class FakePrivateCore:
    def __init__(self, text='好的。', kind='chat', token=TOKEN):
        self.text = text
        self.kind = kind
        self.token = token
        self.calls = []
        self.finalized = []
        self.unknown = []
        self._diagnostic_callback = None

    def set_diagnostic_callback(self, callback):
        self._diagnostic_callback = callback

    def handle_private(self, sender, message_id, text):
        self.calls.append((sender, message_id, text))
        return SimpleNamespace(
            text=self.text, kind=self.kind, delivery_token=self.token
        )

    def finalize_reply(self, token, delivered):
        self.finalized.append((token, delivered))
        return True

    def mark_delivery_unknown(self, token):
        self.unknown.append(token)
        return True


class ScriptedWS:
    """Fake OneBot peer with a per-send outcome script.

    Outcomes: ``ok``, ``failed``, ``malformed``, ``timeout``, ``hang``.
    A missing outcome defaults to ``ok``.
    """

    def __init__(self, loop, script=()):
        self.loop = loop
        self.script = list(script)
        self.incoming = asyncio.Queue()
        self.sent = []
        self.private_sends = []
        self.group_sends = []
        self._counter = 900

    def _next_outcome(self):
        return self.script.pop(0) if self.script else 'ok'

    async def send(self, raw):
        packet = json.loads(raw)
        self.sent.append(packet)
        action = packet['action']
        if action == 'get_login_info':
            await self.incoming.put(json.dumps({
                'status': 'ok', 'retcode': 0,
                'data': {'user_id': int(BOT)}, 'echo': packet['echo'],
            }))
            return
        if action not in ('send_private_msg', 'send_group_msg'):
            await self.incoming.put(json.dumps({
                'status': 'ok', 'retcode': 0, 'data': {},
                'echo': packet['echo'],
            }))
            return

        if action == 'send_private_msg':
            self.private_sends.append(packet['params'])
        else:
            self.group_sends.append(packet['params'])

        outcome = self._next_outcome()
        if outcome == 'timeout':
            raise asyncio.TimeoutError('synthetic timeout')
        if outcome == 'hang':
            await asyncio.Event().wait()
        self._counter += 1
        if outcome == 'failed':
            response = {
                'status': 'failed', 'retcode': 1404,
                'data': {}, 'echo': packet['echo'],
            }
        elif outcome == 'malformed':
            response = {
                'status': 'ok', 'retcode': 0,
                'data': {}, 'echo': packet['echo'],
            }
        else:
            response = {
                'status': 'ok', 'retcode': 0,
                'data': {'message_id': self._counter},
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


class ScriptedConnector:
    def __init__(self, script=()):
        self.script = list(script)
        self.ws = None

    async def __aenter__(self):
        self.ws = ScriptedWS(asyncio.get_running_loop(), self.script)
        return self.ws

    async def __aexit__(self, exc_type, exc, tb):
        await self.ws.close()


def private_event(message_id=1, text='合成问题'):
    return {
        'post_type': 'message', 'message_type': 'private',
        'sub_type': 'friend', 'self_id': int(BOT),
        'user_id': int(SENDER), 'message_id': message_id,
        'message': [{'type': 'text', 'data': {'text': text}}],
    }


def group_event(message_id=1, text='合成群问题'):
    return {
        'post_type': 'message', 'message_type': 'group',
        'sub_type': 'normal', 'self_id': int(BOT),
        'group_id': int(GROUP), 'user_id': int(SENDER),
        'message_id': message_id,
        'message': [
            {'type': 'at', 'data': {'qq': BOT}},
            {'type': 'text', 'data': {'text': text}},
        ],
    }


class SplitReplyTests(unittest.TestCase):
    def test_short_replies_are_never_split(self):
        for text in ('好的。', 'ok', '嗯', '。', 'a' * SOFT_LIMIT):
            self.assertEqual(split_reply(text), [text], text[:10])
        self.assertEqual(split_reply(''), [])
        self.assertEqual(split_reply(None), [])
        self.assertEqual(split_reply(123), [])

    def test_chinese_sentences_keep_semantic_boundaries(self):
        sentence = '这是需要完整保留的一句中文内容，不应该从中间断开。'
        text = sentence * 20
        chunks = split_reply(text)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(''.join(chunks), text)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), HARD_LIMIT)
            self.assertTrue(chunk.endswith('。'), chunk[-10:])

    def test_paragraphs_and_list_items_stay_together(self):
        paragraphs = [
            '第一段说明了整体情况，内容较长但仍应保持完整。' * 3,
            '第二段继续补充细节，不能把段落从中间切断。' * 3,
        ]
        items = [
            '1. 第一项需要完整保留，不能在条目中间断开。',
            '2. 第二项也需要完整保留，内容同样很长。',
            '3. 第三项结尾。',
        ]
        text = '\n\n'.join(paragraphs) + '\n\n' + '\n'.join(items)
        chunks = split_reply(text)
        self.assertEqual(''.join(chunks), text)
        for value in paragraphs + items:
            self.assertEqual(sum(value in chunk for chunk in chunks), 1, value)

    def test_closing_quotes_and_brackets_do_not_start_a_chunk(self):
        text = '他说：“今天需要完整保留这句话。”' * 20
        chunks = split_reply(text)
        self.assertEqual(''.join(chunks), text)
        closers = '”’"\'）)]】》」』'
        for chunk in chunks[1:]:
            self.assertNotIn(chunk[0], closers, chunk[:20])

    def test_english_words_are_kept_whole(self):
        words = [f'word{index}' for index in range(120)]
        text = ' '.join(words)
        chunks = split_reply(text)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(''.join(chunks), text)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), HARD_LIMIT)
        self.assertEqual([word for chunk in chunks for word in chunk.split()], words)
        for chunk in chunks[:-1]:
            self.assertTrue(chunk[-1].isspace(), repr(chunk[-10:]))

    def test_emoji_and_flag_graphemes_are_not_cut(self):
        zwj = chr(0x200D)
        family = zwj.join((
            '\U0001F468', '\U0001F469', '\U0001F467', '\U0001F466',
        ))
        flag = '\U0001F1E8\U0001F1F3'
        text = family * 120
        chunks = split_reply(text)
        self.assertEqual(''.join(chunks), text)
        for chunk in chunks:
            self.assertFalse(chunk.startswith(zwj))
            self.assertFalse(chunk.endswith(zwj))
            self.assertLessEqual(len(chunk), HARD_LIMIT)
        for left, right in zip(chunks, chunks[1:]):
            if left and right:
                self.assertFalse(
                    left[-1] == zwj or right[0] == zwj
                )
        flags = split_reply(flag * 300)
        self.assertEqual(''.join(flags), flag * 300)
        # A flag is a pair of regional indicators.  Each chunk (and therefore
        # each boundary between chunks) must contain a whole number of
        # indicator pairs, so no flag is split across two messages.
        for chunk in flags:
            run = 0
            for char in chunk:
                if 0x1F1E6 <= ord(char) <= 0x1F1FF:
                    run += 1
                else:
                    self.assertEqual(run % 2, 0, chunk)
                    run = 0
            self.assertEqual(run % 2, 0, chunk)

    def test_long_unpunctuated_text_is_bounded_and_lossless(self):
        text = '字' * 2000
        chunks = split_reply(text)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(''.join(chunks), text)
        for chunk in chunks:
            self.assertTrue(0 < len(chunk) <= HARD_LIMIT)

    def test_code_fence_within_one_message_stays_intact(self):
        block = '```python\nprint("完整代码块")\nx = 1\n```'
        text = ('前面的说明很长，需要先占满一定篇幅。' * 8
                + '\n\n' + block + '\n\n后面的总结。')
        chunks = split_reply(text)
        self.assertEqual(''.join(chunks), text)
        self.assertEqual(sum(block in chunk for chunk in chunks), 1)

    def test_large_code_fence_is_split_with_fences_maintained(self):
        body_lines = [f'line_{index:04d} = {index}' for index in range(400)]
        body = '\n'.join(body_lines)
        text = '```python\n' + body + '\n```'
        self.assertGreater(len(text), MAX_CHUNK_CHARS)
        chunks = split_reply(text)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), MAX_CHUNK_CHARS)
            self.assertTrue(chunk.startswith('```python\n'))
            self.assertIn('```', chunk.rsplit('\n', 1)[-1])
        recovered = []
        for chunk in chunks:
            lines = chunk.splitlines()
            recovered.extend(lines[1:-1])
        self.assertEqual(recovered, body_lines)

    def test_bounded_malformed_input_and_determinism(self):
        self.assertEqual(split_reply('x' * (MAX_REPLY_CHARS + 1)), [])
        unclosed = '```python\n' + 'print(1)\n' * 50
        first = split_reply(unclosed)
        second = split_reply(unclosed)
        self.assertEqual(first, second)
        self.assertTrue(first)
        self.assertEqual(''.join(first), unclosed)
        huge = 'x' * MAX_REPLY_CHARS
        bounded = split_reply(huge)
        self.assertLessEqual(len(bounded), MAX_CHUNKS)
        self.assertEqual(''.join(bounded), huge)
        for chunk in bounded:
            self.assertTrue(0 < len(chunk) <= MAX_CHUNK_CHARS)
        self.assertEqual(split_reply(huge), bounded)


class AdapterHarness(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.data = Path(self._temp.name)
        self.adapter = None
        self.connector = None
        self.statuses = []

    def tearDown(self):
        if self.adapter is not None:
            self.adapter.stop(timeout=3.0)
        self._temp.cleanup()

    def wait_for(self, predicate, timeout=4.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return predicate()

    def start(self, core_object, *, script=(), group_core_object=None):
        self.connector = ScriptedConnector(script)

        def status(code, message):
            self.statuses.append((code, message))

        self.adapter = OneBotAdapter(
            'ws://127.0.0.1:3001/', 'synthetic-token',
            lambda bot_id: core_object,
            status_callback=status,
            connect_factory=lambda *_: self.connector,
            group_factory=(None if group_core_object is None
                           else lambda bot_id, private: group_core_object),
            group_enabled=lambda: True,
            send_pacing=0,
        )
        self.assertTrue(self.adapter.start())
        self.assertTrue(self.wait_for(
            lambda: self.adapter.verified_bot_id() == BOT
        ))

    def codes(self):
        return [code for code, _ in self.statuses]


class PrivateSequentialDeliveryTests(AdapterHarness):
    def test_over_old_limit_reply_is_sent_in_order_without_truncation(self):
        reply = long_chinese_reply()
        self.assertGreater(len(reply), 2000)
        fake = FakePrivateCore(reply)
        self.start(fake)
        self.connector.ws.inject(private_event(1))
        chunks = split_reply(reply)
        self.assertTrue(self.wait_for(lambda: len(fake.finalized) == 1))
        sent_texts = [
            item['message'][0]['data']['text']
            for item in self.connector.ws.private_sends
        ]
        self.assertEqual(sent_texts, chunks)
        self.assertEqual(''.join(sent_texts), reply)
        self.assertGreater(len(sent_texts), 1)
        self.assertEqual(fake.finalized, [(TOKEN, True)])
        self.assertEqual(fake.unknown, [])
        self.assertEqual(self.codes().count('reply_acknowledged'), 1)

    def test_short_reply_is_unchanged_single_send(self):
        fake = FakePrivateCore('好的。')
        self.start(fake)
        self.connector.ws.inject(private_event(1))
        self.assertTrue(self.wait_for(lambda: len(fake.finalized) == 1))
        self.assertEqual(len(self.connector.ws.private_sends), 1)
        self.assertEqual(
            self.connector.ws.private_sends[0]['message'],
            [{'type': 'text', 'data': {'text': '好的。'}}],
        )
        self.assertEqual(fake.finalized, [(TOKEN, True)])
        self.assertIn('reply_acknowledged', self.codes())

    def test_later_rejection_after_success_is_partial_and_never_resent(self):
        reply = long_chinese_reply()
        fake = FakePrivateCore(reply)
        self.start(fake, script=['ok', 'failed', 'ok', 'ok'])
        self.connector.ws.inject(private_event(1))
        self.assertTrue(self.wait_for(
            lambda: 'delivery_partial' in self.codes()
        ))
        time.sleep(0.15)
        self.assertEqual(len(self.connector.ws.private_sends), 2)
        self.assertEqual(fake.finalized, [])
        self.assertEqual(fake.unknown, [TOKEN])
        self.assertNotIn('reply_acknowledged', self.codes())
        self.assertNotIn('delivery_rejected', self.codes())
        message = dict(self.statuses)['delivery_partial']
        self.assertIn('部分', message)
        self.assertIn('未确认', message)
        self.assertNotIn('好的。', message)
        self.assertNotIn('第0段', message)

    def test_timeout_in_middle_marks_unknown_and_sends_nothing_further(self):
        reply = long_chinese_reply()
        fake = FakePrivateCore(reply)
        self.start(fake, script=['ok', 'timeout', 'ok', 'ok'])
        self.connector.ws.inject(private_event(1))
        self.assertTrue(self.wait_for(
            lambda: 'delivery_partial' in self.codes()
        ))
        time.sleep(0.15)
        self.assertEqual(len(self.connector.ws.private_sends), 2)
        self.assertEqual(fake.unknown, [TOKEN])
        self.assertEqual(fake.finalized, [])
        self.assertNotIn('reply_acknowledged', self.codes())

    def test_first_definite_rejection_still_finalizes_false(self):
        reply = long_chinese_reply()
        fake = FakePrivateCore(reply)
        self.start(fake, script=['failed'])
        self.connector.ws.inject(private_event(1))
        self.assertTrue(self.wait_for(lambda: fake.finalized == [(TOKEN, False)]))
        self.assertEqual(len(self.connector.ws.private_sends), 1)
        self.assertEqual(fake.unknown, [])
        self.assertIn('delivery_rejected', self.codes())
        self.assertNotIn('reply_acknowledged', self.codes())

    def test_malformed_ack_mid_sequence_is_unknown_not_delivered(self):
        reply = long_chinese_reply()
        fake = FakePrivateCore(reply)
        self.start(fake, script=['ok', 'malformed', 'ok'])
        self.connector.ws.inject(private_event(1))
        self.assertTrue(self.wait_for(
            lambda: 'delivery_partial' in self.codes()
        ))
        time.sleep(0.1)
        self.assertEqual(len(self.connector.ws.private_sends), 2)
        self.assertEqual(fake.unknown, [TOKEN])
        self.assertEqual(fake.finalized, [])

    def test_cancellation_mid_sequence_keeps_pending_and_never_retries(self):
        reply = long_chinese_reply()
        fake = FakePrivateCore(reply)
        self.start(fake, script=['ok', 'hang'])
        self.connector.ws.inject(private_event(1))
        self.assertTrue(self.wait_for(
            lambda: len(self.connector.ws.private_sends) == 2
        ))
        self.assertTrue(self.adapter.stop(timeout=3.0))
        self.adapter = None
        self.assertEqual(len(self.connector.ws.private_sends), 2)
        self.assertEqual(fake.unknown, [TOKEN])
        self.assertEqual(fake.finalized, [])
        self.assertIn('delivery_partial', self.codes())
        self.assertNotIn('reply_acknowledged', self.codes())

    def test_invalid_outgoing_answer_is_finalized_false_without_send(self):
        fake = FakePrivateCore('长' * (MAX_REPLY_CHARS + 1))
        self.start(fake)
        self.connector.ws.inject(private_event(1))
        self.assertTrue(self.wait_for(lambda: fake.finalized == [(TOKEN, False)]))
        self.assertEqual(self.connector.ws.private_sends, [])
        self.assertIn('model_response_invalid', self.codes())

    def test_control_character_in_long_reply_is_invalid_without_send(self):
        fake = FakePrivateCore('正常内容。' * 300 + '\x00')
        self.start(fake)
        self.connector.ws.inject(private_event(1))
        self.assertTrue(self.wait_for(lambda: fake.finalized == [(TOKEN, False)]))
        self.assertEqual(self.connector.ws.private_sends, [])
        self.assertIn('model_response_invalid', self.codes())


class GroupSequentialDeliveryTests(AdapterHarness):
    def make_group(self, reply):
        recorder = lambda endpoint, model, messages, api_key: reply
        return GroupChat(self.data, BOT, request_reply=recorder)

    def test_group_at_sender_only_on_first_chunk_and_order_preserved(self):
        reply = long_chinese_reply()
        fake_private = FakePrivateCore()
        chat = self.make_group(reply)
        self.start(fake_private, group_core_object=chat)
        self.connector.ws.inject(group_event(1))
        self.assertTrue(self.wait_for(
            lambda: 'group_reply_acknowledged' in self.codes()
        ))
        chunks = split_reply(reply)
        sends = self.connector.ws.group_sends
        self.assertEqual(len(sends), len(chunks))
        for index, (params, chunk) in enumerate(zip(sends, chunks)):
            message = params['message']
            if index == 0:
                self.assertEqual(message[0], {
                    'type': 'at', 'data': {'qq': SENDER},
                })
                self.assertEqual(message[1], {
                    'type': 'text', 'data': {'text': ' '+chunk},
                })
                self.assertEqual(len(message), 2)
            else:
                self.assertEqual(message, [{
                    'type': 'text', 'data': {'text': chunk},
                }])
        state = json.loads(
            (self.data / group_core._STATE_FILENAME).read_text(
                encoding='utf-8'
            )
        )
        self.assertIsNone(state['pending'])
        turns = list(state['pairs'].values())[0]['turns']
        self.assertEqual(turns[-1]['assistant'], reply)
        self.assertEqual(len(turns), 1)

    def test_group_partial_rejection_keeps_pending_without_history(self):
        reply = long_chinese_reply()
        fake_private = FakePrivateCore()
        chat = self.make_group(reply)
        self.start(fake_private, script=['ok', 'failed'],
                   group_core_object=chat)
        self.connector.ws.inject(group_event(1))
        self.assertTrue(self.wait_for(
            lambda: 'group_delivery_partial' in self.codes()
        ))
        time.sleep(0.15)
        self.assertEqual(len(self.connector.ws.group_sends), 2)
        self.assertNotIn('group_reply_acknowledged', self.codes())
        self.assertNotIn('group_delivery_rejected', self.codes())
        message = dict(self.statuses)['group_delivery_partial']
        self.assertIn('部分', message)
        self.assertIn('未确认', message)
        state = json.loads(
            (self.data / group_core._STATE_FILENAME).read_text(
                encoding='utf-8'
            )
        )
        self.assertIsNotNone(state['pending'])
        self.assertEqual(state['pairs'], {})


class CoreBoundTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.data = Path(self._temp.name)

    def tearDown(self):
        self._temp.cleanup()

    def bind_private(self, instance):
        code = instance.begin_pairing()
        result = instance.handle_private(
            '654321', 'bind-1', f'绑定 {code}'
        )
        self.assertEqual(result.kind, 'bound')

    def test_private_core_stores_full_long_reply_and_restart_reads_it(self):
        reply = long_chinese_reply()
        self.assertGreater(len(reply), 2000)
        recorder = lambda *args: reply
        instance = QQCore(self.data, BOT, request_reply=recorder)
        self.bind_private(instance)
        result = instance.handle_private('654321', 'long-1', '合成问题')
        self.assertEqual(result.kind, 'chat')
        self.assertEqual(result.text, reply)
        self.assertEqual(instance._pending['assistant'], reply)
        self.assertTrue(instance.finalize_reply(result.delivery_token, True))
        self.assertEqual(instance._turns[-1]['assistant'], reply)

        restarted = QQCore(self.data, BOT, request_reply=recorder)
        self.assertEqual(restarted._turns[-1]['assistant'], reply)
        self.assertEqual(restarted.local_status()['pending_recovery_required'], False)

    def test_private_pending_long_reply_survives_restart_fail_closed(self):
        reply = long_chinese_reply()
        calls = []

        def recorder(*args):
            calls.append(True)
            return reply

        instance = QQCore(self.data, BOT, request_reply=recorder)
        self.bind_private(instance)
        result = instance.handle_private('654321', 'pending-1', '合成问题')
        self.assertEqual(result.kind, 'chat')
        self.assertTrue(instance.mark_delivery_unknown(result.delivery_token))

        restarted = QQCore(self.data, BOT, request_reply=recorder)
        self.assertTrue(
            restarted.local_status()['pending_recovery_required']
        )
        self.assertEqual(restarted._pending['assistant'], reply)
        self.assertIsNone(restarted.handle_private(
            '654321', 'after-restart', '新的问题'
        ))
        self.assertEqual(len(calls), 1)
        self.assertEqual(restarted._turns, [])

    def test_group_core_stores_full_long_reply_and_restart_reads_it(self):
        reply = long_chinese_reply()
        chat = GroupChat(self.data, BOT, request_reply=lambda *args: reply)
        result = chat.handle_group(GROUP, SENDER, 'g-long-1', '合成群问题')
        self.assertEqual(result.kind, 'group_chat')
        self.assertEqual(result.text, reply)
        self.assertTrue(chat.finalize_group(result.delivery_token, True))
        state = json.loads(
            (self.data / group_core._STATE_FILENAME).read_text(
                encoding='utf-8'
            )
        )
        turns = list(state['pairs'].values())[0]['turns']
        self.assertEqual(turns[-1]['assistant'], reply)

        restarted = GroupChat(
            self.data, BOT, request_reply=lambda *args: reply
        )
        turns = list(restarted._pairs.values())[0]['turns']
        self.assertEqual(turns[-1]['assistant'], reply)

    def test_core_reply_limits_are_raised_but_input_limits_stay(self):
        self.assertEqual(core._MAX_REPLY, MAX_REPLY_CHARS)
        self.assertEqual(group_core._MAX_REPLY, MAX_REPLY_CHARS)
        self.assertEqual(core._MAX_INPUT, 2000)
        self.assertEqual(group_core._MAX_INPUT, 1000)

    def test_many_long_group_turns_still_respect_the_state_byte_bound(self):
        reply = '长' * MAX_REPLY_CHARS
        chat = GroupChat(self.data, BOT, request_reply=lambda *args: reply)
        for index in range(8):
            chat._clock = lambda: float(1000 + index * 10)
            result = chat.handle_group(
                GROUP, f'2000543{index:02d}', f'bulk-{index}', '合成问题'
            )
            self.assertIsNotNone(result)
            self.assertTrue(chat.finalize_group(result.delivery_token, True))
        raw = (self.data / group_core._STATE_FILENAME).read_bytes()
        self.assertLessEqual(len(raw), group_core._MAX_STATE_BYTES)
        # The file must still load with the original bounds.
        GroupChat(self.data, BOT, request_reply=lambda *args: reply)


if __name__ == '__main__':
    unittest.main()
