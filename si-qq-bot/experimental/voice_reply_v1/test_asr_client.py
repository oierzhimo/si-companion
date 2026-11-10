import json
import unittest
from asr_client import transcribe_wav
from test_voice_reply import sample
from voice_reply import VoiceError


class Response:
    def __init__(self, status, body):
        self.status, self.body, self.closed = status, body, False
    def read(self, limit):
        return self.body[:limit]
    def close(self):
        self.closed = True


class Connection:
    def __init__(self, response):
        self.response, self.closed = response, False
        self.call = None
    def request(self, method, path, body, headers):
        self.call = (method, path, body, headers)
    def getresponse(self):
        return self.response
    def close(self):
        self.closed = True


class AsrTests(unittest.TestCase):
    def test_local_multipart_and_result(self):
        response = Response(200, json.dumps({'text': '管理员，你好'}).encode())
        conn = Connection(response)
        def factory(host, port, timeout):
            self.assertEqual((host, port), ('127.0.0.1', 3900))
            return conn
        audio = sample()
        self.assertEqual(transcribe_wav(audio, connection_factory=factory), '管理员，你好')
        self.assertEqual(conn.call[:2], ('POST', '/transcribe'))
        self.assertIn(audio, conn.call[2])
        self.assertTrue(conn.closed and response.closed)

    def test_missing_model_does_not_trigger_download(self):
        response = Response(409, b'{}')
        conn = Connection(response)
        with self.assertRaises(VoiceError):
            transcribe_wav(sample(), connection_factory=lambda *a, **k: conn)
        self.assertTrue(conn.closed and response.closed)

    def test_bad_result_and_invalid_audio_are_rejected(self):
        conn = Connection(Response(200, b'{"text":""}'))
        with self.assertRaises(VoiceError):
            transcribe_wav(sample(), connection_factory=lambda *a, **k: conn)
        with self.assertRaises(VoiceError):
            transcribe_wav(b'bad', connection_factory=lambda *a, **k: self.fail('called'))


if __name__ == '__main__':
    unittest.main()
