"""Serialize this companion's GPU jobs and release owned idle model caches.

No process termination, file deletion, global proxy or system setting changes.
External ComfyUI jobs are preserved; the caller receives a busy error.
"""
from contextlib import contextmanager, nullcontext
from functools import wraps
from pathlib import Path
import json
import os
import threading
import urllib.request
import urllib.error
import urllib.parse

_LOCK = threading.RLock()
_ALIASES = {'si-qwen3.5:9b', 'si-qwen2.5vl:7b'}


class ResourceBusy(RuntimeError):
    pass


def _json(port, route, body=None):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    raw = None if body is None else json.dumps(body).encode('utf-8')
    req = urllib.request.Request(f'http://127.0.0.1:{port}/{route}', data=raw,
                                 headers={'Content-Type': 'application/json'})
    with opener.open(req, timeout=20) as response:
        payload = response.read(131073)
    if len(payload) > 131072:
        raise ValueError('invalid local resource response')
    return json.loads(payload) if payload else {}


def _release_comfy(request):
    try:
        state = request(8188, 'queue')
    except (OSError, urllib.error.URLError):
        return
    if state.get('queue_running') or state.get('queue_pending'):
        raise ResourceBusy('本机绘图任务尚在运行，请稍后再试。')
    request(8188, 'free', {'unload_models': True, 'free_memory': True})


def _release_audio(request, config_path):
    try:
        path = Path(config_path)
        if path.stat().st_size > 65536:
            return
        config = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return
    if not isinstance(config, dict) or not isinstance(config.get('models'), list):
        return
    if config.get('host') != '127.0.0.1' or config.get('port') != 17860:
        return
    expected = {item['id'] for item in config['models']
                if isinstance(item, dict) and isinstance(item.get('id'), str)}
    if not expected:
        return
    try:
        actual = request(17860, 'v1/models')
    except (OSError, urllib.error.URLError):
        return
    ids = {item.get('id') for item in actual.get('data', []) if isinstance(item, dict)}
    if not expected <= ids:
        return  # Never unload an unrelated listener.
    request(17860, 'v1/tasks/unload_all_models', {})


def _release_text(request):
    try:
        state = request(11434, 'api/ps')
    except (OSError, urllib.error.URLError):
        return
    for model in state.get('models', []):
        name = model.get('name') if isinstance(model, dict) else None
        if name in _ALIASES:
            request(11434, 'api/generate', {'model': name, 'keep_alive': 0})


@contextmanager
def resource_stage(stage, *, request=None, audio_config=None):
    if stage not in ('text', 'voice', 'image'):
        raise ValueError('unknown resource stage')
    request = request or _json
    config = audio_config or Path(os.environ.get('APPDATA', '')) / 'OmniVoice/audiocpp/server.json'
    with _LOCK:
        # Check external drawing jobs before releasing any other cache.
        _release_comfy(request)
        if stage != 'voice':
            _release_audio(request, config)
        if stage != 'text':
            _release_text(request)
        yield


def guard_local_text(function):
    @wraps(function)
    def call(connection, *args, **kwargs):
        with connection._lock:
            snapshot = connection._settings
            injected = connection._request_fn is not None
        local = (snapshot is not None and snapshot.provider == 'local'
                 and urllib.parse.urlsplit(snapshot.endpoint).hostname in ('127.0.0.1', 'localhost')
                 and urllib.parse.urlsplit(snapshot.endpoint).port == 11434)
        with resource_stage('text') if local and not injected else nullcontext():
            return function(connection, *args, **kwargs)
    return call
