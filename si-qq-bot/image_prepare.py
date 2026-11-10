"""Prepare a single already-authorized image in memory for the local proxy.

No network or filesystem access occurs here. The caller must authorize the
sender before obtaining image bytes and must disclose image upload to GenAI.
"""
import base64
import io
import re
import warnings
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 20_000_000
MAX_SIDE = 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
FORMATS = {'JPEG', 'PNG', 'GIF', 'WEBP'}
_FILE_TOKEN = re.compile(r'[A-Za-z0-9{}_.-]{1,160}\Z')


def authorized_parts(event, bot_id, admin_id):
    """Extract supported segments only after confirming an authorized friend.

    Image file values are opaque OneBot identifiers, never URLs or paths.
    This parser does not fetch images or interpret numeric QQ face IDs.
    """
    if not all(isinstance(x, str) and x.isascii() and x.isdecimal() and len(x) <= 32 for x in (bot_id, admin_id)) or int(bot_id) == int(admin_id):
        return None
    if not isinstance(event, dict) or event.get('post_type') != 'message' or event.get('message_type') != 'private' or event.get('sub_type') != 'friend':
        return None
    def same(value, expected):
        return (not isinstance(value, bool) and isinstance(value, (int, str))
                and str(value).isascii() and str(value).isdecimal() and len(str(value)) <= 32
                and int(value) == int(expected))
    if not same(event.get('self_id'), bot_id) or not same(event.get('user_id'), admin_id):
        return None
    segments = event.get('message')
    if not isinstance(segments, list) or not 1 <= len(segments) <= 32:
        return None
    texts, images = [], []
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get('data'), dict):
            return None
        kind, data = segment.get('type'), segment['data']
        if kind == 'text':
            value = data.get('text')
            if not isinstance(value, str) or len(value) > 2000:
                return None
            texts.append(value)
        elif kind == 'image':
            value = data.get('file')
            if not isinstance(value, str) or not _FILE_TOKEN.fullmatch(value) or '..' in value or len(images) >= 4:
                return None
            images.append(value)
        elif kind == 'face':
            value = data.get('id')
            if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).isdecimal() or len(str(value)) > 8:
                return None
            texts.append(f'[QQ表情编号 {value}]')
        else:
            return None
    text = ''.join(texts).strip()
    return (text, images) if len(text) <= 2000 and (text or images) else None


class InvalidImage(ValueError):
    pass


def prepare_image(image_bytes):
    if not isinstance(image_bytes, bytes) or not 0 < len(image_bytes) <= MAX_INPUT_BYTES:
        raise InvalidImage('图片为空或超出大小限制')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(image_bytes)) as im:
                if im.format not in FORMATS or im.width <= 0 or im.height <= 0 or im.width * im.height > MAX_PIXELS:
                    raise InvalidImage('图片格式或尺寸不支持')
                im.seek(0)  # Animated stickers: inspect the first frame only.
                im = ImageOps.exif_transpose(im)
                im.thumbnail((MAX_SIDE, MAX_SIDE))
                if 'A' in im.getbands() or 'transparency' in im.info:
                    rgba = im.convert('RGBA')
                    background = Image.new('RGB', rgba.size, 'white')
                    background.paste(rgba, mask=rgba.getchannel('A'))
                    im = background
                else:
                    im = im.convert('RGB')
                output = io.BytesIO()
                # Re-encode to remove EXIF, metadata and embedded filenames.
                im.save(output, format='JPEG', quality=85, optimize=True)
                data = output.getvalue()
        if len(data) > MAX_OUTPUT_BYTES:
            raise InvalidImage('处理后的图片过大')
        return 'data:image/jpeg;base64,' + base64.b64encode(data).decode('ascii')
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        if isinstance(exc, InvalidImage):
            raise
        raise InvalidImage('图片无法读取') from None


def prepare_frames(image_bytes, frame_limit=3):
    """Sample at most three frames; return JPEG data URLs and animation flag.

    Stills or a caller-imposed limit of one produce one frame. Timing and
    motion are not reconstructed: the caller must tell the model these are
    sampled frames and it must not claim to have watched the whole animation.
    """
    if not isinstance(image_bytes, bytes) or not 0 < len(image_bytes) <= MAX_INPUT_BYTES:
        raise InvalidImage('图片为空或超出大小限制')
    if type(frame_limit) is not int or not 1 <= frame_limit <= 3:
        raise InvalidImage('帧数设置无效')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(image_bytes)) as im:
                if im.format not in FORMATS or im.width <= 0 or im.height <= 0 or im.width * im.height > MAX_PIXELS:
                    raise InvalidImage('图片格式或尺寸不支持')
                count = getattr(im, 'n_frames', 1)
                if count > 300:
                    raise InvalidImage('动画帧数过多')
                n = min(frame_limit, count)
                indexes = [round(i * (count - 1) / (n - 1)) for i in range(n)] if n > 1 else [0]
                results = []
                for index in indexes:
                    im.seek(index)
                    frame = ImageOps.exif_transpose(im.copy())
                    frame.thumbnail((MAX_SIDE, MAX_SIDE))
                    rgba = frame.convert('RGBA')
                    background = Image.new('RGB', rgba.size, 'white')
                    background.paste(rgba, mask=rgba.getchannel('A'))
                    out = io.BytesIO()
                    background.save(out, format='JPEG', quality=85, optimize=True)
                    data = out.getvalue()
                    if len(data) > MAX_OUTPUT_BYTES:
                        raise InvalidImage('处理后的图片过大')
                    results.append('data:image/jpeg;base64,' + base64.b64encode(data).decode('ascii'))
                return results, count > 1
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        if isinstance(exc, InvalidImage):
            raise
        raise InvalidImage('图片无法读取') from None


def attach_image(messages, image_data_url):
    """Return a copy of the messages with the image on the last user turn."""
    if not isinstance(messages, list) or not messages or not isinstance(messages[-1], dict):
        raise ValueError('invalid messages')
    last = messages[-1]
    if last.get('role') != 'user' or not isinstance(last.get('content'), str):
        raise ValueError('last message must be user text')
    if not isinstance(image_data_url, str) or not image_data_url.startswith('data:image/jpeg;base64,'):
        raise ValueError('invalid image')
    copied = [dict(message) for message in messages]
    copied[-1]['content'] = [
        {'type': 'text', 'text': last['content'] or '看看这张图。'},
        {'type': 'image_url', 'image_url': {'url': image_data_url}},
    ]
    return copied
