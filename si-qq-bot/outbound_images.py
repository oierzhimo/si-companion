"""Local image generation and curated reactions, with native OneBot payloads.

No remote URLs, shell commands, arbitrary filesystem reads or import-time I/O.
The adapter owns authorization, deduplication and delivery acknowledgement.
"""
import base64
import io
import json
import re
import secrets
import time
import urllib.parse
import urllib.request
from pathlib import Path

MAX_BYTES = 8_000_000
CHECKPOINT = 'sdxl\\anime\\animagine\\animagine-xl-4.0.safetensors'
GENERAL_CHECKPOINT = 'sdxl\\realistic\\RealVisXL_V5.0_fp16.safetensors'


def select_checkpoint(description):
    """Keep explicit anime/Si requests stylized; use general SDXL otherwise."""
    return CHECKPOINT if re.search(r'动漫|二次元|动画|插画|小祀|干员祀|角色祀|^祀|祀的|\banime\b', description, re.I) else GENERAL_CHECKPOINT


def image_segment(payload):
    from PIL import Image
    if not isinstance(payload, bytes) or not 32 <= len(payload) <= MAX_BYTES:
        raise ValueError('invalid image size')
    with Image.open(io.BytesIO(payload)) as image:
        if image.format not in ('PNG', 'JPEG', 'GIF', 'WEBP'):
            raise ValueError('unsupported image')
        if not 1 <= image.width * image.height <= 16_000_000:
            raise ValueError('invalid dimensions')
        image.verify()
    return {'type': 'image', 'data': {
        'file': 'base64://' + base64.b64encode(payload).decode('ascii')}}


def request_kind(text):
    """Explicit requests only; ordinary conversation never starts a GPU job."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    match = re.fullmatch(r'(?:/画图\s+|(?:请)?(?:帮我|给我)?(?:生成(?:一张|个|张)?(?:图片|图像|图)|画(?:一张|张)?(?:图片|图像|图)?)[：:\s]*)(.{2,500})', text)
    if match:
        return 'generate', match.group(1)
    match = re.fullmatch(r'(?:/表情\s*|(?:请)?(?:发|来|给我发)(?:一张|个|张)?表情包[：:\s]*)([\w\u4e00-\u9fff-]{0,24})', text)
    return ('reaction', match.group(1) or '默认') if match else None


def reaction_segment(directory, name):
    """Only explicit names from the local curated manifest can be sent."""
    directory = Path(directory).resolve()
    manifest = directory / 'manifest.json'
    if manifest.stat().st_size > 65536:
        raise ValueError('invalid reaction manifest')
    entries = json.loads(manifest.read_text(encoding='utf-8'))
    filename = entries.get(name) if isinstance(entries, dict) else None
    if not isinstance(filename, str) or Path(filename).name != filename or '/' in filename or '\\' in filename:
        raise ValueError('reaction unavailable')
    path = (directory / filename).resolve()
    if path.parent != directory or path.stat().st_size > MAX_BYTES:
        raise ValueError('invalid reaction path')
    return image_segment(path.read_bytes())


def _request(route, payload=None):
    body = None if payload is None else json.dumps(payload).encode('utf-8')
    request = urllib.request.Request('http://127.0.0.1:8188/' + route,
                                     data=body, headers={'Content-Type': 'application/json'})
    # Do not inherit a system proxy for local-only services.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=20) as response:
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('response too large')
        return raw


def generate_segment(prompt, *, timeout=180, request=None, clock=None, pause=None, checkpoint=CHECKPOINT):
    """Submit one fixed SDXL graph; fetch only that job's output image."""
    if not isinstance(prompt, str) or not 2 <= len(prompt.strip()) <= 1500:
        raise ValueError('invalid prompt')
    if checkpoint not in (CHECKPOINT, GENERAL_CHECKPOINT):
        raise ValueError('unsupported local checkpoint')
    request, clock, pause = request or _request, clock or time.monotonic, pause or time.sleep
    graph = {
        '1': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': checkpoint}},
        '2': {'class_type': 'CLIPTextEncode', 'inputs': {'text': prompt, 'clip': ['1', 1]}},
        '3': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'low quality, text, watermark', 'clip': ['1', 1]}},
        '4': {'class_type': 'EmptyLatentImage', 'inputs': {'width': 768, 'height': 768, 'batch_size': 1}},
        '5': {'class_type': 'KSampler', 'inputs': {'model': ['1', 0], 'positive': ['2', 0], 'negative': ['3', 0], 'latent_image': ['4', 0], 'seed': secrets.randbelow(2**32), 'steps': 20, 'cfg': 6, 'sampler_name': 'euler', 'scheduler': 'normal', 'denoise': 1}},
        '6': {'class_type': 'VAEDecode', 'inputs': {'samples': ['5', 0], 'vae': ['1', 2]}},
        '7': {'class_type': 'SaveImage', 'inputs': {'images': ['6', 0], 'filename_prefix': 'SiQQ'}}}
    result = json.loads(request('prompt', {'prompt': graph}))
    job = result.get('prompt_id')
    if not isinstance(job, str) or not re.fullmatch(r'[a-zA-Z0-9-]{1,100}', job):
        raise ValueError('generation not accepted')
    deadline = clock() + timeout
    while clock() < deadline:
        entry = json.loads(request('history/' + job)).get(job)
        if entry:
            if entry.get('status', {}).get('status_str') == 'error':
                raise ValueError('generation failed')
            images = entry.get('outputs', {}).get('7', {}).get('images', [])
            if images:
                image = images[0]
                if image.get('type') != 'output':
                    raise ValueError('invalid output')
                query = urllib.parse.urlencode({key: image.get(key, '') for key in ('filename', 'subfolder', 'type')})
                return image_segment(request('view?' + query))
        pause(1)
    # Never interrupt another application's queued or running job.
    raise TimeoutError('generation still pending')
