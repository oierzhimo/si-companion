import io
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from si_pet.voice_client import VoiceClient,VoiceUnavailable
from si_pet.voice import scaled_wav


def sample_wav():
    stream=io.BytesIO()
    with wave.open(stream,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000)
        wav.writeframes((b'\x01\x00\xff\xff')*6000)
    return stream.getvalue()


class FakeVoiceClient(VoiceClient):
    def __init__(self,cache_dir):super().__init__(cache_dir);self.calls=[];self.profiles=[{'id':'06332768'}];self.audio=sample_wav()
    def _request(self,path,data=None):
        self.calls.append((path,data))
        if path=='/profiles':return json.dumps(self.profiles).encode()
        if path=='/generate':return self.audio
        raise AssertionError(path)


class VoiceClientTests(unittest.TestCase):
    def test_valid_profile_generates_then_uses_cache(self):
        with tempfile.TemporaryDirectory() as root:
            client=FakeVoiceClient(root)
            one=client.render('管理员，我在。','06332768')
            two=client.render('管理员，我在。','06332768')
            self.assertEqual(one,two);self.assertEqual(one.read_bytes(),client.audio)
            self.assertEqual([p for p,_ in client.calls],['/profiles','/generate','/profiles'])
            self.assertIn(b'profile_id=06332768',client.calls[1][1])
    def test_missing_profile_never_generates_with_a_different_voice(self):
        with tempfile.TemporaryDirectory() as root:
            client=FakeVoiceClient(root);client.profiles=[]
            with self.assertRaises(VoiceUnavailable):client.render('管理员，我在。','06332768')
            self.assertEqual([p for p,_ in client.calls],['/profiles'])
    def test_invalid_or_silent_audio_not_cached(self):
        with tempfile.TemporaryDirectory() as root:
            client=FakeVoiceClient(root);client.audio=b'not a wav'
            with self.assertRaises(VoiceUnavailable):client.render('管理员，我在。','06332768')
            self.assertFalse(list(Path(root).glob('*.wav')))
    def test_service_is_loopback_only(self):
        with self.assertRaises(ValueError):VoiceClient('unused','http://example.com:3900')
    def test_volume_scales_pcm_without_changing_source(self):
        with tempfile.TemporaryDirectory() as root:
            source=Path(root)/'voice.wav';source.write_bytes(sample_wav())
            quiet=scaled_wav(source,50)
            self.assertNotEqual(quiet,source)
            with wave.open(str(source),'rb') as original,wave.open(str(quiet),'rb') as scaled:
                self.assertEqual(original.getparams()[:4],scaled.getparams()[:4])
                self.assertNotEqual(original.readframes(10),scaled.readframes(10))


if __name__=='__main__':unittest.main()
