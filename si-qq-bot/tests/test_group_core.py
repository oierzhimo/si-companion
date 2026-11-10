"""Offline tests for the separate group mention ledger.

Every test uses a temporary directory, a synthetic bot/group/sender, and an
injected ``request_reply`` callable.  No model, QQ, network, credential, or
real user data is touched.
"""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
import group_core  # noqa: E402
from core import QQCore  # noqa: E402
from group_core import (  # noqa: E402
    GROUP_ERROR_TEXT, GROUP_SYSTEM_ADMIN, GROUP_SYSTEM_MEMBER, GroupChat,
)


BOT = '100012345'
GROUP = '300000001'
GROUP_OTHER = '300000002'
SENDER = '200054321'
SENDER_OTHER = '200054322'
APP_ID = 'synthetic-qq-app-id'
ADMIN_OPENID = 'synthetic-admin-openid'
OTHER_OPENID = 'synthetic-other-openid'
PRIVATE_SECRET = 'SECRET-PRIVATE-PROFILE-MUST-NOT-APPEAR'


class Clock:
    def __init__(self, start=1000.0):
        self.value = float(start)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += float(seconds)


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


class BlockingRecorder(Recorder):
    def __init__(self, reply='我在。'):
        super().__init__(reply)
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, endpoint, model, messages, api_key):
        reply = super().__call__(endpoint, model, messages, api_key)
        self.entered.set()
        self.release.wait(10)
        return reply


class PrivateProfileStub:
    """Comparison-only surface with a private value that must stay private."""

    profile = PRIVATE_SECRET

    def __init__(self):
        self.compared = []

    def is_administrator(self, account_id):
        self.compared.append(account_id)
        return account_id == SENDER


class GroupCoreTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.data = Path(self._temp.name)
        self.clock = Clock()
        self.sequence = 0

    def tearDown(self):
        self._temp.cleanup()

    def mid(self, label='m'):
        self.sequence += 1
        return f'{label}-{self.sequence}'

    def make_chat(self, recorder=None, *, is_admin=None, bot=BOT, data=None):
        chat = GroupChat(
            data if data is not None else self.data, bot,
            request_reply=recorder if recorder is not None else Recorder(),
            is_admin=is_admin,
        )
        chat._clock = self.clock
        return chat

    def state_path(self, data=None):
        return (data if data is not None else self.data) / (
            group_core._STATE_FILENAME
        )

    def read_state(self, data=None):
        return json.loads(
            self.state_path(data).read_text(encoding='utf-8')
        )

    def deliver(self, chat, text, *, group=GROUP, sender=SENDER,
                delivered=True):
        reply = chat.handle_group(group, sender, self.mid(), text)
        self.assertIsNotNone(reply)
        self.assertEqual(reply.kind, 'group_chat')
        self.assertTrue(chat.finalize_group(reply.delivery_token, delivered))
        return reply

    # -- prompt privacy and role --------------------------------------

    def test_prompt_is_persona_plus_group_system_and_no_private_data(self):
        recorder = Recorder()
        chat = self.make_chat(recorder)
        self.deliver(chat, '今天过得怎么样？')

        call = recorder.calls[0]
        self.assertEqual(call['endpoint'], core._ENDPOINT)
        self.assertEqual(call['model'], core._MODEL)
        messages = call['messages']
        self.assertEqual(messages[0], {
            'role': 'system', 'content': core._CONVERSATION.PERSONA,
        })
        self.assertEqual(messages[1], {
            'role': 'system', 'content': GROUP_SYSTEM_MEMBER,
        })
        self.assertEqual(messages[-1], {
            'role': 'user', 'content': '今天过得怎么样？',
        })
        # The group ledger never receives the private profile or private
        # turns, and its model messages contain no numeric account/group IDs.
        encoded = json.dumps(messages, ensure_ascii=False)
        self.assertNotIn(PRIVATE_SECRET, encoded)
        self.assertNotIn(BOT, encoded)
        self.assertNotIn(GROUP, encoded)
        self.assertNotIn(SENDER, encoded)

    def test_admin_comparison_selects_admin_prompt_without_private_fields(self):
        stub = PrivateProfileStub()
        recorder = Recorder()
        chat = self.make_chat(recorder, is_admin=stub.is_administrator)

        self.deliver(chat, '在吗？', sender=SENDER)
        self.assertEqual(
            recorder.calls[0]['messages'][1]['content'],
            GROUP_SYSTEM_ADMIN,
        )
        self.assertIn(SENDER, stub.compared)
        self.assertNotIn(
            PRIVATE_SECRET,
            json.dumps(recorder.calls[0]['messages'], ensure_ascii=False),
        )

        self.clock.advance(2.0)
        self.deliver(chat, '我也在。', sender=SENDER_OTHER)
        self.assertEqual(
            recorder.calls[1]['messages'][1]['content'],
            GROUP_SYSTEM_MEMBER,
        )

    def test_member_system_is_friendly_and_keeps_the_admin_boundary(self):
        # The member prompt must not turn Si into a cold service desk: it
        # explicitly allows warmth and opinions, while still forbidding
        # administrator-only familiarity and private knowledge.
        for expected in ('友好', '热情', '看法', '不同意见'):
            self.assertIn(expected, GROUP_SYSTEM_MEMBER)
        self.assertIn('不是', GROUP_SYSTEM_MEMBER)
        self.assertIn('管理员', GROUP_SYSTEM_MEMBER)
        self.assertIn('私人信息', GROUP_SYSTEM_MEMBER)
        self.assertIn('不要使用只属于管理员', GROUP_SYSTEM_MEMBER)
        self.assertNotIn('不要用对管理员的熟悉口吻', GROUP_SYSTEM_MEMBER)

    def test_unverifiable_admin_comparison_is_a_member(self):
        def broken(_account):
            raise RuntimeError(PRIVATE_SECRET)

        recorder = Recorder()
        chat = self.make_chat(recorder, is_admin=broken)
        self.deliver(chat, '你好。')
        self.assertEqual(
            recorder.calls[0]['messages'][1]['content'],
            GROUP_SYSTEM_MEMBER,
        )
        self.assertNotIn(
            PRIVATE_SECRET,
            json.dumps(recorder.calls[0]['messages'], ensure_ascii=False),
        )

    def test_real_private_core_comparison_reads_no_profile_and_writes_nothing(self):
        responder = Recorder()
        private = QQCore(self.data, APP_ID, request_reply=responder)
        self.clock = Clock()
        private._clock = self.clock
        code = private.begin_pairing()
        bound = private.handle_private(
            ADMIN_OPENID, self.mid('bind'), f'绑定 {code}'
        )
        self.assertEqual(bound.kind, 'bound')
        state_path = self.data / core._STATE_FILENAME
        before = state_path.read_bytes()

        self.assertTrue(private.is_administrator(ADMIN_OPENID))
        self.assertFalse(private.is_administrator(OTHER_OPENID))
        self.assertFalse(private.is_administrator(''))
        self.assertFalse(private.is_administrator(APP_ID))
        # Comparison only: no profile read, model call, turn, or state write.
        self.assertEqual(private._turns, [])
        self.assertEqual(responder.calls, [])
        self.assertEqual(state_path.read_bytes(), before)

    # -- pair isolation ------------------------------------------------

    def test_context_is_isolated_by_group_and_sender(self):
        recorder = Recorder()
        chat = self.make_chat(recorder)

        self.deliver(chat, '第一句', group=GROUP, sender=SENDER)
        self.clock.advance(2.0)
        self.deliver(chat, '第二句', group=GROUP, sender=SENDER)
        second = recorder.calls[1]['messages']
        contents = [item['content'] for item in second]
        self.assertIn('第一句', contents)
        self.assertIn('我在。', contents)

        self.clock.advance(2.0)
        self.deliver(chat, '别人的话', group=GROUP, sender=SENDER_OTHER)
        other_contents = [
            item['content'] for item in recorder.calls[2]['messages']
        ]
        self.assertNotIn('第一句', other_contents)
        self.assertNotIn('第二句', other_contents)

        self.clock.advance(2.0)
        self.deliver(chat, '另一个群', group=GROUP_OTHER, sender=SENDER)
        other_group_contents = [
            item['content'] for item in recorder.calls[3]['messages']
        ]
        self.assertNotIn('第一句', other_group_contents)
        self.assertNotIn('第二句', other_group_contents)

    def test_confirmed_context_survives_restart_for_the_same_pair(self):
        self.deliver(self.make_chat(), '还记得吗？')
        recorder = Recorder()
        restarted = self.make_chat(recorder)
        self.clock.advance(2.0)
        self.deliver(restarted, '接着聊。')
        contents = [item['content'] for item in recorder.calls[0]['messages']]
        self.assertIn('还记得吗？', contents)

    # -- durability ----------------------------------------------------

    def test_pending_exists_before_ack_and_context_only_after_confirmation(self):
        recorder = Recorder()
        chat = self.make_chat(recorder)
        reply = chat.handle_group(GROUP, SENDER, self.mid(), '你好。')
        self.assertEqual(reply.kind, 'group_chat')

        state = self.read_state()
        self.assertIsNotNone(state['pending'])
        self.assertEqual(state['pairs'], {})

        # A rejection resolves the record but never appends the turn.
        self.assertTrue(chat.finalize_group(reply.delivery_token, False))
        state = self.read_state()
        self.assertIsNone(state['pending'])
        self.assertEqual(state['pairs'], {})

        self.clock.advance(2.0)
        reply = chat.handle_group(GROUP, SENDER, self.mid(), '再说一次。')
        self.assertTrue(chat.finalize_group(reply.delivery_token, True))
        state = self.read_state()
        self.assertIsNone(state['pending'])
        pair_turns = list(state['pairs'].values())[0]['turns']
        self.assertEqual(pair_turns[-1], {
            'user': '再说一次。', 'assistant': '我在。',
        })

    def test_wrong_token_and_storage_failure_keep_pending_and_old_state(self):
        chat = self.make_chat()
        reply = chat.handle_group(GROUP, SENDER, self.mid(), '第一句。')
        before = self.state_path().read_bytes()

        self.assertFalse(chat.finalize_group('wrong-token', True))
        self.assertEqual(self.state_path().read_bytes(), before)
        # A plain in-flight pending is not yet offered for recovery: the owner
        # may still resolve it with the real token.  The adapter flags an
        # unknown outcome explicitly after its send attempt has unwound.
        self.assertFalse(
            chat.local_status()['pending_recovery_required']
        )
        self.assertTrue(
            chat.mark_group_delivery_unknown(reply.delivery_token)
        )
        self.assertTrue(
            chat.local_status()['pending_recovery_required']
        )

        with mock.patch.object(
                group_core._private_core, '_atomic_write_json',
                side_effect=OSError('synthetic disk failure')):
            self.assertFalse(chat.finalize_group(reply.delivery_token, True))

        self.assertEqual(self.state_path().read_bytes(), before)
        self.assertTrue(chat.local_status()['pending_recovery_required'])
        state = self.read_state()
        self.assertEqual(state['pairs'], {})

    def test_failed_dedup_commit_never_calls_the_model(self):
        recorder = Recorder()
        chat = self.make_chat(recorder)
        path = self.state_path()
        with mock.patch.object(
                group_core._private_core, '_atomic_write_json',
                side_effect=OSError('synthetic disk failure')):
            reply = chat.handle_group(GROUP, SENDER, self.mid(), '你好。')
        self.assertIsNone(reply)
        self.assertEqual(recorder.calls, [])
        self.assertFalse(path.exists())

    def test_restart_pending_blocks_until_explicit_recovery_and_never_replays(self):
        first = self.make_chat()
        reply = first.handle_group(GROUP, SENDER, self.mid(), '第一句。')
        original_id = self.sequence

        recorder = Recorder()
        restarted = self.make_chat(recorder)
        self.assertTrue(
            restarted.local_status()['pending_recovery_required']
        )
        self.assertIsNone(restarted.handle_group(
            GROUP, SENDER, self.mid(), '新的问题。'
        ))
        self.assertIsNone(restarted.handle_group(
            GROUP, SENDER, f'm-{original_id}', '第一句。'
        ))
        self.assertEqual(recorder.calls, [])

        self.assertTrue(restarted.discard_uncertain_group_pending())
        self.assertFalse(
            restarted.local_status()['pending_recovery_required']
        )
        # Recovery drops only the unresolved record; the inbound message stays
        # deduplicated, so it is not replayed to the model.
        self.assertIsNone(restarted.handle_group(
            GROUP, SENDER, f'm-{original_id}', '第一句。'
        ))
        self.assertEqual(recorder.calls, [])
        self.assertEqual(
            restarted.handle_group(
                GROUP, SENDER, self.mid(), '恢复后的新问题。'
            ).kind,
            'group_chat',
        )
        self.assertEqual(len(recorder.calls), 1)

    def test_model_failure_is_fixed_natural_and_deduplicated(self):
        recorder = Recorder()
        recorder.error = RuntimeError(PRIVATE_SECRET)
        chat = self.make_chat(recorder)
        message_id = self.mid()

        reply = chat.handle_group(GROUP, SENDER, message_id, '你好。')
        self.assertEqual(reply.kind, 'group_error')
        self.assertEqual(reply.text, GROUP_ERROR_TEXT)
        self.assertIsNone(reply.delivery_token)
        self.assertNotIn(PRIVATE_SECRET, reply.text)

        # The failed inbound message is deduplicated before the model call.
        self.assertIsNone(chat.handle_group(
            GROUP, SENDER, message_id, '你好。'
        ))
        self.assertEqual(len(recorder.calls), 1)
        state = self.read_state()
        self.assertIsNone(state['pending'])
        self.assertEqual(len(state['processed_message_ids']), 1)

    def test_self_and_invalid_requests_are_ignored(self):
        recorder = Recorder()
        chat = self.make_chat(recorder)
        self.assertIsNone(chat.handle_group(GROUP, BOT, self.mid(), '自己'))
        self.assertIsNone(chat.handle_group('', SENDER, self.mid(), '群'))
        self.assertIsNone(chat.handle_group(GROUP, SENDER, '', '消息'))
        self.assertIsNone(chat.handle_group(GROUP, SENDER, self.mid(), ''))
        self.assertIsNone(chat.handle_group(
            GROUP, SENDER, self.mid(), 'x' * 1001
        ))
        self.assertEqual(recorder.calls, [])
        self.assertFalse(self.state_path().exists())

    def test_only_one_model_job_runs_at_a_time(self):
        recorder = BlockingRecorder()
        chat = self.make_chat(recorder)
        results = []

        def first_call():
            results.append(
                chat.handle_group(GROUP, SENDER, self.mid(), '第一个。')
            )

        self.addCleanup(recorder.release.set)
        worker = threading.Thread(target=first_call)
        worker.start()
        self.assertTrue(recorder.entered.wait(3))
        # A second pair cannot start another model job while the first runs.
        self.assertIsNone(chat.handle_group(
            GROUP, SENDER_OTHER, self.mid(), '第二个。'
        ))
        self.assertEqual(len(recorder.calls), 1)
        recorder.release.set()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0].kind, 'group_chat')

    def test_cooldown_is_per_pair_and_bounded(self):
        recorder = Recorder()
        chat = self.make_chat(recorder)
        self.deliver(chat, '第一句。')
        # Immediately repeating the same pair is ignored without a model call.
        self.assertIsNone(chat.handle_group(
            GROUP, SENDER, self.mid(), '太快了。'
        ))
        self.assertEqual(len(recorder.calls), 1)
        # Another sender is a different pair and is not cooled down.
        self.deliver(chat, '我是别人。', sender=SENDER_OTHER)
        self.assertEqual(len(recorder.calls), 2)
        self.clock.advance(1.5)
        self.deliver(chat, '现在可以了。')
        self.assertEqual(len(recorder.calls), 3)

        for index in range(140):
            self.deliver(
                chat, f'批量 {index}', sender=f'9000{index:05d}'
            )
        self.assertLessEqual(len(chat._cooldown), group_core._MAX_COOLDOWN_ENTRIES)

    def test_processed_ids_and_state_size_are_bounded(self):
        chat = self.make_chat()
        limit = group_core._MAX_PROCESSED_IDS + 8
        for index in range(limit):
            self.clock.advance(2.0)
            reply = chat.handle_group(
                GROUP, SENDER, f'bulk-{index}', f'消息 {index}'
            )
            self.assertIsNotNone(reply)
            self.assertTrue(chat.finalize_group(reply.delivery_token, True))

        state = self.read_state()
        self.assertLessEqual(
            len(state['processed_message_ids']),
            group_core._MAX_PROCESSED_IDS,
        )
        self.assertLessEqual(len(state['pairs']), group_core._MAX_PAIRS)

    def test_corrupt_state_keeps_bytes_and_refuses_to_load(self):
        path = self.state_path()
        original = b'{"schema":1,"bot_id":'
        path.write_bytes(original)
        with self.assertRaises(RuntimeError):
            self.make_chat()
        self.assertEqual(path.read_bytes(), original)

        path.write_bytes(json.dumps({
            'schema': 1, 'bot_id': '999999', 'pairs': {},
            'processed_message_ids': [], 'pending': None,
        }).encode('utf-8'))
        original = path.read_bytes()
        with self.assertRaises(RuntimeError):
            self.make_chat()
        self.assertEqual(path.read_bytes(), original)

        # Deeply nested JSON raises RecursionError while decoding.  It must
        # still fail closed with the original bytes untouched.
        nested = (b'[' * 2000) + (b']' * 2000)
        path.write_bytes(nested)
        with self.assertRaises(RuntimeError):
            self.make_chat()
        self.assertEqual(path.read_bytes(), nested)

    def test_group_status_stays_available_during_a_private_model_request(self):
        recorder = BlockingRecorder()
        private = QQCore(self.data, BOT, request_reply=recorder)
        code = private.begin_pairing()
        self.assertIsNotNone(private.handle_private(
            SENDER, 'bind-only', '绑定 ' + code,
        ))
        chat = self.make_chat()
        # A second core for the same group state still shares its serial lock.
        self.assertIs(chat._lock, self.make_chat()._lock)
        worker = threading.Thread(
            target=private.handle_private,
            args=(SENDER, 'slow-private-model', '一条合成私聊测试'),
        )
        worker.start()
        probe_done = threading.Event()
        result = []
        probe = threading.Thread(target=lambda: (
            result.append(chat.local_status()), probe_done.set(),
        ))
        try:
            self.assertTrue(recorder.entered.wait(1.0))
            probe.start()
            self.assertTrue(probe_done.wait(0.3),
                            'private model request blocked group status')
            self.assertEqual(result, [{'pending_recovery_required': False}])
        finally:
            recorder.release.set()
            worker.join(2.0)
            if probe.ident is not None:
                probe.join(2.0)

    def test_group_and_sender_ids_are_not_state_keys_or_paths(self):
        self.deliver(self.make_chat(), '你好。')
        raw = self.state_path().read_text(encoding='utf-8')
        self.assertNotIn(GROUP, raw)
        self.assertNotIn(SENDER, raw)
        self.assertNotIn(GROUP, str(self.state_path()))


if __name__ == '__main__':
    unittest.main()
