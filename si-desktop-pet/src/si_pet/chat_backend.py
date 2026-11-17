"""Generic multimodal chat backend for the desktop pet.

This module is intentionally independent of QQ, OneBot, NapCat, and asyncio.
It provides image preparation, voice synthesis, and session management that
can be called directly from any synchronous desktop UI code.

Public surface:
  ChatError                       — exception type for all backend failures
  prepare_image_attachment(bytes) — resize/encode image → data URL
  attach_images(messages, urls)   — inject image parts into last user message
  check_voice_service(timeout)    — probe VoiceStudio → status string
  synthesize_voice(text, ...)     — call VoiceStudio → WAV bytes
  ChatSession                     — persistent multi-turn session
"""

import base64
import http.client
import io
import json
import os
import urllib.parse
import warnings
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from .conversation import (  # noqa: F401 – re-exported for callers
    local_endpoint,
    ConversationError,
    PERSONA,
    request_reply,
    _auth_headers,
    _opener,
    _http_status_message,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VOICE_HOST: str = '127.0.0.1'
VOICE_PORT: int = 3900
VOICE_PROFILE_DEFAULT: str = '06332768'

MAX_IMAGE_BYTES: int = 10 * 1024 * 1024   # 10 MiB — input gate
MAX_IMAGE_SIDE: int = 1024                 # longest edge after thumbnail
MAX_IMAGE_OUTPUT_BYTES: int = 4 * 1024 * 1024  # 4 MiB — JPEG output gate

_VOICE_MAX_AUDIO_BYTES: int = 2_000_000
_SUPPORTED_FORMATS = {'JPEG', 'PNG', 'GIF', 'WEBP'}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ChatError(Exception):
    """Raised by any public function in this module when an operation fails.

    Callers should catch ``ChatError`` for expected failure paths (bad image
    bytes, voice service unavailable, etc.) and let other exceptions propagate
    as bugs.
    """


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def prepare_image_attachment(image_bytes: bytes) -> str:
    """Resize, strip EXIF, flatten transparency, and encode *image_bytes* as a JPEG data URL.

    The image is scaled so that neither side exceeds ``MAX_IMAGE_SIDE`` pixels.
    Transparent pixels are composited on a white background.  EXIF metadata and
    embedded thumbnails are removed by re-encoding.

    Args:
        image_bytes: Raw bytes of a JPEG, PNG, GIF, or WebP image.  Must not
            exceed ``MAX_IMAGE_BYTES``.

    Returns:
        A ``data:image/jpeg;base64,…`` string suitable for use as an
        ``image_url`` part in an OpenAI-compatible multipart message.

    Raises:
        ChatError: If the bytes are empty, too large, in an unsupported format,
            geometrically invalid, or if Pillow cannot decode them.
    """
    if not isinstance(image_bytes, bytes) or not 0 < len(image_bytes) <= MAX_IMAGE_BYTES:
        raise ChatError('图片为空或超出大小限制')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(image_bytes)) as im:
                if im.format not in _SUPPORTED_FORMATS or im.width <= 0 or im.height <= 0:
                    raise ChatError('图片格式或尺寸不支持')
                # For animated images, only process the first frame.
                try:
                    im.seek(0)
                except EOFError:
                    pass
                im = ImageOps.exif_transpose(im)
                im.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
                if 'A' in im.getbands() or 'transparency' in im.info:
                    rgba = im.convert('RGBA')
                    background = Image.new('RGB', rgba.size, 'white')
                    background.paste(rgba, mask=rgba.getchannel('A'))
                    im = background
                else:
                    im = im.convert('RGB')
                output = io.BytesIO()
                im.save(output, format='JPEG', quality=85, optimize=True)
                data = output.getvalue()
        if len(data) > MAX_IMAGE_OUTPUT_BYTES:
            raise ChatError('处理后的图片过大')
        return 'data:image/jpeg;base64,' + base64.b64encode(data).decode('ascii')
    except ChatError:
        raise
    except (UnidentifiedImageError, OSError, ValueError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ChatError('图片无法读取') from None


def attach_images(messages: list, image_data_urls: list) -> list:
    """Return a copy of *messages* with images appended to the last user turn.

    The original list and its dicts are never mutated.  The last message must
    have ``role == 'user'`` and a ``str`` content; it is replaced with a
    multipart list that starts with the original text and is followed by one
    ``image_url`` part per URL.

    Args:
        messages: A non-empty list of ``{'role': ..., 'content': ...}`` dicts.
        image_data_urls: One or more ``data:image/jpeg;base64,…`` strings as
            returned by :func:`prepare_image_attachment`.  Passing an empty
            sequence returns a shallow copy of *messages* unchanged.

    Returns:
        A new list; the caller may modify it without affecting *messages*.

    Raises:
        ChatError: If *messages* is structurally invalid or the last message is
            not a plain-text user message.
    """
    if not isinstance(messages, list) or not messages:
        raise ChatError('messages 列表无效')
    if not image_data_urls:
        return [dict(m) for m in messages]
    last = messages[-1]
    if not isinstance(last, dict) or last.get('role') != 'user' or not isinstance(last.get('content'), str):
        raise ChatError('最后一条消息必须是用户文字消息')
    for url in image_data_urls:
        if not isinstance(url, str) or not url.startswith('data:image/jpeg;base64,'):
            raise ChatError('image_data_url 格式无效')
    copied = [dict(m) for m in messages]
    text = last['content'] or '看看这张图。'
    parts = [{'type': 'text', 'text': text}]
    for url in image_data_urls:
        parts.append({'type': 'image_url', 'image_url': {'url': url}})
    copied[-1] = dict(last)
    copied[-1]['content'] = parts
    return copied


# ---------------------------------------------------------------------------
# Voice helpers
# ---------------------------------------------------------------------------

def _get_profiles(connection: http.client.HTTPConnection) -> list:
    """Internal: GET /profiles and return the parsed JSON list."""
    connection.request('GET', '/profiles')
    response = connection.getresponse()
    try:
        if response.status != 200:
            raise ChatError('VoiceStudio 声线列表不可用')
        raw = response.read(1_000_001)
    finally:
        response.close()
    if len(raw) > 1_000_000:
        raise ChatError('VoiceStudio 声线列表过大')
    try:
        profiles = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise ChatError('VoiceStudio 声线列表无法解析') from exc
    if not isinstance(profiles, list):
        raise ChatError('VoiceStudio 声线列表格式无效')
    return profiles


def check_voice_service(
    timeout: float = 2.0,
    profile_id: str = VOICE_PROFILE_DEFAULT,
) -> str:
    """Probe the local VoiceStudio and return a status string.

    Performs a single ``GET /profiles`` against ``127.0.0.1:3900``.  No
    text, reply, or private data is ever sent.

    Args:
        timeout: Socket timeout in seconds.
        profile_id: VoiceStudio profile ID to check for.

    Returns:
        ``'available'``       — service up and *profile_id* found.
        ``'profile_missing'`` — service up but *profile_id* absent.
        ``'unavailable'``     — connection failed or response unreadable.
    """
    target_profile = (profile_id or VOICE_PROFILE_DEFAULT).strip() or VOICE_PROFILE_DEFAULT
    try:
        connection = http.client.HTTPConnection(VOICE_HOST, VOICE_PORT, timeout=timeout)
        try:
            connection.request('GET', '/profiles')
            response = connection.getresponse()
            try:
                if response.status != 200:
                    return 'unavailable'
                raw = response.read(1_000_001)
            finally:
                response.close()
        finally:
            connection.close()
    except (OSError, TimeoutError, http.client.HTTPException):
        return 'unavailable'
    if len(raw) > 1_000_000:
        return 'unavailable'
    try:
        profiles = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeError):
        return 'unavailable'
    if not isinstance(profiles, list):
        return 'unavailable'
    if not any(
            isinstance(p, dict) and str(p.get('id')) == target_profile
            for p in profiles):
        return 'profile_missing'
    return 'available'


def synthesize_voice(
    text: str,
    profile_id: str = VOICE_PROFILE_DEFAULT,
    timeout: int = 180,
) -> bytes:
    """Synthesize *text* with VoiceStudio and return raw WAV bytes.

    The profile is verified to exist on the server before the synthesis
    request is sent, so the server can never silently fall back to a
    different voice.

    Args:
        text: The text to speak.  Must be non-empty after stripping.
        profile_id: VoiceStudio profile ID.  Defaults to
            ``VOICE_PROFILE_DEFAULT``.
        timeout: HTTP socket timeout in seconds.

    Returns:
        Valid 16-bit PCM WAV bytes.

    Raises:
        ChatError: If the service is unreachable, the profile is missing, or
            the response is not valid audio.
    """
    if not isinstance(text, str) or not text.strip():
        raise ChatError('合成文字不能为空')
    text = text.strip()
    if not isinstance(profile_id, str) or not profile_id.strip():
        raise ChatError('声线 ID 不能为空')
    profile_id = profile_id.strip()

    form = urllib.parse.urlencode({
        'text': text,
        'profile_id': profile_id,
        'language': 'Chinese',
        'engine': 'audiocpp',
    }).encode('utf-8')

    connection = http.client.HTTPConnection(VOICE_HOST, VOICE_PORT, timeout=timeout)
    try:
        # Verify profile before sending any synthesis request.
        profiles = _get_profiles(connection)
        if not any(isinstance(p, dict) and str(p.get('id')) == profile_id
                   for p in profiles):
            raise ChatError('所选声线不可用')

        connection.request(
            'POST', '/generate', body=form,
            headers={'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8'},
        )
        response = connection.getresponse()
        try:
            if response.status != 200:
                raise ChatError('VoiceStudio 语音合成失败')
            payload = response.read(_VOICE_MAX_AUDIO_BYTES + 1)
        finally:
            response.close()
    except ChatError:
        raise
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        raise ChatError('VoiceStudio 未连接或生成超时') from exc
    finally:
        connection.close()

    if len(payload) > _VOICE_MAX_AUDIO_BYTES:
        raise ChatError('VoiceStudio 返回的音频过大')
    _validate_wav(payload)
    return payload


def _validate_wav(payload: bytes) -> None:
    """Raise ChatError if *payload* is not a playable 16-bit PCM WAV."""
    import io as _io
    import struct
    import wave
    if not isinstance(payload, bytes) or len(payload) < 44:
        raise ChatError('语音数据过小或格式无效')
    try:
        with wave.open(_io.BytesIO(payload), 'rb') as wav:
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            rate = wav.getframerate()
            frames = wav.getnframes()
            if channels not in (1, 2) or width != 2 or not 8000 <= rate <= 96000:
                raise ValueError('unsupported PCM')
            if not rate // 10 <= frames <= 45 * rate:
                raise ValueError('duration out of range')
            samples = wav.readframes(frames)
            if len(samples) != frames * channels * width or not any(samples):
                raise ValueError('truncated or silent')
    except (wave.Error, EOFError, ValueError, struct.error) as exc:
        raise ChatError('语音数据不可播放') from exc


# ---------------------------------------------------------------------------
# ChatSession
# ---------------------------------------------------------------------------

_SESSION_LIMIT = 2 * 1024 * 1024  # 2 MiB — max JSON file size
_MAX_TURNS = 12


class ChatSession:
    """Persistent multi-turn chat session backed by a JSON file.

    The on-disk format is intentionally compatible with
    :class:`~si_pet.conversation.Conversation` so that files are
    interchangeable.  Unlike ``Conversation`` this class does **not** validate
    or enforce provider/endpoint constraints; those belong to the UI layer.

    Attributes:
        path (Path): Absolute path of the backing JSON file.
        turns (list[dict]): Conversation history as ``{'user': …, 'assistant': …}`` dicts.
        profile (str): Optional free-text user profile hint sent to the model.
        endpoint (str): OpenAI-compatible base URL (e.g. ``http://127.0.0.1:1234/v1``).
        model (str): Model identifier string.
        provider (str): Provider tag, e.g. ``'local'`` or ``'genai'``.
    """

    def __init__(self, path) -> None:
        """Load an existing session from *path*, or start with defaults.

        If *path* does not exist the session starts empty with sensible
        defaults and no error is raised.  If the file exists but is unreadable
        or malformed, :class:`ChatError` is raised so the caller can warn the
        user before overwriting anything.

        Args:
            path: File-system path (``str`` or ``pathlib.Path``) for the
                session JSON.

        Raises:
            ChatError: If *path* exists but cannot be parsed as a valid session.
        """
        from .conversation import DEFAULT_LOCAL_ENDPOINT  # local import to avoid circular
        self.path = Path(path)
        self.turns: list = []
        self.profile: str = ''
        self.endpoint: str = DEFAULT_LOCAL_ENDPOINT
        self.model: str = ''
        self.provider: str = 'local'

        if not self.path.exists():
            return
        try:
            if self.path.stat().st_size > _SESSION_LIMIT:
                raise ValueError('file too large')
            data = json.loads(self.path.read_text(encoding='utf-8'))
            turns = data.get('turns', [])
            if not isinstance(turns, list) or len(turns) > _MAX_TURNS:
                raise ValueError('turns invalid')
            for turn in turns:
                if (not isinstance(turn, dict)
                        or set(turn) != {'user', 'assistant'}
                        or any(not isinstance(v, str) or not v or len(v) > 12000
                               for v in turn.values())):
                    raise ValueError('turn structure invalid')
            profile = data.get('profile', '')
            model = data.get('model', '')
            provider = data.get('provider', 'local')
            endpoint = data.get('endpoint', self.endpoint)
            if (not isinstance(profile, str) or len(profile) > 2000
                    or not isinstance(model, str) or len(model) > 200
                    or not isinstance(endpoint, str)):
                raise ValueError('field invalid')
            if provider not in ('local', 'genai'):
                provider = 'local'
            self.turns = turns
            self.profile = profile
            self.model = model
            self.provider = provider
            self.endpoint = endpoint
        except (OSError, ValueError, KeyError, TypeError, UnicodeError):
            raise ChatError(
                '对话文件无法读取。原文件已保留，请先备份并检查文件内容。'
            ) from None

    def save(self) -> None:
        """Atomically persist the session to :attr:`path`.

        Uses a write-to-temp-then-replace strategy identical to
        :meth:`~si_pet.conversation.Conversation.save` so that crashes during
        a write never produce a half-written file.

        Raises:
            ChatError: If the directory cannot be created or the file cannot be
                written.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        try:
            with temp.open('w', encoding='utf-8') as f:
                json.dump(
                    {
                        'turns': self.turns,
                        'profile': self.profile,
                        'endpoint': self.endpoint,
                        'model': self.model,
                        'provider': self.provider,
                    },
                    f,
                    ensure_ascii=False,
                )
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, self.path)
        except OSError:
            raise ChatError('对话无法保存，请检查存档目录是否可写。') from None

    def messages(self, text: str, image_data_urls: list = ()) -> list:
        """Build the full messages list for one inference call.

        Includes the system PERSONA, an optional profile hint, the stored
        conversation history, and the new user turn.  If *image_data_urls* is
        non-empty the last user message is converted to a multipart list via
        :func:`attach_images`.

        Args:
            text: The user's current message.  Must be non-empty.
            image_data_urls: Zero or more ``data:image/jpeg;base64,…`` strings.

        Returns:
            A list of message dicts ready to pass to :func:`request_reply`.

        Raises:
            ChatError: If *text* is empty or too long, or if the image URLs are
                structurally invalid.
        """
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ChatError('每次请输入 1 至 2000 字。')
        msgs: list = [{'role': 'system', 'content': PERSONA}]
        if self.profile:
            msgs.append({'role': 'user', 'content': '管理员自述偏好（资料）：' + self.profile})
        for turn in self.turns:
            msgs.append({'role': 'user', 'content': turn['user']})
            msgs.append({'role': 'assistant', 'content': turn['assistant']})
        msgs.append({'role': 'user', 'content': text.strip()})
        if image_data_urls:
            try:
                msgs = attach_images(msgs, list(image_data_urls))
            except ChatError:
                raise
        return msgs

    def commit(self, text: str, reply: str) -> None:
        """Append a completed turn and persist the session.

        Keeps only the most recent ``_MAX_TURNS`` (12) turns.  If
        :meth:`save` raises, the in-memory state is rolled back so it stays
        consistent with what is on disk.

        Args:
            text: The user's message for this turn (will be ``.strip()``-ped).
            reply: The assistant's reply.

        Raises:
            ChatError: Propagated from :meth:`save` if the file cannot be written.
        """
        previous = self.turns
        self.turns = (self.turns + [{'user': text.strip(), 'assistant': reply}])[-_MAX_TURNS:]
        try:
            self.save()
        except Exception:
            self.turns = previous
            raise
