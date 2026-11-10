import base64
import io
import unittest
import wave

from voice_reply import (MAX_AUDIO_BYTES, VoiceError, join_pcm, prepare_reply,
                         reply_mode_command, split_text, validate_audio)


def sample(rate=24000, channels=1, frames=4800):
    data = io.BytesIO()
    with wave.open(data, 'wb') as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b'\x01\x00' * (frames * channels))
    return data.getvalue()


class VoiceReplyTests(unittest.TestCase):
    def test_mode_commands_are_exact(self):
        self.assertEqual(reply_mode_command(' /语音 '), 'voice')
        self.assertEqual(reply_mode_command('/文字'), 'text')
        self.assertIsNone(reply_mode_command('我喜欢语音'))
        self.assertIsNone(reply_mode_command('/语音 你好'))

    def test_success_is_record_with_exact_audio_and_retains_text(self):
        audio = sample()
        result = prepare_reply('管理员，我在。', enabled=True, synthesize=lambda _: audio)
        self.assertTrue(result.voice_ready)
        self.assertEqual(result.text, '管理员，我在。')
        self.assertEqual(result.segments[0]['type'], 'record')
        file = result.segments[0]['data']['file']
        self.assertTrue(file.startswith('base64://'))
        self.assertEqual(base64.b64decode(file[9:]), audio)
        self.assertNotIn('管理员', repr(result))
        self.assertNotIn('base64://', repr(result))

    def test_disabled_and_long_text_never_call_synth(self):
        def forbidden(_):
            raise AssertionError('should not synthesize')
        for text, enabled in [('你好', False), ('长' * 601, True)]:
            result = prepare_reply(text, enabled=enabled, synthesize=forbidden)
            self.assertFalse(result.voice_ready)
            self.assertEqual(result.segments[0]['data']['text'], text)

    def test_failure_retains_text_without_sending(self):
        def fail(_):
            raise VoiceError('down')
        result = prepare_reply('原始回复', enabled=True, synthesize=fail)
        self.assertFalse(result.voice_ready)
        self.assertEqual(result.text, result.segments[0]['data']['text'])

    def test_long_reply_splits_and_all_parts_are_prepared(self):
        text = '你好。' * 80
        seen = []
        def fake(chunk):
            seen.append(chunk)
            return sample()
        result = prepare_reply(text, enabled=True, synthesize=fake)
        self.assertTrue(result.voice_ready)
        self.assertEqual(''.join(seen), text)
        self.assertTrue(all(len(c) <= 120 for c in seen))
        self.assertEqual(len(result.segments), 1)
        self.assertEqual(result.segments[0]['type'], 'record')
        combined = base64.b64decode(result.segments[0]['data']['file'][9:])
        with wave.open(io.BytesIO(combined), 'rb') as reader:
            self.assertEqual(reader.getnframes(), len(seen) * 4800)
            self.assertEqual(reader.readframes(reader.getnframes()), b'\x01\x00' * (len(seen) * 4800))

    def test_different_pcm_parameters_fall_back_to_full_text(self):
        for incompatible in (sample(rate=16000), sample(channels=2)):
            samples = iter([sample(), incompatible])
            text = '你好。' * 80
            result = prepare_reply(text, enabled=True, synthesize=lambda _: next(samples))
            self.assertFalse(result.voice_ready)
            self.assertEqual(result.segments[0]['data']['text'], text)

    def test_combined_duration_and_bytes_are_bounded(self):
        # Each individual clip is valid, but combining them breaches a limit.
        with self.assertRaises(VoiceError):
            join_pcm([sample(rate=8000, frames=24 * 8000)] * 2)
        with self.assertRaises(VoiceError):
            join_pcm([sample(rate=48000, frames=12 * 48000)] * 2)
        for audio in (sample(rate=8000, frames=24 * 8000),
                      sample(rate=48000, frames=12 * 48000)):
            text = '你好。' * 80
            result = prepare_reply(text, enabled=True, synthesize=lambda _: audio)
            self.assertFalse(result.voice_ready)
            self.assertEqual(result.segments[0]['data']['text'], text)

    def test_later_synthesis_failure_returns_whole_text(self):
        calls = []
        def fake(chunk):
            calls.append(chunk)
            if len(calls) > 1:
                raise VoiceError('failed')
            return sample()
        text = '你好。' * 80
        result = prepare_reply(text, enabled=True, synthesize=fake)
        self.assertFalse(result.voice_ready)
        self.assertEqual(result.segments, ({'type': 'text', 'data': {'text': text}},))

    def test_split_keeps_all_internal_characters(self):
        text = ('甲乙\n丙丁，' * 35) + '尾'
        parts = split_text(text)
        self.assertEqual(''.join(parts), text)
        self.assertTrue(all(len(c) <= 120 for c in parts))

    def test_invalid_audio_rejected_and_falls_back(self):
        for data in (b'bad', b'\x00' * (MAX_AUDIO_BYTES + 1), sample()[:-4]):
            with self.assertRaises(VoiceError):
                validate_audio(data)
            self.assertFalse(prepare_reply('一句话', enabled=True,
                                           synthesize=lambda _: data).voice_ready)


if __name__ == '__main__':
    unittest.main()
