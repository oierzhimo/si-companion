import json
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import model_connection as mc
from test_model_connection import FakeOpener


class GenerationContractTests(unittest.TestCase):
    def test_local_json_schema_enforces_count_and_is_presented_as_plain_text(self):
        raw=json.dumps({'choices':[{'message':{'content':json.dumps({'items':['first','second','third']})}}]}).encode()
        opener=FakeOpener(raw)
        reply=mc.request_reply('http://127.0.0.1:11434/v1','qwen2.5vl:7b',
            [{'role':'user','content':'写三句晚安留言'}],'',provider=mc.LOCAL_PROVIDER,opener=opener,generation_count=3)
        self.assertEqual(reply,'first\nsecond\nthird')
        payload=json.loads(opener.requests[0][0].data)
        schema=payload['response_format']['json_schema']['schema']['properties']['items']
        self.assertEqual(schema['minItems'],3);self.assertEqual(schema['maxItems'],3)
        self.assertEqual(opener.requests[0][1],mc.LOCAL_MODEL_REQUEST_TIMEOUT)

    def test_invalid_structured_response_never_becomes_raw_json_reply(self):
        for content in ('not JSON',json.dumps({'items':['only one']})):
            raw=json.dumps({'choices':[{'message':{'content':content}}]}).encode()
            with self.assertRaises(mc.ModelConnectionError):
                mc.request_reply('http://127.0.0.1:11434/v1','qwen2.5vl:7b',
                    [{'role':'user','content':'写三句'}],'',provider=mc.LOCAL_PROVIDER,
                    opener=FakeOpener(raw),generation_count=3)

    def test_requested_prose_length_is_transient_and_never_a_json_item_count(self):
        text='写一段400字左右的关于管理员的介绍'
        raw=json.dumps({'choices':[{'message':{'content':'合成介绍正文'}}]}).encode()
        opener=FakeOpener(raw)
        reply=mc.request_reply('http://127.0.0.1:11434/v1','qwen2.5vl:7b',
            [{'role':'user','content':text}],'',provider=mc.LOCAL_PROVIDER,
            opener=opener)
        self.assertEqual(reply,'合成介绍正文')
        self.assertEqual(len(opener.requests),1)
        payload=json.loads(opener.requests[0][0].data)
        self.assertNotIn('response_format',payload)
        self.assertGreater(payload['max_tokens'],700)
        self.assertLessEqual(payload['max_tokens'],mc.MAX_LENGTH_TOKENS)
        instructions='\n'.join(
            str(item.get('content','')) for item in payload['messages']
            if item.get('role')=='system'
        )
        self.assertIn('篇幅要求：',instructions)
        self.assertIn('85%',instructions)
        self.assertIn('115%',instructions)
        # The user's own turn is unchanged and the extra instruction is
        # transient (added only to this request payload).
        self.assertEqual(payload['messages'][-1],
                         {'role':'user','content':text})

    def test_explicit_requested_chars_has_a_bounded_token_budget(self):
        for requested in (1,400,2000):
            opener=FakeOpener(json.dumps({'choices':[{'message':{'content':'x'}}]}).encode())
            mc.request_reply('http://127.0.0.1:11434/v1','qwen2.5vl:7b',
                [{'role':'user','content':'写一段介绍'}],'',provider=mc.LOCAL_PROVIDER,
                opener=opener,requested_chars=requested)
            payload=json.loads(opener.requests[0][0].data)
            self.assertGreaterEqual(payload['max_tokens'],700)
            self.assertLessEqual(payload['max_tokens'],mc.MAX_LENGTH_TOKENS)
        opener=FakeOpener(json.dumps({'choices':[{'message':{'content':'x'}}]}).encode())
        mc.request_reply('http://127.0.0.1:11434/v1','qwen2.5vl:7b',
            [{'role':'user','content':'写一段介绍'}],'',provider=mc.LOCAL_PROVIDER,
            opener=opener,requested_chars=0)
        payload=json.loads(opener.requests[0][0].data)
        self.assertFalse(any(
            '篇幅要求：' in str(item.get('content',''))
            for item in payload['messages']
        ))


if __name__=='__main__':unittest.main()
