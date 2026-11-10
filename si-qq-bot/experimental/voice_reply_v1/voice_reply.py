"""Isolated QQ voice preparation; no QQ connection, messages or disk cache.

The caller owns delivery, acknowledgement and durable conversation state.
"""
from dataclasses import dataclass, field
import base64
import http.client
import io
import struct
import urllib.parse
import wave

PROFILE_A = '06332768'
MAX_AUDIO_BYTES = 2_000_000
MAX_TEXT = 120
MAX_CHUNKS = 5


def reply_mode_command(text):
    """Recognize only exact opt-in commands; caller must authorize sender first."""
    if not isinstance(text, str):
        return None
    return {'/语音': 'voice', '/文字': 'text'}.get(text.strip())


class VoiceError(RuntimeError):
    pass


def validate_audio(payload):
    if not isinstance(payload, bytes) or not 44 <= len(payload) <= MAX_AUDIO_BYTES:
        raise VoiceError('语音数据大小不符合要求')
    try:
        with wave.open(io.BytesIO(payload), 'rb') as wav:
            channels, width, rate, frames = (wav.getnchannels(), wav.getsampwidth(),
                                             wav.getframerate(), wav.getnframes())
            if channels not in (1, 2) or width != 2 or not 8000 <= rate <= 96000:
                raise ValueError('unsupported PCM')
            if not rate // 10 <= frames <= 45 * rate:
                raise ValueError('duration')
            samples = wav.readframes(frames)
            if len(samples) != frames * channels * width or not any(samples):
                raise ValueError('truncated or silent')
    except (wave.Error, EOFError, ValueError, OverflowError, struct.error) as exc:
        raise VoiceError('语音数据不可播放') from exc


def synthesize_a(text, timeout=180):
    """Send only the response text to the existing loopback VoiceStudio."""
    if not isinstance(text, str) or not text.strip() or len(text.strip()) > MAX_TEXT:
        raise VoiceError('文字长度不适合语音')
    form = urllib.parse.urlencode({'text': text.strip(), 'profile_id': PROFILE_A,
                                   'language': 'Chinese', 'engine': 'audiocpp'}).encode('utf-8')
    connection = http.client.HTTPConnection('127.0.0.1', 3900, timeout=timeout)
    try:
        connection.request('POST', '/generate', body=form,
                           headers={'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8'})
        response = connection.getresponse()
        try:
            if response.status != 200:
                raise VoiceError('VoiceStudio 语音暂不可用')
            payload = response.read(MAX_AUDIO_BYTES + 1)
        finally:
            response.close()
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        raise VoiceError('VoiceStudio 未连接或生成超时') from exc
    finally:
        connection.close()
    validate_audio(payload)
    return payload


@dataclass(frozen=True)
class PreparedReply:
    text: str = field(repr=False)
    segments: tuple = field(repr=False)
    voice_ready: bool
    status: str


def split_text(text):
    """Split without losing characters, preferring sentence and clause ends."""
    remaining = text.strip()
    chunks = []
    while remaining:
        if len(remaining) <= MAX_TEXT:
            chunks.append(remaining)
            break
        window = remaining[:MAX_TEXT]
        cut = max((i + 1 for i, ch in enumerate(window)
                   if ch in '。！？!?；;\n'), default=0)
        if cut < MAX_TEXT // 3:
            cut = max((i + 1 for i, ch in enumerate(window)
                       if ch in '，,、：: '), default=0)
        if cut < MAX_TEXT // 3:
            cut = MAX_TEXT
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    return tuple(chunks)


def join_pcm(audio_parts):
    """Join compatible WAV PCM into one bounded clip without touching disk."""
    if not audio_parts:
        raise VoiceError('没有语音数据')
    params = None
    pcm_parts = []
    total_frames = 0
    total_bytes = 0
    for audio in audio_parts:
        validate_audio(audio)
        with wave.open(io.BytesIO(audio), 'rb') as reader:
            key = (reader.getnchannels(), reader.getsampwidth(), reader.getframerate(),
                   reader.getcomptype())
            if params is not None and key != params:
                raise VoiceError('语音分段格式不一致')
            params = key
            frames = reader.getnframes()
            total_frames += frames
            pcm = reader.readframes(frames)
            total_bytes += len(pcm)
            if total_frames > 45 * key[2] or total_bytes + 44 > MAX_AUDIO_BYTES:
                raise VoiceError('合成语音超过总时长或大小限制')
            pcm_parts.append(pcm)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as output:
        output.setnchannels(params[0])
        output.setsampwidth(params[1])
        output.setframerate(params[2])
        output.writeframes(b''.join(pcm_parts))
    combined = buf.getvalue()
    validate_audio(combined)
    return combined


def prepare_reply(text, *, enabled, synthesize=synthesize_a):
    """Prepare one joined record or one text segment, without delivery.

    Text remains available in the return value for UI/history and fallback.
    All clips are prepared and validated before returning the single record.
    The caller must never retry a send with an unknown outcome.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError('reply text required')
    fallback = {'type': 'text', 'data': {'text': text}}
    if not enabled:
        return PreparedReply(text, (fallback,), False, '语音未启用')
    chunks = split_text(text)
    if len(chunks) > MAX_CHUNKS:
        return PreparedReply(text, (fallback,), False, '回复过长，使用文字')
    try:
        audio_parts = []
        for chunk in chunks:
            audio = synthesize(chunk)
            validate_audio(audio)
            audio_parts.append(audio)
        audio = join_pcm(audio_parts)
        record = {'type': 'record', 'data': {
            'file': 'base64://' + base64.b64encode(audio).decode('ascii')}}
        return PreparedReply(text, (record,), True, '语音已生成')
    except (VoiceError, OSError, ValueError, TypeError):
        return PreparedReply(text, (fallback,), False, '语音生成失败，使用文字')
