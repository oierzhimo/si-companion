"""Local VoiceStudio speech recognition client; no QQ or file access."""
import http.client
import json
import secrets
from voice_reply import VoiceError, validate_audio


def transcribe_wav(audio, timeout=120, connection_factory=http.client.HTTPConnection):
    """Transcribe validated WAV on loopback. Caller must authorize the sender.

    No file path, QQ message, URL, or account identifier is accepted here.
    """
    validate_audio(audio)
    boundary = 'SiASR' + secrets.token_hex(16)
    head = (f'--{boundary}\r\nContent-Disposition: form-data; name="audio"; '
            'filename="speech.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode('ascii')
    tail = f'\r\n--{boundary}--\r\n'.encode('ascii')
    conn = connection_factory('127.0.0.1', 3900, timeout=timeout)
    try:
        conn.request('POST', '/transcribe', head + audio + tail,
                     {'Content-Type': f'multipart/form-data; boundary={boundary}'})
        response = conn.getresponse()
        try:
            if response.status != 200:
                raise VoiceError('语音识别不可用，请发送文字')
            raw = response.read(65537)
        finally:
            response.close()
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        raise VoiceError('语音识别连接失败，请发送文字') from exc
    finally:
        conn.close()
    if len(raw) > 65536:
        raise VoiceError('语音识别结果过大')
    try:
        obj = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise VoiceError('语音识别结果无效') from exc
    text = obj.get('text') if isinstance(obj, dict) else None
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise VoiceError('未能识别语音，请发送文字')
    return text.strip()
