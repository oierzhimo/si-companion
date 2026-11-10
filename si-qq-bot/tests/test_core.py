"""Offline tests for the reusable QQ private-chat core.

Every test uses a temporary directory and synthetic OpenIDs/message IDs.
``request_reply`` is always injected, so these tests never touch the network,
the real model service, or a QQ SDK.
"""
import inspect
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from core import CoreReply, QQCore  # noqa: E402


PROD = core._CONVERSATION
PERSONA = PROD.PERSONA
ACCESS_KEY = PROD.GENAI_DEFAULT_API_KEY
ADMIN = 'synthetic-admin-openid'
OTHER = 'synthetic-other-openid'
APP_ID = 'synthetic-qq-app-id'
QQ_SELF = '100012345'
QQ_ADMIN = '200054321'

DIAGNOSTIC_CODES = {
    'model_processing', 'model_failed', 'model_failed_timeout',
    'model_failed_connection', 'model_failed_authentication',
    'model_failed_school_login', 'model_failed_busy',
    'model_failed_unavailable', 'model_response_invalid',
    'model_failed_runtime', 'model_failed_conversation', 'model_failed_value',
    'model_failed_type', 'model_failed_lookup', 'model_failed_attribute',
    'model_failed_os', 'model_prepare_failed',
    *(f'model_http_{x}' for x in (400, 404, 500, 502)),
    *(f'model_http_{x}xx' for x in range(1, 6)),
}

# Exact fixed messages the production request_reply wraps in its own
# ConversationError, and the fixed diagnostic code each must produce.
FIXED_CONVERSATION_MESSAGES = (
    ('本机反代访问口令有误。请检查“反代访问口令”后重试。',
     'model_failed_authentication'),
    ('本机反代或学校登录暂不可用。请先完成学校登录，并确认反代服务正在运行。',
     'model_failed_school_login'),
    ('本机模型服务正忙，请稍后重试。', 'model_failed_busy'),
    ('本机模型未连接或等待超时。请确认服务已启动，再重试。',
     'model_failed_unavailable'),
    ('本机接口没有返回可显示的文字回复。', 'model_response_invalid'),
)


class Clock:
    def __init__(self, start=1000.0):
        self.value = float(start)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class Recorder:
    """Deterministic request_reply replacement with captured arguments."""

    def __init__(self, reply='好的。'):
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


class SyntheticConversation:
    """Minimal stand-in for the production conversation class."""

    @staticmethod
    def messages(view, text):
        return [{'role': 'user', 'content': text}]


class SyntheticConversationError(Exception):
    """Synthetic stand-in for the production ConversationError class."""


class CoreTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.data = Path(self._temp.name)
        self.clock = Clock()
        self._sequence = 0

    def tearDown(self):
        self._temp.cleanup()

    # -- helpers -------------------------------------------------------

    def mid(self, label='message'):
        self._sequence += 1
        return f'{label}-{self._sequence}'

    def make_core(self, responder, app_id=APP_ID, data=None):
        instance = QQCore(data if data is not None else self.data,
                          app_id, request_reply=responder)
        instance._clock = self.clock
        return instance

    def state_path(self, data=None):
        return (data if data is not None else self.data) / core._STATE_FILENAME

    def read_state(self, data=None):
        return json.loads(self.state_path(data).read_text(encoding='utf-8'))

    def bind(self, instance, code=None, openid=ADMIN):
        if code is None:
            code = instance.begin_pairing()
        self.assertIsInstance(code, str)
        result = instance.handle_private(
            openid, self.mid('bind'), f'绑定 {code}'
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.kind, 'bound')
        self.assertIsNone(result.delivery_token)
        return code

    def chat(self, instance, text, label='chat', openid=ADMIN):
        message_id = self.mid(label)
        result = instance.handle_private(openid, message_id, text)
        self.assertIsNotNone(result)
        self.assertEqual(result.kind, 'chat')
        self.assertIsInstance(result.delivery_token, str)
        return message_id, result

    def use_synthetic_conversation(self, instance, error_class):
        """Replace only the conversation module surface used by _chat."""
        instance._conversation = SimpleNamespace(
            Conversation=SyntheticConversation,
            GENAI_DEFAULT_API_KEY='synthetic-conversation-key',
            ConversationError=error_class,
        )

    # -- construction, pairing, takeover -------------------------------

    def test_construction_and_begin_pairing_do_not_touch_disk_or_model(self):
        recorder = Recorder()
        instance = self.make_core(recorder)
        self.assertEqual(recorder.calls, [])
        self.assertFalse(self.state_path().exists())
        self.assertEqual(list(self.data.iterdir()), [])

        first = instance.begin_pairing()
        second = instance.begin_pairing()
        self.assertIsInstance(first, str)
        self.assertIsInstance(second, str)
        self.assertNotEqual(first, second)
        self.assertEqual(recorder.calls, [])
        self.assertEqual(list(self.data.iterdir()), [])

        # Replacing a local code invalidates the old one.
        self.assertIsNone(instance.handle_private(
            ADMIN, self.mid('old-code'), f'绑定 {first}'
        ))
        self.assertEqual(recorder.calls, [])
        self.assertFalse(self.state_path().exists())

        self.bind(instance, code=second)
        self.assertEqual(recorder.calls, [])

    def test_wrong_missing_expired_and_replayed_codes_are_silent(self):
        recorder = Recorder()
        instance = self.make_core(recorder)

        # No code has been created yet.
        self.assertIsNone(instance.handle_private(
            ADMIN, self.mid('no-code'), '绑定 AAAAAAAAAA'
        ))
        self.assertEqual(recorder.calls, [])
        self.assertFalse(self.state_path().exists())

        code = instance.begin_pairing()
        wrong = ('B' if code[0] != 'B' else 'C') + code[1:]
        self.assertIsNone(instance.handle_private(
            ADMIN, self.mid('wrong-code'), f'绑定 {wrong}'
        ))
        self.assertEqual(recorder.calls, [])
        self.assertFalse(self.state_path().exists())

        self.clock.advance(core._PAIR_TTL + 1)
        self.assertIsNone(instance.handle_private(
            ADMIN, self.mid('expired-code'), f'绑定 {code}'
        ))
        self.assertEqual(recorder.calls, [])
        self.assertFalse(self.state_path().exists())

        fresh = instance.begin_pairing()
        self.bind(instance, code=fresh)
        self.assertNotIn(code, self.state_path().read_text(encoding='utf-8'))
        self.assertNotIn(fresh, self.state_path().read_text(encoding='utf-8'))

        # Replay and takeover attempts cannot replace the administrator.
        self.assertIsNone(instance.handle_private(
            OTHER, self.mid('replay'), f'绑定 {fresh}'
        ))
        self.assertIsNone(instance.begin_pairing())
        self.assertEqual(recorder.calls, [])
        self.assertEqual(self.read_state()['admin_openid'], ADMIN)

    def test_bad_code_rate_limit_is_bounded_and_recovers(self):
        recorder = Recorder()
        instance = self.make_core(recorder)
        code = instance.begin_pairing()
        wrong = ('B' if code[0] != 'B' else 'C') + code[1:]

        for index in range(core._PAIR_MAX_FAILURES):
            self.assertIsNone(instance.handle_private(
                ADMIN, self.mid(f'bad-{index}'), f'绑定 {wrong}'
            ))
        self.assertEqual(recorder.calls, [])

        # The correct code is ignored while this account is rate-limited.
        self.assertIsNone(instance.handle_private(
            ADMIN, self.mid('locked-correct'), f'绑定 {code}'
        ))
        self.assertEqual(recorder.calls, [])
        self.assertFalse(self.state_path().exists())

        self.clock.advance(core._PAIR_LOCK_SECONDS + 1)
        self.bind(instance, code=code)
        self.assertEqual(self.read_state()['admin_openid'], ADMIN)

    def test_bound_core_refuses_new_pairing_and_other_users(self):
        recorder = Recorder()
        instance = self.make_core(recorder)
        self.bind(instance)
        before = self.state_path().read_bytes()

        self.assertIsNone(instance.begin_pairing())
        self.assertIsNone(instance.handle_private(
            OTHER, self.mid('other-bind'), '绑定 AAAAAAAAAA'
        ))
        self.assertIsNone(instance.handle_private(
            OTHER, self.mid('other-chat'), '你好'
        ))
        self.assertIsNone(instance.handle_private(
            ADMIN, self.mid('empty'), ''
        ))
        self.assertIsNone(instance.handle_private(
            ADMIN, self.mid('spaces'), '   \n\t'
        ))
        self.assertIsNone(instance.handle_private(
            ADMIN, self.mid('too-long'), '字' * (core._MAX_INPUT + 1)
        ))
        self.assertIsNone(instance.handle_private(
            ADMIN, self.mid('control'), '你好\x00世界'
        ))
        self.assertEqual(recorder.calls, [])
        self.assertEqual(self.state_path().read_bytes(), before)
        self.assertEqual(self.read_state()['admin_openid'], ADMIN)

    # -- model request contract and state ------------------------------

    def test_production_persona_endpoint_model_key_and_turn_history(self):
        recorder = Recorder(reply='我在。')
        instance = self.make_core(recorder)
        self.bind(instance)

        _, first = self.chat(instance, '  你好  ')
        self.assertEqual(len(recorder.calls), 1)
        request = recorder.calls[0]
        self.assertEqual(request['endpoint'], 'http://127.0.0.1:8000/v1')
        self.assertEqual(request['model'], 'DeepSeek-V4.1-Flash')
        self.assertEqual(request['api_key'], ACCESS_KEY)
        self.assertEqual(request['messages'][0],
                         {'role': 'system', 'content': PERSONA})
        self.assertEqual(request['messages'][-1],
                         {'role': 'user', 'content': '你好'})
        self.assertEqual(len(request['messages']), 2)

        self.assertTrue(instance.finalize_reply(first.delivery_token, True))
        self.assertEqual(len(instance._turns), 1)

        _, second = self.chat(instance, '再说一句')
        self.assertEqual(len(recorder.calls), 2)
        messages = recorder.calls[1]['messages']
        self.assertEqual(messages[0]['content'], PERSONA)
        self.assertEqual(messages[1],
                         {'role': 'user', 'content': '你好'})
        self.assertEqual(messages[2],
                         {'role': 'assistant', 'content': '我在。'})
        self.assertEqual(messages[3],
                         {'role': 'user', 'content': '再说一句'})
        self.assertTrue(instance.finalize_reply(second.delivery_token, True))

        files = list(self.data.iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].name, core._STATE_FILENAME)
        self.assertNotIn(ADMIN, files[0].name)

    def test_optional_admin_profile_is_separate_user_context_for_model(self):
        recorder = Recorder(reply='记住了。')
        profile = '称呼我小葱；表达自然、亲切、克制。'
        instance = self.make_core(recorder)
        instance._set_profile(profile)
        self.bind(instance)

        _, reply = self.chat(instance, '你还记得我的偏好吗？')
        messages = recorder.calls[-1]['messages']
        self.assertEqual(messages[0], {'role': 'system', 'content': PERSONA})
        self.assertEqual(messages[1], {
            'role': 'user', 'content': '管理员自述偏好（资料）：' + profile,
        })
        self.assertEqual(messages[2], {
            'role': 'user', 'content': '你还记得我的偏好吗？',
        })
        self.assertTrue(instance.finalize_reply(reply.delivery_token, True))

        # Preferences stay in the separate local profile file owned by the UI;
        # the QQ message ledger never copies them into transcript state.
        raw = self.state_path().read_text(encoding='utf-8')
        self.assertNotIn(profile, raw)
        self.assertEqual(list(self.data.iterdir()), [self.state_path()])

    def test_admin_profile_is_bounded_and_can_be_updated_for_future_turns(self):
        with self.assertRaises(ValueError):
            self.make_core(Recorder())._set_profile(
                'x' * (core._MAX_PROFILE + 1)
            )

        recorder = Recorder(reply='收到。')
        instance = self.make_core(recorder)
        self.bind(instance)
        instance._set_profile('只在未来回合生效。')
        _, reply = self.chat(instance, '测试')
        self.assertEqual(recorder.calls[-1]['messages'][1], {
            'role': 'user', 'content': '管理员自述偏好（资料）：只在未来回合生效。',
        })
        self.assertTrue(instance.finalize_reply(reply.delivery_token, True))

    def test_no_access_key_pair_code_or_delivery_token_is_written(self):
        recorder = Recorder(reply='好。')
        instance = self.make_core(recorder)
        code = instance.begin_pairing()
        self.bind(instance, code=code)
        message_id, reply = self.chat(instance, '测试')

        raw = self.state_path().read_text(encoding='utf-8')
        self.assertNotIn(ACCESS_KEY, raw)
        self.assertNotIn(code, raw)
        self.assertNotIn(reply.delivery_token, raw)
        self.assertIn(ADMIN, raw)  # local identity is the state's own data

        self.assertTrue(instance.finalize_reply(reply.delivery_token, True))
        raw = self.state_path().read_text(encoding='utf-8')
        self.assertNotIn(ACCESS_KEY, raw)
        self.assertNotIn(code, raw)
        self.assertNotIn(reply.delivery_token, raw)
        self.assertNotIn(message_id, self.state_path().name)

    def test_duplicate_message_id_and_unauthorized_text_do_not_call_model(self):
        recorder = Recorder(reply='答。')
        instance = self.make_core(recorder)

        # Before pairing, ordinary text must not create a session.
        self.assertIsNone(instance.handle_private(
            ADMIN, self.mid('unbound'), '你好'
        ))
        self.assertFalse(self.state_path().exists())
        self.assertEqual(recorder.calls, [])

        self.bind(instance)
        self.assertIsNone(instance.handle_private(
            OTHER, self.mid('unauthorized'), '你好'
        ))
        self.assertEqual(recorder.calls, [])

        message_id, reply = self.chat(instance, '第一次')
        self.assertTrue(instance.finalize_reply(reply.delivery_token, True))
        self.assertEqual(len(recorder.calls), 1)

        self.assertIsNone(instance.handle_private(
            ADMIN, message_id, '第一次'
        ))
        self.assertEqual(len(recorder.calls), 1)
        self.assertEqual(len(instance._turns), 1)

    def test_model_failure_is_generic_and_deduplicated_without_history(self):
        secret_exception = (
            f'upstream failed for {ADMIN} with {ACCESS_KEY} and SECRET-42'
        )
        recorder = Recorder()
        recorder.error = RuntimeError(secret_exception)
        instance = self.make_core(recorder)
        self.bind(instance)

        message_id = self.mid('failure')
        result = instance.handle_private(ADMIN, message_id, '会失败的消息')
        self.assertIsNotNone(result)
        self.assertEqual(result.kind, 'error')
        self.assertEqual(result.text, core._ERROR_TEXT)
        self.assertIsNone(result.delivery_token)
        self.assertNotIn('SECRET-42', result.text)
        self.assertNotIn(ADMIN, result.text)
        self.assertNotIn(ACCESS_KEY, result.text)
        self.assertEqual(len(recorder.calls), 1)
        self.assertEqual(instance._turns, [])
        self.assertIsNone(instance._pending)

        raw = self.state_path().read_text(encoding='utf-8')
        self.assertNotIn('SECRET-42', raw)
        self.assertNotIn(secret_exception, raw)

        self.assertIsNone(instance.handle_private(
            ADMIN, message_id, '会失败的消息'
        ))
        self.assertEqual(len(recorder.calls), 1)

    def test_diagnostic_codes_are_fixed_and_never_leak_request_details(self):
        codes = []
        recorder = Recorder(reply='正常答复。')
        instance = self.make_core(recorder)
        self.assertIsNone(instance.set_diagnostic_callback(codes.append))
        with self.assertRaises(TypeError):
            instance.set_diagnostic_callback('not-callable')
        self.bind(instance)
        # Binding never reaches the model, so no diagnostic is emitted.
        self.assertEqual(codes, [])

        _, reply = self.chat(instance, '正常问题')
        self.assertEqual(codes, ['model_processing'])
        self.assertTrue(instance.finalize_reply(reply.delivery_token, True))

        secret = (
            f'{ADMIN} {ACCESS_KEY} SECRET-EXC-42 '
            'Authorization: Bearer hidden-response-body'
        )
        recorder.error = RuntimeError(secret)
        result = instance.handle_private(ADMIN, self.mid('generic'), '通用失败')
        self.assertEqual(result.kind, 'error')
        self.assertEqual(codes[-2:], ['model_processing', 'model_failed_runtime'])

        recorder.error = TimeoutError(secret)
        result = instance.handle_private(ADMIN, self.mid('timeout'), '超时失败')
        self.assertEqual(result.kind, 'error')
        self.assertEqual(codes[-1], 'model_failed_timeout')

        recorder.error = ConnectionResetError(secret)
        result = instance.handle_private(
            ADMIN, self.mid('connection'), '连接失败'
        )
        self.assertEqual(result.kind, 'error')
        self.assertEqual(codes[-1], 'model_failed_connection')

        # A custom exception type name is never inspected or forwarded.
        sensitive_type = type('SENSITIVE-EXCEPTION-CLASS-NAME', (RuntimeError,), {})
        recorder.error = sensitive_type(secret)
        result = instance.handle_private(ADMIN, self.mid('custom'), '未知失败')
        self.assertEqual(result.kind, 'error')
        self.assertEqual(codes[-1], 'model_failed')

        recorder.error = None
        recorder.reply = '\x00\x01'
        result = instance.handle_private(ADMIN, self.mid('invalid'), '无效回复')
        self.assertEqual(result.kind, 'error')
        self.assertEqual(codes[-2:], ['model_processing', 'model_response_invalid'])

        # A raising diagnostic hook never changes behaviour.
        def broken(_code):
            raise RuntimeError(secret)

        instance.set_diagnostic_callback(broken)
        recorder.reply = '回调异常仍可用。'
        message_id, reply = self.chat(instance, '回调问题')
        self.assertTrue(instance.finalize_reply(reply.delivery_token, True))
        self.assertIsNone(instance.set_diagnostic_callback(None))
        result = instance.handle_private(ADMIN, self.mid('after-none'), '后续')
        self.assertEqual(result.kind, 'chat')
        self.assertTrue(instance.finalize_reply(result.delivery_token, True))

        # The hook only ever saw fixed codes, never exception, message, or
        # account material.
        self.assertTrue(set(codes) <= DIAGNOSTIC_CODES)
        rendered = repr(codes)
        for value in (ADMIN, ACCESS_KEY, 'SECRET-EXC-42', 'Authorization',
                      'hidden-response-body', 'SENSITIVE-EXCEPTION-CLASS-NAME',
                      '正常问题', '通用失败', '无效回复', '回调问题'):
            self.assertNotIn(value, rendered)

    def test_http_errors_classified_only_on_exact_fixed_message(self):
        for status, code in ((400, 'model_http_400'), (404, 'model_http_404'),
                             (500, 'model_http_500'), (502, 'model_http_502'),
                             (418, 'model_http_4xx'), (599, 'model_http_5xx')):
            message = f'本机模型返回状态 {status}，请检查服务与模型名称。'
            self.assertEqual(core._model_failure_code(SyntheticConversationError(message), SyntheticConversationError), code)
            for malformed in (message + ' SECRET', message.replace(str(status), '0'+str(status)),
                              message.replace(str(status), '６００')):
                self.assertEqual(core._model_failure_code(SyntheticConversationError(malformed), SyntheticConversationError), 'model_failed_conversation')
            self.assertNotEqual(core._model_failure_code(RuntimeError(message), SyntheticConversationError), code)

    def test_production_http_error_messages_are_classified(self):
        module = core._CONVERSATION
        cls = module.ConversationError
        for status, code in ((400, 'model_http_400'), (404, 'model_http_404'),
                             (500, 'model_http_500'), (502, 'model_http_502')):
            self.assertEqual(core._model_failure_code(cls(module._http_status_message(status)), cls), code)

    def test_model_preparation_failure_uses_fixed_code(self):
        recorder = Recorder()
        instance = self.make_core(recorder)
        codes = []
        instance.set_diagnostic_callback(codes.append)
        self.bind(instance)
        class BrokenConversation:
            class Conversation:
                @staticmethod
                def messages(*args):
                    raise ValueError('PRIVATE SECRET')
        instance._conversation = BrokenConversation
        result = instance.handle_private(ADMIN, self.mid('prepare'), 'synthetic')
        self.assertEqual(result.kind, 'error')
        self.assertEqual(codes[-1], 'model_prepare_failed')
        self.assertNotIn('PRIVATE SECRET', repr(codes))
        self.assertEqual(recorder.calls, [])

    def test_fixed_conversation_error_messages_are_exactly_classified(self):
        codes = []
        error_texts = []
        recorder = Recorder()
        instance = self.make_core(recorder)
        self.use_synthetic_conversation(instance, SyntheticConversationError)
        instance.set_diagnostic_callback(codes.append)
        self.bind(instance)

        for message, expected in FIXED_CONVERSATION_MESSAGES:
            recorder.error = SyntheticConversationError(message)
            result = instance.handle_private(
                ADMIN, self.mid('fixed-message'), '固定错误测试'
            )
            self.assertIsNotNone(result)
            self.assertEqual(result.kind, 'error')
            self.assertEqual(result.text, core._ERROR_TEXT)
            self.assertIsNone(result.delivery_token)
            error_texts.append(result.text)
            self.assertEqual(codes[-1], expected)
            # The fixed input text itself is not forwarded through the hook.
            self.assertNotIn(message, repr(codes))

        # Fixed-message failures are still deduplicated and never become turns.
        self.assertEqual(instance._turns, [])
        self.assertIsNone(instance._pending)
        self.assertTrue(set(codes) <= DIAGNOSTIC_CODES)
        self.assertIn('model_processing', codes)
        rendered = repr(codes) + repr(error_texts)
        for value in (ADMIN, ACCESS_KEY, '固定错误测试'):
            self.assertNotIn(value, rendered)

    def test_conversation_error_classification_is_exact_and_leak_free(self):
        secret = (
            f'{ADMIN} {ACCESS_KEY} SECRET-CONVERSATION-42 '
            'Authorization: Bearer hidden-response-body'
        )
        impostor_class = type('ConversationError', (Exception,), {})
        subclass = type(
            'ConversationErrorMessageSubclass', (SyntheticConversationError,), {}
        )

        def explosive(message):
            class ExplosiveError(Exception):
                def __str__(self):
                    raise AssertionError('exception was stringified')

                def __repr__(self):
                    raise AssertionError('exception was repr()d')

            return ExplosiveError(message)

        # Only the exact genuine class with exactly one exact string argument
        # and an exact allowlisted message is classified.
        cases = (
            (SyntheticConversationError(secret), 'model_failed_conversation'),
            (SyntheticConversationError(
                FIXED_CONVERSATION_MESSAGES[2][0], secret
            ), 'model_failed_conversation'),
            (SyntheticConversationError(42), 'model_failed_conversation'),
            (SyntheticConversationError(), 'model_failed_conversation'),
            (SyntheticConversationError(None), 'model_failed_conversation'),
            (impostor_class(FIXED_CONVERSATION_MESSAGES[2][0]),
             'model_failed'),
            (subclass(FIXED_CONVERSATION_MESSAGES[2][0]), 'model_failed'),
            (RuntimeError(FIXED_CONVERSATION_MESSAGES[4][0]),
             'model_failed_runtime'),
            (ValueError(secret), 'model_failed_value'),
        )
        for error, expected in cases:
            self.assertEqual(
                core._model_failure_code(error, SyntheticConversationError),
                expected,
            )
            self.assertEqual(
                core._model_failure_code(error, None),
                {RuntimeError: 'model_failed_runtime', ValueError: 'model_failed_value'}.get(type(error), 'model_failed')
            )

        # Classification reads args only; it never stringifies the exception.
        busy_error = explosive(FIXED_CONVERSATION_MESSAGES[2][0])
        self.assertEqual(
            core._model_failure_code(busy_error, type(busy_error)),
            'model_failed_busy',
        )

        # A module without the genuine class can never produce an allowlisted
        # category, even when the exception text matches exactly.
        self.assertIsNone(core._conversation_error_class(SimpleNamespace()))
        self.assertEqual(
            core._conversation_error_class(SimpleNamespace(
                ConversationError=SyntheticConversationError
            )),
            SyntheticConversationError,
        )

        # End to end: an arbitrary secret-bearing genuine error reaches the
        # hook only as the generic fixed code.
        codes = []
        recorder = Recorder()
        instance = self.make_core(recorder)
        self.use_synthetic_conversation(instance, SyntheticConversationError)
        instance.set_diagnostic_callback(codes.append)
        self.bind(instance)
        recorder.error = SyntheticConversationError(secret)
        result = instance.handle_private(
            ADMIN, self.mid('secret-conversation'), '合成敏感内容'
        )
        self.assertEqual(result.kind, 'error')
        self.assertEqual(result.text, core._ERROR_TEXT)
        self.assertEqual(codes[-1], 'model_failed_conversation')
        self.assertTrue(set(codes) <= DIAGNOSTIC_CODES)
        rendered = repr(codes) + repr(result.text)
        for value in (ADMIN, ACCESS_KEY, 'SECRET-CONVERSATION-42',
                      'Authorization', 'Bearer', 'hidden-response-body',
                      '合成敏感内容'):
            self.assertNotIn(value, rendered)

    def test_empty_or_control_only_model_output_becomes_generic_error(self):
        recorder = Recorder(reply='\x00\x01')
        instance = self.make_core(recorder)
        self.bind(instance)
        result = instance.handle_private(ADMIN, self.mid(), '你好')
        self.assertEqual(result.kind, 'error')
        self.assertEqual(result.text, core._ERROR_TEXT)
        self.assertIsNone(result.delivery_token)
        self.assertEqual(instance._turns, [])

    def test_reply_is_cleaned_and_length_bounded(self):
        recorder = Recorder(reply='甲\x00乙' + '长' * (core._MAX_REPLY + 20))
        instance = self.make_core(recorder)
        self.bind(instance)
        _, result = self.chat(instance, '你好')
        self.assertTrue(result.text.startswith('甲乙'))
        self.assertLessEqual(len(result.text), core._MAX_REPLY)
        self.assertNotIn('\x00', result.text)
        self.assertEqual(result.kind, 'chat')

    # -- delivery transaction -----------------------------------------

    def test_delivery_success_appends_turn_only_after_finalize(self):
        recorder = Recorder(reply='送达的答复。')
        instance = self.make_core(recorder)
        self.bind(instance)

        _, reply = self.chat(instance, '送达的问题')
        self.assertEqual(instance._turns, [])
        self.assertIsNotNone(instance._pending)

        # A second request cannot overwrite an in-flight delivery.
        self.assertIsNone(instance.handle_private(
            ADMIN, self.mid('while-pending'), '另一句'
        ))
        self.assertEqual(len(recorder.calls), 1)

        self.assertTrue(instance.finalize_reply(reply.delivery_token, True))
        self.assertIsNone(instance._pending)
        self.assertEqual(instance._turns, [{
            'user': '送达的问题', 'assistant': '送达的答复。'
        }])
        self.assertFalse(instance.finalize_reply(reply.delivery_token, True))

        _, second = self.chat(instance, '下一句')
        self.assertEqual(recorder.calls[-1]['messages'][1]['content'],
                         '送达的问题')
        self.assertEqual(recorder.calls[-1]['messages'][2]['content'],
                         '送达的答复。')
        self.assertTrue(instance.finalize_reply(second.delivery_token, True))

    def test_delivery_failure_never_becomes_future_context(self):
        recorder = Recorder(reply='未送达的答复。')
        instance = self.make_core(recorder)
        self.bind(instance)

        message_id, reply = self.chat(instance, '未送达的问题')
        self.assertTrue(instance.finalize_reply(reply.delivery_token, False))
        self.assertIsNone(instance._pending)
        self.assertEqual(instance._turns, [])

        # The failed input is still deduplicated.
        self.assertIsNone(instance.handle_private(
            ADMIN, message_id, '未送达的问题'
        ))
        self.assertEqual(len(recorder.calls), 1)

        self.chat(instance, '新的问题')
        messages = recorder.calls[-1]['messages']
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[-1],
                         {'role': 'user', 'content': '新的问题'})
        contents = [item['content'] for item in messages]
        self.assertNotIn('未送达的问题', contents)
        self.assertNotIn('未送达的答复。', contents)

    def test_unknown_delivery_result_survives_restart_and_never_auto_sends(self):
        recorder = Recorder(reply='可能已发出的答复。')
        instance = self.make_core(recorder)
        self.bind(instance)
        message_id, reply = self.chat(instance, '结果未知的问题')
        self.assertIsNotNone(instance._pending)

        # Simulate a process restart with the same local state and app_id.
        restarted = self.make_core(recorder)
        self.assertEqual(len(recorder.calls), 1)
        self.assertIsNotNone(restarted._pending)
        self.assertEqual(restarted._turns, [])
        self.assertIsNone(restarted.handle_private(
            ADMIN, message_id, '结果未知的问题'
        ))
        self.assertIsNone(restarted.handle_private(
            ADMIN, self.mid('after-crash'), '重启后的新消息'
        ))
        self.assertEqual(len(recorder.calls), 1)

        # A bogus token cannot claim delivery.  A retained real token may
        # resolve the preserved record without any resend.
        self.assertFalse(restarted.finalize_reply('not-the-token', True))
        self.assertEqual(restarted._turns, [])
        self.assertTrue(restarted.finalize_reply(reply.delivery_token, True))
        self.assertEqual(restarted._turns, [{
            'user': '结果未知的问题',
            'assistant': '可能已发出的答复。',
        }])

    def test_concurrent_same_message_calls_model_once(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def responder(endpoint, model, messages, api_key):
            calls.append(messages[-1]['content'])
            entered.set()
            release.wait(5)
            return '唯一答复。'

        instance = self.make_core(responder)
        self.bind(instance)
        message_id = self.mid('same')
        results = []

        def worker():
            results.append(instance.handle_private(
                ADMIN, message_id, '并发同一条'
            ))

        first = threading.Thread(target=worker)
        second = threading.Thread(target=worker)
        first.start()
        self.assertTrue(entered.wait(5))
        second.start()
        time.sleep(0.05)
        release.set()
        first.join(5)
        second.join(5)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(calls, ['并发同一条'])
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(item is not None for item in results), 1)
        chat_results = [item for item in results if item is not None]
        self.assertEqual(chat_results[0].kind, 'chat')
        self.assertTrue(instance.finalize_reply(
            chat_results[0].delivery_token, True
        ))

    def test_concurrent_different_messages_do_not_replace_pending(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def responder(endpoint, model, messages, api_key):
            calls.append(messages[-1]['content'])
            entered.set()
            release.wait(5)
            return '第一条答复。'

        instance = self.make_core(responder)
        self.bind(instance)
        results = []
        message_ids = [self.mid('different-1'), self.mid('different-2')]

        def worker(message_id):
            results.append(instance.handle_private(
                ADMIN, message_id, '第一条'
            ))

        first = threading.Thread(target=worker, args=(message_ids[0],))
        second = threading.Thread(target=worker, args=(message_ids[1],))
        first.start()
        self.assertTrue(entered.wait(5))
        second.start()
        time.sleep(0.05)
        release.set()
        first.join(5)
        second.join(5)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(calls, ['第一条'])
        chats = [item for item in results if item is not None]
        self.assertEqual(len(chats), 1)
        self.assertIsNotNone(instance._pending)
        self.assertTrue(instance.finalize_reply(
            chats[0].delivery_token, True
        ))

    # -- persistence failures, restart, bounds -------------------------

    def test_write_failure_preserves_old_bytes_and_pending(self):
        recorder = Recorder(reply='可送达。')
        instance = self.make_core(recorder)
        self.bind(instance)
        old_bytes = self.state_path().read_bytes()

        with mock.patch.object(core.os, 'replace',
                               side_effect=OSError('synthetic write failure')):
            result = instance.handle_private(
                ADMIN, self.mid('write-fail'), '写盘失败'
            )
        self.assertIsNone(result)
        self.assertEqual(self.state_path().read_bytes(), old_bytes)
        self.assertIsNone(instance._pending)
        self.assertEqual(instance._turns, [])
        self.assertEqual(list(self.data.iterdir()), [self.state_path()])

        _, reply = self.chat(instance, '先成功的待发送')
        self.assertIsNotNone(instance._pending)
        pending_bytes = self.state_path().read_bytes()
        with mock.patch.object(core.os, 'replace',
                               side_effect=OSError('synthetic write failure')):
            self.assertFalse(instance.finalize_reply(
                reply.delivery_token, True
            ))
        self.assertEqual(self.state_path().read_bytes(), pending_bytes)
        self.assertIsNotNone(instance._pending)
        self.assertEqual(instance._turns, [])

        # Once writes work again, the same pending record can be resolved.
        self.assertTrue(instance.finalize_reply(reply.delivery_token, True))
        self.assertEqual(len(instance._turns), 1)

    def test_binding_success_waits_for_durable_admin_openid(self):
        recorder = Recorder()
        instance = self.make_core(recorder)
        code = instance.begin_pairing()
        bind_message_id = self.mid('bind-write-fail')

        with mock.patch.object(core.os, 'replace',
                               side_effect=OSError('synthetic write failure')):
            result = instance.handle_private(
                ADMIN, bind_message_id, f'绑定 {code}'
            )
        self.assertIsNone(result)
        self.assertFalse(self.state_path().exists())
        self.assertIsNone(instance._admin_openid)
        self.assertIsNotNone(instance._pair_code)

        # The code was not consumed by the failed transaction.
        self.bind(instance, code=code)
        self.assertEqual(self.read_state()['admin_openid'], ADMIN)
        self.assertEqual(recorder.calls, [])

    def test_app_id_mismatch_is_rejected_without_overwrite(self):
        instance = self.make_core(Recorder())
        self.bind(instance)
        before = self.state_path().read_bytes()

        with self.assertRaises(RuntimeError):
            QQCore(self.data, 'different-app-id',
                   request_reply=Recorder())
        self.assertEqual(self.state_path().read_bytes(), before)

        reopened = self.make_core(Recorder())
        self.assertEqual(reopened._admin_openid, ADMIN)

    def test_corrupt_state_fails_closed_and_keeps_bytes(self):
        data = self.data / 'corrupt'
        data.mkdir()
        path = self.state_path(data)
        original = b'{not valid json'
        path.write_bytes(original)

        with self.assertRaises(RuntimeError):
            QQCore(data, APP_ID, request_reply=Recorder())
        self.assertEqual(path.read_bytes(), original)

    def test_restart_preserves_deduplication_and_bounded_history(self):
        calls = []

        def responder(endpoint, model, messages, api_key):
            calls.append([dict(item) for item in messages])
            return '答:' + messages[-1]['content']

        instance = self.make_core(responder)
        self.bind(instance)
        last_message_id = None
        for index in range(core._MAX_TURNS + 2):
            last_message_id, reply = self.chat(
                instance, f'消息{index}', label=f'history-{index}'
            )
            self.assertTrue(instance.finalize_reply(
                reply.delivery_token, True
            ))

        self.assertEqual(len(instance._turns), core._MAX_TURNS)
        self.assertEqual(len(self.read_state()['turns']), core._MAX_TURNS)

        restarted = self.make_core(responder)
        self.assertEqual(len(restarted._turns), core._MAX_TURNS)
        call_count = len(calls)
        self.assertIsNone(restarted.handle_private(
            ADMIN, last_message_id, '重复消息'
        ))
        self.assertEqual(len(calls), call_count)

        self.chat(restarted, '重启后的新问题', label='after-restart')
        messages = calls[-1]
        self.assertEqual(messages[0],
                         {'role': 'system', 'content': PERSONA})
        self.assertEqual(len(messages), 1 + core._MAX_TURNS * 2 + 1)
        self.assertEqual(messages[-1],
                         {'role': 'user', 'content': '重启后的新问题'})
        self.assertEqual(messages[-2]['role'], 'assistant')

    def test_processed_message_ids_are_bounded_across_restart(self):
        recorder = Recorder(reply='短答。')
        instance = self.make_core(recorder)
        self.bind(instance)

        for index in range(core._MAX_PROCESSED_IDS + 8):
            _, reply = self.chat(
                instance, f'上限消息{index}', label=f'bounded-{index}'
            )
            self.assertTrue(instance.finalize_reply(
                reply.delivery_token, False
            ))

        self.assertEqual(len(instance._processed), core._MAX_PROCESSED_IDS)
        state = self.read_state()
        self.assertEqual(
            len(state['processed_message_ids']), core._MAX_PROCESSED_IDS
        )
        restarted = self.make_core(recorder)
        self.assertEqual(len(restarted._processed), core._MAX_PROCESSED_IDS)

    # -- own account and explicit local recovery -----------------------

    def test_unbound_self_account_cannot_bind_with_a_valid_code(self):
        recorder = Recorder()
        instance = self.make_core(recorder, app_id=QQ_SELF)
        code = instance.begin_pairing()
        self.assertIsInstance(code, str)

        for sender in (QQ_SELF, '000' + QQ_SELF,
                       '000000' + QQ_SELF):
            self.assertIsNone(instance.handle_private(
                sender, self.mid('self-bind'), f'绑定 {code}'
            ))
            self.assertIsNone(instance.handle_private(
                sender, self.mid('self-chat'), '你好'
            ))

        self.assertEqual(recorder.calls, [])
        self.assertFalse(self.state_path().exists())
        self.assertIsNone(instance._admin_openid)
        # The rejected self attempts did not consume the local code.
        self.assertIsNotNone(instance._pair_code)
        self.assertEqual(instance.local_status(), {
            'bound': False,
            'pending_recovery_required': False,
        })

        self.bind(instance, code=code, openid=QQ_ADMIN)
        self.assertEqual(instance.local_status(), {
            'bound': True,
            'pending_recovery_required': False,
        })

    def test_bound_core_rejects_self_account_and_leading_zero_variants(self):
        recorder = Recorder(reply='不应出现。')
        instance = self.make_core(recorder, app_id=QQ_SELF)
        self.bind(instance, openid=QQ_ADMIN)
        before = self.state_path().read_bytes()

        for sender in (QQ_SELF, '000' + QQ_SELF,
                       '000000' + QQ_SELF):
            self.assertIsNone(instance.handle_private(
                sender, self.mid('self-bound-chat'), '你好'
            ))
            self.assertIsNone(instance.handle_private(
                sender, self.mid('self-bound-bind'), '绑定 AAAAAAAAAA'
            ))

        self.assertEqual(recorder.calls, [])
        self.assertEqual(self.state_path().read_bytes(), before)
        self.assertEqual(instance._admin_openid, QQ_ADMIN)

        # The configured own number may itself carry leading zeroes.
        padded_data = self.data / 'padded-app'
        padded = self.make_core(Recorder(), app_id='000' + QQ_SELF,
                                data=padded_data)
        padded_code = padded.begin_pairing()
        self.assertIsNone(padded.handle_private(
            QQ_SELF, self.mid('padded-self'), f'绑定 {padded_code}'
        ))
        self.assertFalse(self.state_path(padded_data).exists())

    def test_non_decimal_openid_is_not_coerced_into_the_bot_number(self):
        recorder = Recorder()
        instance = self.make_core(recorder, app_id=QQ_SELF)
        lookalike = '+' + QQ_SELF
        code = instance.begin_pairing()
        result = instance.handle_private(
            lookalike, self.mid('lookalike-bind'), f'绑定 {code}'
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.kind, 'bound')
        self.assertEqual(instance._admin_openid, lookalike)
        self.assertEqual(recorder.calls, [])

        # Opaque string identifiers still compare exactly, as before.
        opaque_data = self.data / 'opaque-app'
        opaque = self.make_core(Recorder(), app_id=APP_ID, data=opaque_data)
        opaque_code = opaque.begin_pairing()
        self.assertIsNone(opaque.handle_private(
            APP_ID, self.mid('opaque-self'), f'绑定 {opaque_code}'
        ))
        self.assertFalse(self.state_path(opaque_data).exists())

    def test_local_ui_cannot_discard_a_current_process_pending(self):
        recorder = Recorder(reply='当前进程答复。')
        instance = self.make_core(recorder, app_id=QQ_SELF)
        self.bind(instance, openid=QQ_ADMIN)
        _, reply = self.chat(instance, '当前进程问题', openid=QQ_ADMIN)
        self.assertIsNotNone(instance._pending)
        before = self.state_path().read_bytes()

        self.assertEqual(instance.local_status(), {
            'bound': True,
            'pending_recovery_required': False,
        })
        self.assertFalse(instance.discard_uncertain_pending())
        self.assertIsNotNone(instance._pending)
        self.assertEqual(self.state_path().read_bytes(), before)

        self.assertTrue(instance.finalize_reply(
            reply.delivery_token, True
        ))
        self.assertEqual(instance.local_status(), {
            'bound': True,
            'pending_recovery_required': False,
        })
        self.assertFalse(instance.discard_uncertain_pending())

    def test_in_process_unknown_send_becomes_explicitly_recoverable(self):
        recorder = Recorder(reply='结果不明答复。')
        instance = self.make_core(recorder, app_id=QQ_SELF)
        self.bind(instance, openid=QQ_ADMIN)
        message_id, reply = self.chat(
            instance, '结果不明问题', openid=QQ_ADMIN
        )
        before = self.state_path().read_bytes()
        calls_before = len(recorder.calls)

        # An in-flight send is not recoverable yet: it may still finish.
        self.assertEqual(instance.local_status(), {
            'bound': True,
            'pending_recovery_required': False,
        })
        self.assertFalse(instance.discard_uncertain_pending())
        self.assertEqual(self.state_path().read_bytes(), before)

        # Only the matching token can mark the outcome unknown, and marking
        # neither delivers, fails, resends, nor calls the model.
        self.assertFalse(instance.mark_delivery_unknown('not-the-token'))
        self.assertEqual(instance.local_status(), {
            'bound': True,
            'pending_recovery_required': False,
        })
        self.assertTrue(instance.mark_delivery_unknown(reply.delivery_token))
        self.assertEqual(instance.local_status(), {
            'bound': True,
            'pending_recovery_required': True,
        })
        self.assertEqual(self.state_path().read_bytes(), before)
        self.assertEqual(len(recorder.calls), calls_before)
        self.assertEqual(instance._turns, [])
        self.assertIsNotNone(instance._pending)

        # QQ text can never resolve an unknown local delivery.
        for text in ('恢复', '丢弃待发送', 'discard pending', '绑定 AAAAAAAAAA'):
            self.assertIsNone(instance.handle_private(
                QQ_ADMIN, self.mid('qq-unknown'), text
            ))
        self.assertEqual(len(recorder.calls), calls_before)
        self.assertTrue(
            instance.local_status()['pending_recovery_required']
        )

        # Explicit local recovery only drops the unresolved record; its input
        # stays deduplicated and no unsent answer enters future context.
        self.assertTrue(instance.discard_uncertain_pending())
        self.assertIsNone(instance._pending)
        self.assertEqual(instance._turns, [])
        self.assertFalse(
            instance.local_status()['pending_recovery_required']
        )
        raw = self.state_path().read_text(encoding='utf-8')
        self.assertNotIn('结果不明问题', raw)
        self.assertNotIn('结果不明答复。', raw)
        self.assertIn(message_id, self.read_state()['processed_message_ids'])
        self.assertFalse(instance.discard_uncertain_pending())
        self.assertIsNone(instance.handle_private(
            QQ_ADMIN, message_id, '结果不明问题'
        ))
        self.assertEqual(len(recorder.calls), calls_before)

        recorder.reply = '恢复后答复。'
        _, next_reply = self.chat(
            instance, '恢复后问题', openid=QQ_ADMIN
        )
        self.assertTrue(instance.finalize_reply(
            next_reply.delivery_token, True
        ))
        contents = [item['content'] for item in recorder.calls[-1]['messages']]
        self.assertNotIn('结果不明问题', contents)
        self.assertNotIn('结果不明答复。', contents)

    def test_marked_unknown_can_still_be_finalized_the_real_outcome(self):
        recorder = Recorder(reply='迟到确认答复。')
        instance = self.make_core(recorder, app_id=QQ_SELF)
        self.bind(instance, openid=QQ_ADMIN)
        _, reply = self.chat(instance, '迟到确认问题', openid=QQ_ADMIN)

        self.assertTrue(instance.mark_delivery_unknown(reply.delivery_token))
        # Marking unknown is not a final failure; the real token may still
        # resolve it if the adapter genuinely learns the outcome later.
        self.assertTrue(instance.finalize_reply(reply.delivery_token, True))
        self.assertEqual(instance.local_status(), {
            'bound': True,
            'pending_recovery_required': False,
        })
        self.assertEqual(instance._turns, [{
            'user': '迟到确认问题',
            'assistant': '迟到确认答复。',
        }])

    def test_mark_unknown_after_definite_failure_is_rejected(self):
        recorder = Recorder(reply='确定失败答复。')
        instance = self.make_core(recorder, app_id=QQ_SELF)
        self.bind(instance, openid=QQ_ADMIN)
        _, reply = self.chat(instance, '确定失败问题', openid=QQ_ADMIN)
        self.assertTrue(instance.finalize_reply(reply.delivery_token, False))

        self.assertFalse(instance.mark_delivery_unknown(reply.delivery_token))
        self.assertEqual(instance.local_status(), {
            'bound': True,
            'pending_recovery_required': False,
        })
        self.assertFalse(instance.discard_uncertain_pending())

    def test_restart_local_status_exposes_only_booleans(self):
        recorder = Recorder(reply='重启前答复。')
        instance = self.make_core(recorder, app_id=QQ_SELF)
        self.bind(instance, openid=QQ_ADMIN)
        message_id, _ = self.chat(instance, '重启前问题', openid=QQ_ADMIN)

        restarted = self.make_core(recorder, app_id=QQ_SELF)
        status = restarted.local_status()
        self.assertEqual(status, {
            'bound': True,
            'pending_recovery_required': True,
        })
        self.assertEqual(set(status), {'bound', 'pending_recovery_required'})
        self.assertTrue(all(type(value) is bool for value in status.values()))
        rendered = repr(status)
        for detail in (QQ_SELF, QQ_ADMIN, message_id,
                       '重启前问题', '重启前答复。'):
            self.assertNotIn(detail, rendered)
        self.assertEqual(len(recorder.calls), 1)

        # QQ text, including recovery-looking commands, cannot resolve or
        # bypass the local-only recovery step.
        for text in ('恢复', '丢弃待发送', 'discard pending',
                     '绑定 AAAAAAAAAA'):
            self.assertIsNone(restarted.handle_private(
                QQ_ADMIN, self.mid('qq-recovery'), text
            ))
        self.assertTrue(
            restarted.local_status()['pending_recovery_required']
        )
        self.assertEqual(len(recorder.calls), 1)

    def test_explicit_discard_is_local_and_keeps_history_and_dedup(self):
        recorder = Recorder(reply='已送达答复。')
        instance = self.make_core(recorder, app_id=QQ_SELF)
        self.bind(instance, openid=QQ_ADMIN)
        _, delivered = self.chat(instance, '已送达问题', openid=QQ_ADMIN)
        self.assertTrue(instance.finalize_reply(
            delivered.delivery_token, True
        ))

        recorder.reply = '未知答复。'
        unknown_id, _ = self.chat(instance, '未知问题', openid=QQ_ADMIN)
        self.assertIsNotNone(instance._pending)
        calls_before = len(recorder.calls)

        restarted = self.make_core(recorder, app_id=QQ_SELF)
        self.assertTrue(
            restarted.local_status()['pending_recovery_required']
        )
        self.assertTrue(restarted.discard_uncertain_pending())
        self.assertIsNone(restarted._pending)
        self.assertFalse(
            restarted.local_status()['pending_recovery_required']
        )
        # No model request and no QQ send happened during local recovery.
        self.assertEqual(len(recorder.calls), calls_before)
        self.assertEqual(restarted._turns, [{
            'user': '已送达问题',
            'assistant': '已送达答复。',
        }])
        raw = self.state_path().read_text(encoding='utf-8')
        self.assertNotIn('未知问题', raw)
        self.assertNotIn('未知答复。', raw)
        self.assertIn(unknown_id,
                      self.read_state()['processed_message_ids'])
        self.assertFalse(restarted.discard_uncertain_pending())

        # The preserved ID remains deduplicated and cannot call the model.
        self.assertIsNone(restarted.handle_private(
            QQ_ADMIN, unknown_id, '未知问题'
        ))
        self.assertEqual(len(recorder.calls), calls_before)

        recorder.reply = '恢复后答复。'
        _, new_reply = self.chat(
            restarted, '恢复后的新问题', openid=QQ_ADMIN
        )
        self.assertTrue(restarted.finalize_reply(
            new_reply.delivery_token, True
        ))
        messages = recorder.calls[-1]['messages']
        self.assertEqual(messages[1],
                         {'role': 'user', 'content': '已送达问题'})
        self.assertEqual(messages[2],
                         {'role': 'assistant', 'content': '已送达答复。'})
        self.assertEqual(messages[3],
                         {'role': 'user', 'content': '恢复后的新问题'})
        contents = [item['content'] for item in messages]
        self.assertNotIn('未知问题', contents)
        self.assertNotIn('未知答复。', contents)

    def test_discard_survives_another_restart_without_recovery(self):
        recorder = Recorder(reply='第一次答复。')
        instance = self.make_core(recorder, app_id=QQ_SELF)
        self.bind(instance, openid=QQ_ADMIN)
        _, first = self.chat(instance, '第一次问题', openid=QQ_ADMIN)
        self.assertTrue(instance.finalize_reply(
            first.delivery_token, True
        ))
        recorder.reply = '未定答复。'
        unknown_id, _ = self.chat(instance, '未定问题', openid=QQ_ADMIN)

        restarted = self.make_core(recorder, app_id=QQ_SELF)
        self.assertTrue(restarted.discard_uncertain_pending())
        stable_bytes = self.state_path().read_bytes()

        reopened = self.make_core(recorder, app_id=QQ_SELF)
        self.assertEqual(reopened.local_status(), {
            'bound': True,
            'pending_recovery_required': False,
        })
        self.assertIsNone(reopened._pending)
        self.assertFalse(reopened.discard_uncertain_pending())
        self.assertEqual(self.state_path().read_bytes(), stable_bytes)
        self.assertEqual(reopened._turns, [{
            'user': '第一次问题',
            'assistant': '第一次答复。',
        }])
        self.assertIn(unknown_id, reopened._processed)

        recorder.reply = '后续答复。'
        _, reply = self.chat(reopened, '后续问题', openid=QQ_ADMIN)
        self.assertTrue(reopened.finalize_reply(
            reply.delivery_token, True
        ))

    def test_discard_write_failure_keeps_bytes_and_recovery_state(self):
        recorder = Recorder(reply='待恢复答复。')
        instance = self.make_core(recorder, app_id=QQ_SELF)
        self.bind(instance, openid=QQ_ADMIN)
        self.chat(instance, '待恢复问题', openid=QQ_ADMIN)

        restarted = self.make_core(recorder, app_id=QQ_SELF)
        before = self.state_path().read_bytes()
        pending_before = dict(restarted._pending)
        with mock.patch.object(
                core.os, 'replace',
                side_effect=OSError('synthetic write failure')):
            self.assertFalse(restarted.discard_uncertain_pending())

        self.assertEqual(self.state_path().read_bytes(), before)
        self.assertEqual(restarted._pending, pending_before)
        self.assertEqual(restarted._turns, [])
        self.assertEqual(restarted._admin_openid, QQ_ADMIN)
        self.assertEqual(restarted.local_status(), {
            'bound': True,
            'pending_recovery_required': True,
        })
        self.assertEqual(list(self.data.iterdir()), [self.state_path()])

        self.assertTrue(restarted.discard_uncertain_pending())
        self.assertIsNone(restarted._pending)
        self.assertFalse(
            restarted.local_status()['pending_recovery_required']
        )

    def test_restart_finalize_with_original_token_updates_recovery(self):
        recorder = Recorder(reply='可恢复答复。')
        instance = self.make_core(recorder, app_id=QQ_SELF)
        self.bind(instance, openid=QQ_ADMIN)
        message_id, reply = self.chat(
            instance, '可恢复问题', openid=QQ_ADMIN
        )

        restarted = self.make_core(recorder, app_id=QQ_SELF)
        self.assertEqual(restarted.local_status(), {
            'bound': True,
            'pending_recovery_required': True,
        })
        self.assertFalse(restarted.finalize_reply('not-the-token', True))
        self.assertTrue(
            restarted.local_status()['pending_recovery_required']
        )
        self.assertTrue(restarted.finalize_reply(
            reply.delivery_token, True
        ))
        self.assertEqual(restarted.local_status(), {
            'bound': True,
            'pending_recovery_required': False,
        })
        self.assertIsNone(restarted._pending)
        self.assertEqual(restarted._turns, [{
            'user': '可恢复问题',
            'assistant': '可恢复答复。',
        }])
        self.assertIn(message_id, restarted._processed)
        self.assertFalse(restarted.discard_uncertain_pending())

        reopened = self.make_core(recorder, app_id=QQ_SELF)
        self.assertEqual(reopened.local_status(), {
            'bound': True,
            'pending_recovery_required': False,
        })

    def test_restart_finalize_false_clears_recovery_without_history(self):
        recorder = Recorder(reply='未送达重启答复。')
        instance = self.make_core(recorder, app_id=QQ_SELF)
        self.bind(instance, openid=QQ_ADMIN)
        message_id, reply = self.chat(
            instance, '未送达重启问题', openid=QQ_ADMIN
        )

        restarted = self.make_core(recorder, app_id=QQ_SELF)
        self.assertTrue(
            restarted.local_status()['pending_recovery_required']
        )
        self.assertTrue(restarted.finalize_reply(
            reply.delivery_token, False
        ))
        self.assertFalse(
            restarted.local_status()['pending_recovery_required']
        )
        self.assertIsNone(restarted._pending)
        self.assertEqual(restarted._turns, [])
        self.assertIn(message_id, restarted._processed)
        self.assertIsNone(restarted.handle_private(
            QQ_ADMIN, message_id, '未送达重启问题'
        ))

        recorder.reply = '后续答复。'
        _, next_reply = self.chat(
            restarted, '后续问题', openid=QQ_ADMIN
        )
        self.assertTrue(restarted.finalize_reply(
            next_reply.delivery_token, True
        ))

    def test_cores_on_one_directory_share_the_in_process_state_lock(self):
        first = self.make_core(Recorder())
        second = self.make_core(Recorder())
        other_dir = self.data / 'other-lock'
        other = self.make_core(Recorder(), data=other_dir)

        # Two cores pointed at the same directory serialise through one lock;
        # an unrelated data directory remains independent.
        self.assertIs(first._lock, second._lock)
        self.assertIsNot(first._lock, other._lock)

    # -- public surface -------------------------------------------------

    def test_public_surface_is_the_agreed_core_api_only(self):
        self.assertEqual(core.__all__, ['QQCore', 'CoreReply'])
        self.assertEqual(
            list(inspect.signature(QQCore).parameters),
            ['data_dir', 'app_id', 'request_reply'],
        )
        self.assertFalse(hasattr(core, 'handle_group'))
        self.assertFalse(hasattr(QQCore, 'handle_group'))
        self.assertFalse(hasattr(QQCore, 'handle_room'))
        self.assertFalse(hasattr(QQCore, 'send_message'))
        for name in ('begin_pairing', 'handle_private', 'finalize_reply',
                     'mark_delivery_unknown', 'local_status',
                     'discard_uncertain_pending', 'set_diagnostic_callback'):
            self.assertTrue(callable(getattr(QQCore, name)))

        # The original positional construction and keyword construction both
        # remain valid; no diagnostic field was added to CoreReply.
        reply = CoreReply('x', 'error')
        self.assertEqual(reply.text, 'x')
        self.assertEqual(reply.kind, 'error')
        self.assertIsNone(reply.delivery_token)
        keyword_reply = CoreReply(text='y', kind='chat')
        self.assertEqual(keyword_reply.text, 'y')
        self.assertEqual(keyword_reply.kind, 'chat')
        self.assertIsNone(keyword_reply.delivery_token)


if __name__ == '__main__':
    unittest.main()
