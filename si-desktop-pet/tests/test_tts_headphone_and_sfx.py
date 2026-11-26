"""Tests for smart headphone/game TTS policy, onomatopoeia preservation, and Foley action SFX."""
from __future__ import annotations

import http.client
import io
import json
import math
import os
import struct
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QRegion
from PySide6.QtWidgets import QApplication

from si_pet import chat_backend, native
from si_pet.chat_ui import MultimodalDialog
from si_pet.controller import Controller
from si_pet.conversation_ui import ConversationDialog
from si_pet.speech_sfx import (
    AudioSegment,
    assemble_speech_and_sfx,
    build_speech_plan,
    clean_bubble_text,
    render_sfx_wav,
    split_tts_chunks,
)
from si_pet.storage import Store
from si_pet.voice import VoiceEngine
from si_pet.voice_client import VoiceClient, validate_wav


def _make_sine_wav(duration_s: float = 0.28, rate: int = 24000, freq: float = 440.0) -> bytes:
    n = max(1, int(rate * duration_s))
    buf = io.BytesIO()
    frames = bytearray()
    for i in range(n):
        s = int(8000 * math.sin(2.0 * math.pi * freq * (i / rate)))
        frames += struct.pack('<h', s)
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


class _FakeSprites:
    def __init__(self):
        names = (
            'idle', 'jumping', 'walk-right', 'walk-left', 'sitting',
            'sleeping', 'waving', 'running', 'review', 'special', 'failed',
        )
        self.frames = {name: [0] for name in names}
        self.durations = {name: [100] for name in names}
        self._mask = QRegion(0, 0, 192, 208)

    def mask(self, state, index, scale):
        return QRegion(self._mask)

    def index(self, state, elapsed_ms):
        return 0


class HeadphoneAndGamePolicyTests(unittest.TestCase):
    def test_audio_endpoint_classification(self):
        # FormFactor 3 (Headphones) & 5 (Headset) -> True
        self.assertTrue(native._classify_audio_endpoint(3, 'Speakers (Realtek(R) Audio)'))
        self.assertTrue(native._classify_audio_endpoint(5, 'USB Audio Device'))
        # USB / Bluetooth headphone friendly names with generic FormFactor -> True
        self.assertTrue(native._classify_audio_endpoint(1, '耳机 (AirPods Pro)'))
        self.assertTrue(native._classify_audio_endpoint(10, 'HyperX Cloud II Wireless Headset'))
        self.assertTrue(native._classify_audio_endpoint(1, 'Sony WH-1000XM5'))
        # Monitor HDMI/DP & SPDIF Digital Output -> False
        self.assertFalse(native._classify_audio_endpoint(9, '5 - K2421UF (AMD High Definition Audio Device)'))
        self.assertFalse(native._classify_audio_endpoint(8, 'Realtek Digital Output (Realtek(R) Audio)'))
        self.assertFalse(native._classify_audio_endpoint(1, '扬声器 (Realtek(R) Audio)'))
        self.assertFalse(native._classify_audio_endpoint(None, ''))

    def test_blocked_game_detection_apex_r6_and_custom(self):
        with mock.patch('si_pet.native._list_process_names', return_value={'explorer.exe', 'r5apex_dx12.exe'}):
            self.assertEqual(native.running_blocked_game(force_refresh=True), 'r5apex_dx12.exe')

        with mock.patch('si_pet.native._list_process_names', return_value={'explorer.exe', 'rainbowsix_vulkan.exe'}):
            self.assertEqual(native.running_blocked_game(force_refresh=True), 'rainbowsix_vulkan.exe')

        with mock.patch('si_pet.native._list_process_names', return_value={'explorer.exe', 'my_local_fps.exe'}):
            self.assertIsNone(native.running_blocked_game(force_refresh=True))
            self.assertEqual(
                native.running_blocked_game(extra_names='my_local_fps, other_game.exe', force_refresh=True),
                'my_local_fps.exe',
            )

    def test_store_defaults_enable_voice_when_headphone_and_no_game(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'settings.json')
            self.assertTrue(store['voice_auto_headphone'])
            self.assertFalse(store['voice_enabled'])

            # 1. Headphones active + no game -> Voice + Text allowed!
            with mock.patch('si_pet.native._detect_default_audio_endpoint', return_value=(3, 'Headphones (Realtek)')), \
                 mock.patch('si_pet.native._list_process_names', return_value={'explorer.exe'}):
                ok, reason = native.voice_output_allowed(store, force_refresh=True)
                self.assertTrue(ok)
                self.assertIn('耳机', reason)

            # 2. Headphones active + Apex Legends running -> Suppressed (Text only)!
            with mock.patch('si_pet.native._detect_default_audio_endpoint', return_value=(3, 'Headphones (Realtek)')), \
                 mock.patch('si_pet.native._list_process_names', return_value={'explorer.exe', 'r5apex.exe'}):
                ok, reason = native.voice_output_allowed(store, force_refresh=True)
                self.assertFalse(ok)
                self.assertIn('r5apex.exe', reason)

            # 3. Headphones active + Rainbow Six running -> Suppressed (Text only)!
            with mock.patch('si_pet.native._detect_default_audio_endpoint', return_value=(5, 'HyperX Headset')), \
                 mock.patch('si_pet.native._list_process_names', return_value={'explorer.exe', 'rainbowsix.exe'}):
                ok, reason = native.voice_output_allowed(store, force_refresh=True)
                self.assertFalse(ok)
                self.assertIn('rainbowsix.exe', reason)

            # 4. Monitor speakers only (no headphones) -> Suppressed by default!
            with mock.patch('si_pet.native._detect_default_audio_endpoint', return_value=(9, '5 - K2421UF (AMD High Definition Audio Device)')), \
                 mock.patch('si_pet.native._list_process_names', return_value={'explorer.exe'}):
                ok, reason = native.voice_output_allowed(store, force_refresh=True)
                self.assertFalse(ok)
                self.assertIn('未检测到耳机', reason)


class SpeechAndFoleyTests(unittest.TestCase):
    def test_onomatopoeia_and_stage_actions_build_expected_plan(self):
        reply = (
            '（祀耳尖微红，轻咳了一声，执笔在宣纸上画了一道墨痕）'
            '||咳咳，谁担心你了！哼，这杯茶给你便是。（把温热的茶盏推了推，尾巴轻轻拍了下桌面）[表情:害羞]'
        )
        plan = build_speech_plan(reply)
        sfx_cues = [seg.value for seg in plan if seg.kind == 'sfx']
        tts_spans = [seg.value for seg in plan if seg.kind == 'tts']

        # Brush stroke + pause + porcelain tea clink + stamp/tap Foley sounds are generated
        self.assertIn('brush', sfx_cues)
        self.assertIn('pause', sfx_cues)
        self.assertIn('tea', sfx_cues)

        # Spoken onomatopoeia ("咳咳", "哼") are preserved and NOT duplicated into "咳咳，咳咳"
        joined_tts = ' '.join(tts_spans)
        self.assertIn('咳咳，谁担心你了', joined_tts)
        self.assertNotIn('咳咳，咳咳', joined_tts)
        self.assertIn('哼，这杯茶给你便是', joined_tts)
        # Stage narration prose itself is NOT read aloud literally
        self.assertNotIn('耳尖微红', joined_tts)
        self.assertNotIn('执笔在宣纸上', joined_tts)

    def test_bracket_vocal_cue_injected_when_dialogue_lacks_it(self):
        reply = '（祀噗嗤一笑，指尖敲了敲桌面）你画的这是小葱还是青龙呀？'
        plan = build_speech_plan(reply)
        self.assertEqual(plan[0], AudioSegment('sfx', 'tap'))
        tts_text = ''.join(seg.value for seg in plan if seg.kind == 'tts')
        self.assertTrue(tts_text.startswith('噗嗤，'))
        self.assertIn('小葱还是青龙', tts_text)

    def test_all_foley_cues_produce_valid_16bit_wav(self):
        for cue in ('brush', 'stamp', 'tap', 'tea', 'rustle', 'rustle_soft', 'step', 'chime_happy'):
            wav_bytes = render_sfx_wav(cue, sample_rate=24000, channels=1)
            validate_wav(wav_bytes)

    def test_long_reply_with_sfx_assembles_seamlessly(self):
        long_reply = (
            '（祀轻轻叹了口气，转身走近桌案，提笔在卷轴上写下几行批注）||'
            + '唔……既然你都问到这份上了，那我就破例给你讲清楚其中的门道吧，听好了可不许走神哦。' * 3
            + '（啪嗒一声在末尾盖了个小印章）[表情:得意]'
        )
        self.assertGreater(len(long_reply), 160)
        synth_calls = []

        def fake_synth(span: str) -> bytes:
            self.assertLessEqual(len(span), 95)
            synth_calls.append(span)
            return _make_sine_wav(0.25, rate=24000)

        combined = assemble_speech_and_sfx(long_reply, fake_synth)
        validate_wav(combined)
        self.assertGreaterEqual(len(synth_calls), 3)
        self.assertTrue(any(s.startswith('唉……') for s in synth_calls))

    def test_clean_bubble_text_strips_ui_tags_cleanly(self):
        raw = '（祀晃了晃尾巴）||哼，我在呢！[表情:开心][语音]'
        self.assertEqual(clean_bubble_text(raw), '（祀晃了晃尾巴） 哼，我在呢！')


class SimultaneousVoiceAndTextIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(['test', '-platform', 'offscreen'])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

        class _VoiceStubHandler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            generated_texts: list[str] = []

            def log_message(self, *_args):
                pass

            def do_GET(self):
                if self.path == '/health':
                    body = b'{"status":"ok"}'
                elif self.path == '/profiles':
                    body = json.dumps([{'id': '06332768', 'name': '祀'}]).encode('utf-8')
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get('Content-Length', '0'))
                raw = self.rfile.read(length).decode('utf-8')
                params = urllib.parse.parse_qs(raw)
                type(self).generated_texts.append(params.get('text', [''])[0])
                wav = _make_sine_wav(0.24, rate=24000)
                self.send_response(200)
                self.send_header('Content-Type', 'audio/wav')
                self.send_header('Content-Length', str(len(wav)))
                self.end_headers()
                self.wfile.write(wav)

        self.handler_cls = _VoiceStubHandler
        self.handler_cls.generated_texts = []
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), _VoiceStubHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_httpd)

        port = self.httpd.server_port

        class _RedirectConn(http.client.HTTPConnection):
            def __init__(self, host, p=3900, *args, **kwargs):
                if str(host) == '127.0.0.1' and p == 3900:
                    p = port
                super().__init__(host, p, *args, **kwargs)

        p_http = mock.patch.object(http.client, 'HTTPConnection', _RedirectConn)
        p_http.start()
        self.addCleanup(p_http.stop)

    def _stop_httpd(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(2.0)

    def _pump_until(self, pred, timeout=4.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents(QEventLoop.AllEvents, 20)
            if pred():
                return True
            time.sleep(0.01)
        return bool(pred())

    def test_multimodal_dialog_auto_speaks_with_headphone_and_mutes_on_apex(self):
        store = Store(self.root / 'settings.json')
        with mock.patch('si_pet.controller.native.window_platforms', return_value=[]):
            ctrl = Controller(store, _FakeSprites())

        played = []
        fake_winsound = mock.MagicMock()
        fake_winsound.SND_FILENAME = 0x20000
        fake_winsound.SND_NODEFAULT = 0x2
        fake_winsound.PlaySound.side_effect = lambda path, flags=0: played.append((path, flags))

        # Case A: Headphones connected + No game running -> Simultaneous Voice + Text by default!
        with mock.patch('si_pet.native._detect_default_audio_endpoint', return_value=(3, 'Headphones (Realtek)')), \
             mock.patch('si_pet.native._list_process_names', return_value={'explorer.exe'}), \
             mock.patch('si_pet.chat_ui.winsound', fake_winsound), \
             mock.patch(
                 'si_pet.chat_ui.request_reply',
                 return_value='（祀执笔画了一道墨痕，轻咳一声）||咳咳，哼，我在听呢！',
             ):
            native.default_audio_endpoint_info(force_refresh=True)
            native.running_blocked_game(force_refresh=True)
            dlg = MultimodalDialog(ctrl, self.root / 'mm_a.json')
            self.addCleanup(dlg.close)
            self.assertTrue(dlg.voice_mode)

            dlg.input.setPlainText('在吗？')
            dlg.submit()
            self.assertTrue(self._pump_until(lambda: not dlg.busy))
            self.assertTrue(self._pump_until(lambda: len(played) == 1))

            # Both text transcript AND voice with onomatopoeia/SFX were produced!
            self.assertIn('咳咳，哼，我在听呢', dlg.transcript.toPlainText())
            self.assertTrue(any('咳咳，哼，我在听呢' in t for t in self.handler_cls.generated_texts))

        # Case B: Headphones connected + Rainbow Six running -> Text only, zero voice calls!
        self.handler_cls.generated_texts.clear()
        played.clear()
        with mock.patch('si_pet.native._detect_default_audio_endpoint', return_value=(3, 'Headphones (Realtek)')), \
             mock.patch('si_pet.native._list_process_names', return_value={'explorer.exe', 'rainbowsix.exe'}), \
             mock.patch('si_pet.chat_ui.winsound', fake_winsound), \
             mock.patch('si_pet.chat_ui.request_reply', return_value='专心打你的彩六吧，我不出声吵你。'):
            native.default_audio_endpoint_info(force_refresh=True)
            native.running_blocked_game(force_refresh=True)
            dlg2 = MultimodalDialog(ctrl, self.root / 'mm_b.json')
            self.addCleanup(dlg2.close)
            self.assertFalse(dlg2.voice_mode)

            dlg2.input.setPlainText('开了一把彩六')
            dlg2.submit()
            self.assertTrue(self._pump_until(lambda: not dlg2.busy))
            self.assertIn('专心打你的彩六吧', dlg2.transcript.toPlainText())
            self.assertEqual(self.handler_cls.generated_texts, [])
            self.assertEqual(played, [])

    def test_voice_engine_speaks_long_reply_and_stops_if_apex_starts(self):
        store = Store(self.root / 'settings2.json')
        played = []
        fake_winsound = mock.MagicMock()
        fake_winsound.SND_FILENAME = 0x20000
        fake_winsound.SND_ASYNC = 0x1
        fake_winsound.SND_NODEFAULT = 0x2
        fake_winsound.PlaySound.side_effect = lambda path, flags=0: played.append((path, flags))

        with mock.patch('si_pet.voice.data_root', return_value=self.root), \
             mock.patch('si_pet.voice.winsound', fake_winsound), \
             mock.patch('si_pet.native._detect_default_audio_endpoint', return_value=(5, 'Headset')), \
             mock.patch('si_pet.native._list_process_names', return_value={'explorer.exe'}):
            native.default_audio_endpoint_info(force_refresh=True)
            native.running_blocked_game(force_refresh=True)
            engine = VoiceEngine(store)
            self.addCleanup(engine.stop)

            long_line = '（祀推过一杯热茶）||' + '噗嗤，你今天怎么这么精神呀，慢慢跟我说便是。' * 7
            self.assertGreater(len(long_line), 160)
            engine.speak(long_line)
            self.assertTrue(self._pump_until(lambda: len(played) == 1))
            self.assertIsNotNone(played[0][0])

            # Now simulate Apex launching while audio is playing -> guard stops playback immediately!
            with mock.patch('si_pet.native._list_process_names', return_value={'explorer.exe', 'r5apex.exe'}):
                native.running_blocked_game(force_refresh=True)
                engine._poll_audio_game_guard()
            self.assertEqual(played[-1], (None, 0))


if __name__ == '__main__':
    unittest.main()
