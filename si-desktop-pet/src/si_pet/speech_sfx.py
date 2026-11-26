"""Speech & Foley sound renderer for Si's TTS replies.

Preserves spoken onomatopoeia (拟声词) in dialogue and converts Soul Engine
stage/action brackets ``（...）`` and emotion tags ``[表情:...]`` into:
  1. Vocalized interjections spoken by Si's cloned TTS voice (e.g. 咳咳 / 哼 / 噗嗤 / 唉),
  2. Crisp, pleasant 16-bit PCM procedural Foley action sounds (动作的声音:
     ink-brush strokes, tail/seal stamps, desk/stone taps, porcelain tea clinks,
     silk/tail rustles, and soft footsteps),
seamlessly stitched with multi-chunk TTS WAV output.
"""
from __future__ import annotations

from array import array
from dataclasses import dataclass
import io
import math
import random
import re
import struct
import sys
import wave

DEFAULT_SAMPLE_RATE = 24000
MAX_TTS_CHUNK_CHARS = 90
MAX_TOTAL_SECONDS = 44.0

# Stage brackets: full-width （...） always; ASCII (...) only when containing CJK stage cues.
_STAGE_TOKEN_RE = re.compile(
    r'（[^）]{1,160}）'
    r'|\((?=[^)]{1,160}[\u4e00-\u9fff])[^)]{1,160}\)'
    r'|\[表情:[^\]]{1,24}\]'
    r'|\[语音\]'
    r'|\|\|'
)

_ASCII_STAGE_HINTS = (
    '祀', '轻', '微', '执笔', '提笔', '落笔', '落墨', '宣纸', '毛笔', '研墨',
    '尾巴', '耳尖', '耳朵', '眨', '歪头', '低头', '抬头', '转身', '走近', '脚步',
    '敲', '叩', '拍', '盖戳', '印章', '茶', '杯', '叹', '咳', '哼', '笑', '噗嗤',
    '递', '推', '翻', '卷轴', '衣袖', '斗篷', '落子', '棋', '坐下', '蹲',
)

# Vocalized actions inside （...） -> (spoken interjection, root chars to deduplicate)
_BRACKET_VOCAL_RULES: tuple[tuple[tuple[str, ...], str, tuple[str, ...]], ...] = (
    (('干咳', '咳嗽', '轻咳', '清了清嗓', '清嗓'), '咳咳，', ('咳',)),
    (('噗嗤', '忍俊不禁', '偷笑', '轻笑', '笑出声', '笑弯'), '噗嗤，', ('噗', '哈', '嘿')),
    (('轻哼', '冷哼', '哼了一声', '鼻音轻哼', '撇了撇嘴', '撇嘴'), '哼，', ('哼',)),
    (('叹了口气', '叹气', '轻叹', '长叹', '叹息'), '唉……', ('唉', '哎')),
    (('哈欠', '打了个盹', '困倦'), '哈啊……', ('哈啊', '唔')),
    (('倒吸一口', '惊呼', '吓了一跳', '一惊'), '呀！', ('呀', '欸', '诶', '啊')),
    (('啧了', '咂嘴'), '啧，', ('啧',)),
    (('小声嘟囔', '嘟囔', '小声嘀咕', '嘀咕', '抿了抿唇'), '唔……', ('唔', '嗯')),
)

# Physical actions inside （...） -> procedural Foley cue id
_BRACKET_SFX_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (('执笔', '提笔', '落笔', '落墨', '画了', '写下', '描摹', '批注', '宣纸', '毛笔', '研墨', '墨痕'), 'brush'),
    (('盖戳', '盖了个', '印章', '啪嗒', '尾巴一拍', '拍了拍桌', '拍案', '重重搁'), 'stamp'),
    (('茶盏', '热茶', '倒茶', '推过', '推了推', '茶杯', '端起茶', '抿了一口', '杯盏', '添茶', '茶水'), 'tea'),
    (('敲了敲', '叩了叩', '轻敲', '指尖点', '指尖敲', '落子', '棋子', '搁在桌'), 'tap'),
    (('走近', '走到', '脚步', '退开', '退后', '坐下', '蹲下', '起身', '站起'), 'step'),
    (('尾巴', '衣袖', '斗篷', '翻开', '翻书', '书页', '卷轴', '布偶', '耳朵', '发丝', '晃了晃', '甩了甩'), 'rustle'),
)

# Inline spoken sound words that get a subtle matching acoustic layer
_INLINE_SFX_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (('啪嗒', '啪！', '啪，', '啪地'), 'stamp'),
    (('沙沙', '唰唰'), 'brush'),
    (('叮——', '叮～', '叮！', '叮当', '哗啦'), 'tea'),
    (('笃笃', '咚咚', '叩叩'), 'tap'),
)

_EMOTION_CUES: dict[str, tuple[str | None, str | None]] = {
    '开心': (None, 'chime_happy'),
    '得意': (None, 'chime_happy'),
    '生气': ('哼，', 'rustle_soft'),
    '委屈': ('唔……', 'rustle_soft'),
    '困了': ('哈啊……', 'rustle_soft'),
    '害羞': (None, 'rustle_soft'),
}


@dataclass(frozen=True)
class AudioSegment:
    """One ordered piece of a reply's audio timeline."""
    kind: str   # 'tts' | 'sfx'
    value: str  # spoken text for 'tts', or cue name for 'sfx'


def clean_bubble_text(text: str) -> str:
    """Format a reply cleanly for compact desktop speech bubbles while keeping dialogue & onomatopoeia."""
    if not isinstance(text, str):
        return ''
    cleaned = re.sub(r'\[表情:[^\]]*\]|\[语音\]', '', text)
    cleaned = cleaned.replace('||', ' ')
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def _is_stage_paren(token: str) -> bool:
    if token.startswith('（') and token.endswith('）'):
        return True
    if token.startswith('(') and token.endswith(')'):
        inner = token[1:-1]
        return any(hint in inner for hint in _ASCII_STAGE_HINTS)
    return False


def _normalize_tts_span(text: str) -> str:
    """Clean markdown artifacts while keeping all spoken words and onomatopoeia intact."""
    s = re.sub(r'\[(?:语音|图片|表情:[^\]]*)\]', '', text)
    s = s.replace('**', '').replace('__', '').replace('`', '')
    s = re.sub(r'[ \t\u3000]+', ' ', s).strip()
    if not s:
        return ''
    # Pure punctuation has nothing speakable
    if not any(ch.isalnum() or '\u4e00' <= ch <= '\u9fff' for ch in s):
        return ''
    # Ensure ultra-short 1-2 char interjections (e.g. "哼", "欸") carry terminal punctuation
    # so neural TTS models produce a clean, complete phoneme contour.
    if len(s) <= 2 and s[-1] not in '。！？!?…～~，,':
        s += '。'
    return s


def split_tts_chunks(text: str, max_chars: int = MAX_TTS_CHUNK_CHARS) -> list[str]:
    """Split spoken dialogue at natural sentence/clause boundaries without dropping characters."""
    remaining = _normalize_tts_span(text)
    if not remaining:
        return []
    limit = max(20, int(max_chars))
    chunks: list[str] = []
    while remaining:
        if len(remaining) <= limit:
            norm = _normalize_tts_span(remaining)
            if norm:
                chunks.append(norm)
            break
        window = remaining[:limit]
        cut = max((i + 1 for i, ch in enumerate(window) if ch in '。！？!?；;\n'), default=0)
        if cut < limit // 3:
            cut = max((i + 1 for i, ch in enumerate(window) if ch in '，,、：:…～~ '), default=0)
        if cut < limit // 4:
            cut = limit
        piece = _normalize_tts_span(remaining[:cut])
        if piece:
            chunks.append(piece)
        remaining = remaining[cut:].lstrip()
    return chunks


def _parse_stage_bracket(inner: str) -> tuple[tuple[str, tuple[str, ...]] | None, list[str]]:
    """Extract (optional vocal cue, list of Foley sfx cues) from a stage bracket."""
    vocal: tuple[str, tuple[str, ...]] | None = None
    for keywords, spoken, roots in _BRACKET_VOCAL_RULES:
        if any(kw in inner for kw in keywords):
            vocal = (spoken, roots)
            break

    sfx_cues: list[str] = []
    for keywords, cue in _BRACKET_SFX_RULES:
        if any(kw in inner for kw in keywords) and cue not in sfx_cues:
            sfx_cues.append(cue)
        if len(sfx_cues) >= 2:
            break

    # Every stage/action bracket gets at least a subtle movement Foley cue unless
    # it already has either a specific physical cue or a vocal interjection.
    if not sfx_cues and inner.strip():
        sfx_cues.append('rustle_soft')
    return vocal, sfx_cues


def build_speech_plan(text: str, max_chunk_chars: int = MAX_TTS_CHUNK_CHARS) -> list[AudioSegment]:
    """Compile a raw reply (with stage brackets, onomatopoeia, tags, and ``||``) into audio segments."""
    if not isinstance(text, str) or not text.strip():
        return []

    raw_tokens: list[tuple[str, str]] = []
    pos = 0
    for match in _STAGE_TOKEN_RE.finditer(text):
        if match.start() > pos:
            raw_tokens.append(('text', text[pos:match.start()]))
        tok = match.group(0)
        if tok == '||':
            raw_tokens.append(('pause', '||'))
        elif tok == '[语音]':
            continue
        elif tok.startswith('[表情:'):
            raw_tokens.append(('emotion', tok[4:-1].strip()))
        elif _is_stage_paren(tok):
            raw_tokens.append(('stage', tok[1:-1].strip()))
        else:
            raw_tokens.append(('text', tok))
        pos = match.end()
    if pos < len(text):
        raw_tokens.append(('text', text[pos:]))

    # Helper to peek at the next spoken character so vocalized bracket cues don't duplicate
    def _next_spoken_head(start_idx: int) -> str:
        for kind, val in raw_tokens[start_idx:]:
            if kind == 'text':
                stripped = _normalize_tts_span(val)
                if stripped:
                    return stripped[:4]
        return ''

    def _prev_has_tts(plan: list[AudioSegment]) -> bool:
        return any(seg.kind == 'tts' for seg in plan)

    plan: list[AudioSegment] = []
    pending_vocal: str = ''

    for idx, (kind, val) in enumerate(raw_tokens):
        if kind == 'pause':
            if pending_vocal:
                for chunk in split_tts_chunks(pending_vocal, max_chunk_chars):
                    plan.append(AudioSegment('tts', chunk))
                pending_vocal = ''
            if plan and plan[-1]. value != 'pause':
                plan.append(AudioSegment('sfx', 'pause'))
        elif kind == 'stage':
            vocal_info, sfx_list = _parse_stage_bracket(val)
            for cue in sfx_list:
                plan.append(AudioSegment('sfx', cue))
            if vocal_info is not None:
                spoken, roots = vocal_info
                head = _next_spoken_head(idx + 1)
                if not any(head.startswith(r) for r in roots):
                    pending_vocal = (pending_vocal + spoken) if pending_vocal else spoken
        elif kind == 'emotion':
            emo_vocal, emo_sfx = _EMOTION_CUES.get(val, (None, None))
            if emo_vocal and not _prev_has_tts(plan) and not _next_spoken_head(idx + 1):
                pending_vocal = (pending_vocal + emo_vocal) if pending_vocal else emo_vocal
            if emo_sfx and (not plan or plan[-1].kind != 'sfx'):
                plan.append(AudioSegment('sfx', emo_sfx))
        elif kind == 'text':
            combined = (pending_vocal + val.lstrip()) if pending_vocal else val
            pending_vocal = ''
            chunks = split_tts_chunks(combined, max_chunk_chars)
            for chunk in chunks:
                # Check if spoken clause has an explicit physical sound word for subtle Foley layering
                matched_inline = None
                for keywords, cue in _INLINE_SFX_RULES:
                    if any(kw in chunk for kw in keywords):
                        matched_inline = cue
                        break
                if matched_inline and (not plan or plan[-1] != AudioSegment('sfx', matched_inline)):
                    plan.append(AudioSegment('sfx', matched_inline))
                plan.append(AudioSegment('tts', chunk))

    if pending_vocal:
        for chunk in split_tts_chunks(pending_vocal, max_chunk_chars):
            plan.append(AudioSegment('tts', chunk))

    # Fallback if input had speakable text that somehow yielded empty plan
    if not plan:
        fallback = _normalize_tts_span(text)
        if fallback:
            for chunk in split_tts_chunks(fallback, max_chunk_chars):
                plan.append(AudioSegment('tts', chunk))

    return plan


# ---------------------------------------------------------------------------
# Procedural 16-bit PCM Foley synthesizer (pure stdlib, deterministic, fast)
# ---------------------------------------------------------------------------

def _apply_micro_fade(samples: list[float], sample_rate: int, fade_ms: float = 4.5) -> None:
    fade_n = min(len(samples) // 2, max(1, int(sample_rate * fade_ms / 1000.0)))
    for i in range(fade_n):
        w = 0.5 * (1.0 - math.cos(math.pi * (i + 1) / fade_n))
        samples[i] *= w
        samples[-1 - i] *= w


def _floats_to_pcm16(samples: list[float], channels: int = 1) -> bytes:
    pcm = array('h')
    for val in samples:
        clamped = max(-0.92, min(0.92, val))
        s16 = int(round(clamped * 32767.0))
        pcm.append(s16)
        if channels == 2:
            pcm.append(s16)
    if sys.byteorder != 'little':
        pcm.byteswap()
    return pcm.tobytes()


def render_sfx_pcm(cue: str, sample_rate: int = DEFAULT_SAMPLE_RATE, channels: int = 1) -> bytes:
    """Synthesize a clean, pleasant 16-bit PCM Foley waveform for *cue*."""
    sr = max(8000, min(96000, int(sample_rate)))
    ch = 2 if channels == 2 else 1
    rng = random.Random(f'si-foley-v1:{cue}')

    if cue == 'pause':
        n = max(1, int(sr * 0.16))
        # Near-inaudible 1-LSB dither so strict non-zero PCM validators stay happy if ever isolated
        samples = [((i % 2) * 2 - 1) * 0.00015 for i in range(n)]
        return _floats_to_pcm16(samples, ch)

    if cue == 'brush':
        # Two gentle ink-brush strokes on xuan paper ("沙—沙")
        dur = 0.34
        n = int(sr * dur)
        samples = [0.0] * n
        lp = 0.0
        hp_prev = 0.0
        raw_prev = 0.0
        for i in range(n):
            t = i / sr
            # Two overlapping organic brush envelopes
            e1 = math.sin(math.pi * min(1.0, max(0.0, t / 0.17))) ** 1.6 if t < 0.17 else 0.0
            e2 = 0.82 * (math.sin(math.pi * min(1.0, max(0.0, (t - 0.15) / 0.18))) ** 1.7) if t >= 0.15 else 0.0
            env = e1 + e2
            white = rng.uniform(-1.0, 1.0)
            # Band-pass textured paper friction (~900 Hz - 3800 Hz)
            hp = white - raw_prev + 0.86 * hp_prev
            raw_prev = white
            hp_prev = hp
            alpha = min(0.65, 2.0 * math.pi * 3200.0 / sr)
            lp = lp + alpha * (hp - lp)
            samples[i] = lp * env * 0.20
        _apply_micro_fade(samples, sr)
        return _floats_to_pcm16(samples, ch)

    if cue == 'stamp':
        # Warm wooden seal stamp / cute tail thump ("啪嗒")
        dur = 0.25
        n = int(sr * dur)
        samples = [0.0] * n
        lp = 0.0
        for i in range(n):
            t = i / sr
            # Primary stamp at t=0.01s, light settle bounce at t=0.11s
            s = 0.0
            if t >= 0.008:
                dt = t - 0.008
                freq = 95.0 + 130.0 * math.exp(-dt * 55.0)
                body = math.sin(2.0 * math.pi * freq * dt) * math.exp(-dt * 28.0)
                wood = math.sin(2.0 * math.pi * 820.0 * dt) * math.exp(-dt * 95.0)
                snap = rng.uniform(-1.0, 1.0) * math.exp(-dt * 140.0)
                s += 0.34 * body + 0.14 * wood + 0.16 * snap
            if t >= 0.105:
                dt2 = t - 0.105
                click = (
                    0.16 * math.sin(2.0 * math.pi * 620.0 * dt2) * math.exp(-dt2 * 85.0)
                    + 0.09 * rng.uniform(-1.0, 1.0) * math.exp(-dt2 * 160.0)
                )
                s += click
            lp = lp + 0.55 * (s - lp)
            samples[i] = lp
        _apply_micro_fade(samples, sr)
        return _floats_to_pcm16(samples, ch)

    if cue == 'tap':
        # Crisp fingertip / go-stone double tap on wooden desk ("笃、笃")
        dur = 0.26
        n = int(sr * dur)
        samples = [0.0] * n
        for i in range(n):
            t = i / sr
            s = 0.0
            for onset, gain, pitch in ((0.012, 1.0, 1.0), (0.125, 0.76, 1.08)):
                if t >= onset:
                    dt = t - onset
                    m1 = math.sin(2.0 * math.pi * 540.0 * pitch * dt) * math.exp(-dt * 62.0)
                    m2 = math.sin(2.0 * math.pi * 1280.0 * pitch * dt) * math.exp(-dt * 110.0)
                    m3 = rng.uniform(-1.0, 1.0) * math.exp(-dt * 190.0)
                    s += gain * (0.24 * m1 + 0.13 * m2 + 0.08 * m3)
            samples[i] = s
        _apply_micro_fade(samples, sr)
        return _floats_to_pcm16(samples, ch)

    if cue == 'tea':
        # Delicate porcelain teacup clink + gentle warm pour ("叮～")
        dur = 0.34
        n = int(sr * dur)
        samples = [0.0] * n
        lp = 0.0
        for i in range(n):
            t = i / sr
            s = 0.0
            if t >= 0.010:
                dt = t - 0.010
                # Ceramic modal partials (inharmonic porcelain ring)
                ring = (
                    0.18 * math.sin(2.0 * math.pi * 2080.0 * dt) * math.exp(-dt * 18.0)
                    + 0.11 * math.sin(2.0 * math.pi * 3410.0 * dt) * math.exp(-dt * 26.0)
                    + 0.06 * math.sin(2.0 * math.pi * 5120.0 * dt) * math.exp(-dt * 42.0)
                )
                s += ring
            if 0.07 <= t <= 0.31:
                # Subtle warm tea ripple
                env = math.sin(math.pi * (t - 0.07) / 0.24) ** 1.5
                ripple_mod = 0.55 + 0.45 * math.sin(2.0 * math.pi * 19.0 * t)
                lp = lp + 0.28 * (rng.uniform(-1.0, 1.0) - lp)
                s += lp * env * ripple_mod * 0.075
            samples[i] = s
        _apply_micro_fade(samples, sr)
        return _floats_to_pcm16(samples, ch)

    if cue == 'step':
        # Two soft indoor footsteps / posture settle
        dur = 0.30
        n = int(sr * dur)
        samples = [0.0] * n
        lp = 0.0
        for i in range(n):
            t = i / sr
            s = 0.0
            for onset, gain in ((0.015, 0.95), (0.155, 0.78)):
                if t >= onset:
                    dt = t - onset
                    thud = math.sin(2.0 * math.pi * (115.0 * math.exp(-dt * 20.0) + 60.0) * dt) * math.exp(-dt * 34.0)
                    scuff = rng.uniform(-1.0, 1.0) * math.exp(-dt * 75.0)
                    s += gain * (0.24 * thud + 0.07 * scuff)
            lp = lp + 0.35 * (s - lp)
            samples[i] = lp
        _apply_micro_fade(samples, sr)
        return _floats_to_pcm16(samples, ch)

    if cue == 'chime_happy':
        # Subtle jade/hairpin ornament chime
        dur = 0.22
        n = int(sr * dur)
        samples = [0.0] * n
        for i in range(n):
            t = i / sr
            s = 0.0
            for onset, freq, gain in ((0.008, 1568.0, 0.14), (0.068, 2093.0, 0.12)):
                if t >= onset:
                    dt = t - onset
                    s += gain * math.sin(2.0 * math.pi * freq * dt) * math.exp(-dt * 22.0)
            samples[i] = s
        _apply_micro_fade(samples, sr)
        return _floats_to_pcm16(samples, ch)

    # Default: 'rustle' or 'rustle_soft' — gentle silk sleeve / paper / tail swish
    soft = (cue == 'rustle_soft')
    dur = 0.18 if soft else 0.24
    gain = 0.09 if soft else 0.14
    n = int(sr * dur)
    samples = [0.0] * n
    lp = 0.0
    prev = 0.0
    for i in range(n):
        t = i / sr
        env = math.sin(math.pi * (t / dur)) ** 1.8
        white = rng.uniform(-1.0, 1.0)
        diff = white - 0.78 * prev
        prev = white
        lp = lp + 0.32 * (diff - lp)
        samples[i] = lp * env * gain
    _apply_micro_fade(samples, sr)
    return _floats_to_pcm16(samples, ch)


def render_sfx_wav(cue: str, sample_rate: int = DEFAULT_SAMPLE_RATE, channels: int = 1) -> bytes:
    """Return a complete 16-bit PCM WAV byte buffer for a single Foley *cue*."""
    pcm = render_sfx_pcm(cue, sample_rate=sample_rate, channels=channels)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as writer:
        writer.setnchannels(2 if channels == 2 else 1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(pcm)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# WAV decoding, resampling & seamless multi-segment assembly
# ---------------------------------------------------------------------------

def _read_wav_pcm16(payload: bytes) -> tuple[int, int, bytes]:
    """Return ``(channels, sample_rate, raw_pcm16_le_bytes)`` from 16-bit WAV bytes."""
    if not isinstance(payload, bytes) or len(payload) < 44:
        raise ValueError('WAV payload too small')
    with wave.open(io.BytesIO(payload), 'rb') as reader:
        channels = reader.getnchannels()
        width = reader.getsampwidth()
        rate = reader.getframerate()
        frames = reader.getnframes()
        if channels not in (1, 2) or width != 2 or not 8000 <= rate <= 96000 or frames <= 0:
            raise ValueError('Unsupported WAV PCM parameters')
        raw = reader.readframes(frames)
        if len(raw) != frames * channels * 2:
            raise ValueError('Truncated WAV frame data')
    return channels, rate, raw


def _convert_pcm16(raw: bytes, src_ch: int, src_rate: int, dst_ch: int, dst_rate: int) -> bytes:
    """Convert mono/stereo & sample-rate via linear interpolation when needed."""
    if src_ch == dst_ch and src_rate == dst_rate:
        return raw
    samples = array('h')
    samples.frombytes(raw)
    if sys.byteorder != 'little':
        samples.byteswap()

    # Mix to mono float/int list first
    if src_ch == 2:
        mono = [(samples[i] + samples[i + 1]) // 2 for i in range(0, len(samples), 2)]
    else:
        mono = list(samples)

    if src_rate != dst_rate and mono:
        dst_len = max(1, int(round(len(mono) * dst_rate / src_rate)))
        resampled = []
        scale = (len(mono) - 1) / max(1, dst_len - 1) if dst_len > 1 else 0.0
        last_idx = len(mono) - 1
        for i in range(dst_len):
            pos = i * scale
            lo = min(last_idx, int(pos))
            hi = min(last_idx, lo + 1)
            frac = pos - lo
            val = int(round(mono[lo] * (1.0 - frac) + mono[hi] * frac))
            resampled.append(max(-32768, min(32767, val)))
        mono = resampled

    out = array('h')
    if dst_ch == 2:
        for val in mono:
            out.append(val)
            out.append(val)
    else:
        out.extend(mono)
    if sys.byteorder != 'little':
        out.byteswap()
    return out.tobytes()


def assemble_speech_and_sfx(
    text: str,
    synthesize_chunk,
    *,
    default_sample_rate: int = DEFAULT_SAMPLE_RATE,
    max_total_bytes: int = 10_000_000,
) -> bytes:
    """Render *text* into one 16-bit PCM WAV combining TTS speech, onomatopoeia, and Foley SFX.

    Args:
        text: Raw assistant reply or companion phrase.
        synthesize_chunk: Callable ``(chunk_text: str) -> bytes`` returning 16-bit PCM WAV bytes.
        default_sample_rate: Sample rate used when the reply contains only physical action SFX.
        max_total_bytes: Maximum output WAV byte size cap.
    """
    plan = build_speech_plan(text)
    if not plan:
        raise ValueError('无可发声的文本或动作内容')

    # Synthesize all TTS segments first so Foley SFX matches the engine's exact sample rate.
    tts_cache: dict[str, tuple[int, int, bytes]] = {}
    target_channels = 1
    target_rate = default_sample_rate

    for seg in plan:
        if seg.kind == 'tts' and seg.value not in tts_cache:
            wav_bytes = synthesize_chunk(seg.value)
            ch, sr, pcm = _read_wav_pcm16(wav_bytes)
            if not tts_cache:
                target_channels = ch
                target_rate = sr
            tts_cache[seg.value] = (ch, sr, pcm)

    pcm_parts: list[bytes] = []
    frame_bytes = target_channels * 2
    max_frames = int(MAX_TOTAL_SECONDS * target_rate)
    max_pcm_bytes = max(4096, int(max_total_bytes) - 44)
    total_frames = 0
    total_bytes = 0

    for idx, seg in enumerate(plan):
        if seg.kind == 'tts':
            ch, sr, raw_pcm = tts_cache[seg.value]
            part = _convert_pcm16(raw_pcm, ch, sr, target_channels, target_rate)
        else:
            part = render_sfx_pcm(seg.value, sample_rate=target_rate, channels=target_channels)

        part_frames = len(part) // frame_bytes
        if total_frames + part_frames > max_frames or total_bytes + len(part) > max_pcm_bytes:
            # Always keep at least the first segment; stop cleanly if later segments exceed cap
            if pcm_parts:
                break
            allowed_frames = min(max_frames, max_pcm_bytes // frame_bytes)
            part = part[:allowed_frames * frame_bytes]
            part_frames = len(part) // frame_bytes

        pcm_parts.append(part)
        total_frames += part_frames
        total_bytes += len(part)

        # Add a tiny 35ms natural breath gap between consecutive TTS chunks
        if (
            seg.kind == 'tts'
            and idx + 1 < len(plan)
            and plan[idx + 1].kind == 'tts'
        ):
            gap_frames = int(target_rate * 0.035)
            gap_bytes = b'\x00' * (gap_frames * frame_bytes)
            if total_frames + gap_frames <= max_frames and total_bytes + len(gap_bytes) <= max_pcm_bytes:
                pcm_parts.append(gap_bytes)
                total_frames += gap_frames
                total_bytes += len(gap_bytes)

    # Guarantee minimum 0.12s duration so strict WAV validators (>= rate // 10) always pass
    min_frames = (target_rate // 10) + int(target_rate * 0.02)
    if total_frames < min_frames:
        pad_frames = min_frames - total_frames
        pcm_parts.append(b'\x00' * (pad_frames * frame_bytes))
        total_frames += pad_frames

    out = io.BytesIO()
    with wave.open(out, 'wb') as writer:
        writer.setnchannels(target_channels)
        writer.setsampwidth(2)
        writer.setframerate(target_rate)
        writer.writeframes(b''.join(pcm_parts))
    return out.getvalue()
