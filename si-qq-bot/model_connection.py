"""Small, replaceable OpenAI-compatible model connection.

The connection is intentionally an adapter, not a vendor integration:

* the school GenAI preset keeps the existing endpoint, model and public key;
* a local HTTP preset is restricted to loopback hosts;
* a custom HTTPS preset may use an ordinary API base path;
* only ``provider``, ``endpoint`` and ``model`` are ever written to disk;
* the API key lives in memory only and is excluded from ``repr``.

Both ``QQCore`` and ``GroupChat`` already accept an injected
``request_reply(endpoint, model, messages, api_key)`` callable.  A
``ModelConnection`` is such a callable: it ignores the arguments supplied by
those older call sites and uses one immutable snapshot of the user's active
settings for each call.  A call that is already in flight keeps the snapshot
it captured; a later Apply affects only future private and group calls.
"""
from __future__ import annotations

import ipaddress
import json
import re
import ssl
import stat
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from core import _CONVERSATION as _conversation
from core import _atomic_write_json

# Use the exact class the QQ core already recognises, so fixed request errors
# keep their existing diagnostic categories.  All messages raised with it are
# fixed constants from this module (or the two legacy fixed messages reused
# below); no response body, endpoint, key, or arbitrary exception text is ever
# placed in one.
ModelConnectionError = _conversation.ConversationError

MODEL_CONNECTION_FILENAME = 'model_connection.json'
MODEL_CONNECTION_SCHEMA = 1
MAX_CONFIG_BYTES = 4096
MAX_ENDPOINT_LENGTH = 2048
MAX_MODEL_LENGTH = 200
MAX_API_KEY_LENGTH = 512
MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REPLY_CHARS = 12000
# A requested prose length raises the output budget, but never without bound.
MAX_LENGTH_TOKENS = 4096
MAX_MODEL_IDS = 200
MAX_MODEL_ID_LENGTH = 200
MODEL_TEST_TIMEOUT = 20
MODEL_REQUEST_TIMEOUT = 90
LOCAL_MODEL_REQUEST_TIMEOUT = 180

SCHOOL_PROVIDER = 'school'
LOCAL_PROVIDER = 'local'
CUSTOM_PROVIDER = 'custom'
GEMINI_PROVIDER = 'gemini'
GEMINI_ENDPOINT = 'https://openai-gemini-nu-two.vercel.app/v1'
GEMINI_MODEL = 'gemini-3.6-flash'
PROVIDERS = (GEMINI_PROVIDER, SCHOOL_PROVIDER, LOCAL_PROVIDER, CUSTOM_PROVIDER)
PROVIDER_LABELS = {
    GEMINI_PROVIDER: 'Vercel → Gemini',
    SCHOOL_PROVIDER: '学校 GenAI',
    LOCAL_PROVIDER: '本机 HTTP 模型',
    CUSTOM_PROVIDER: '自定义 HTTPS 模型',
}

# The current documented school preset.  It must stay byte-for-byte compatible
# with the previous hard-coded core defaults.
SCHOOL_ENDPOINT = 'http://127.0.0.1:8000/v1'
SCHOOL_MODEL = 'DeepSeek-V4.1-Flash'
SCHOOL_API_KEY = 'sk-shanghaitech-genai'

# Preset examples only.  Official DeepSeek model names are not pinned to an
# old version: any recognisable domestic family spelling is accepted and the
# user's spelling is preserved exactly.
LOCAL_DEFAULT_ENDPOINT = 'http://127.0.0.1:11434/v1'
LOCAL_DEFAULT_MODEL = 'qwen2.5vl:7b'
CUSTOM_ENDPOINT_PLACEHOLDER = 'https://model-host.example/v1'
CUSTOM_MODEL_PLACEHOLDER = 'deepseek-v4-pro'

_ALLOWED_FAMILIES = (
    'deepseek', 'qwen', 'glm', 'kimi', 'moonshot', 'internlm', 'minicpm',
)
# A plain substring check is deliberate: a hidden suffix such as
# ``deepseek-gpt4o`` or ``deepseekgpt`` must never sneak a GPT/OpenAI-branded
# model past the domestic-family check.
_BLOCKED_MODEL_TOKENS = ('gpt', 'openai', 'chatgpt')

_PATH_PATTERN = re.compile(r'^(?:/[A-Za-z0-9._~-]+)*/?$')
_HOSTNAME_PATTERN = re.compile(
    r'^(?=.{1,253}$)'
    r'(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)'
    r'(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$'
)

# Fixed, user-safe messages.  They never interpolate server output, a raw
# exception, an endpoint, a key, a model list, or chat/profile content.
ERROR_PROVIDER_TEXT = '模型连接预设无效，请重新选择。'
ERROR_ENDPOINT_TEXT = (
    '模型地址无效：HTTPS 可填写远程主机，HTTP 只允许本机回环地址；'
    '地址不能包含账号、查询或片段。'
)
ERROR_ENDPOINT_PATH_TEXT = (
    '模型地址路径无效；请填写普通 API 基础路径，例如 /v1。'
)
ERROR_MODEL_TEXT = (
    '模型名称无效；只支持 DeepSeek、Qwen、GLM、Kimi/Moonshot、InternLM、'
    'MiniCPM 等国内模型；Gemini 请选专用预设。不接受 GPT/OpenAI/ChatGPT 命名。'
)
ERROR_GEMINI_PRESET_TEXT = 'Gemini 预设只使用指定的 Vercel 地址和 gemini- 开头的模型。'
ERROR_GEMINI_KEY_TEXT = '尚未输入 Gemini API Key；请在“模型连接”中输入并应用。'
ERROR_MODEL_LENGTH_TEXT = '模型名称过长，请检查后重试。'
ERROR_KEY_TEXT = 'API Key 格式无效，请重新输入。'
ERROR_SCHOOL_PRESET_TEXT = (
    '学校预设的地址和模型名称必须保持当前学校设置；'
    '如需其他服务，请选择本机或自定义 HTTPS。'
)
ERROR_SCHOOL_KEY_TEXT = '学校预设需要填写学校公开 API Key。'
ERROR_LOCAL_HTTP_TEXT = (
    '本机预设只允许 HTTP 本机回环地址；远程服务请选择自定义 HTTPS。'
)
ERROR_CUSTOM_HTTPS_TEXT = '自定义预设只允许 HTTPS 地址。'
ERROR_REQUEST_MESSAGES_TEXT = '模型请求格式无效。'
ERROR_RESPONSE_TEXT = '本机接口没有返回可显示的文字回复。'
ERROR_MODELS_FORMAT_TEXT = '模型服务返回的模型列表格式无法识别。'
ERROR_MODELS_EMPTY_TEXT = '模型服务没有返回可用的模型名称。'
ERROR_TOO_LARGE_TEXT = '模型服务返回内容过大，无法读取。'
ERROR_REDIRECT_TEXT = '模型服务发生重定向，已停止连接。'
ERROR_TLS_UNAVAILABLE_TEXT = '此程序无法加载安全连接组件，HTTPS 模型连接不可用。'
ERROR_TLS_FAILURE_TEXT = '模型服务的安全连接（TLS）失败；请检查地址与证书。'
ERROR_CONNECTION_TEXT = '无法连接模型服务。'
ERROR_LOCAL_ABSENT_TEXT = (
    '无法连接本机模型服务；请确认本机反代或模型服务已启动。'
)
ERROR_REMOTE_CONNECTION_TEXT = (
    '无法连接远程模型服务；请检查地址与网络后重试。'
)
ERROR_TIMEOUT_TEXT = '模型服务响应超时，请稍后重试。'
ERROR_AUTH_TEXT = '模型服务拒绝访问；请检查 API Key 后重试。'
ERROR_SCHOOL_AUTH_TEXT = (
    '学校 GenAI 登录可能已过期或访问被拒绝；'
    '请重新登录学校 GenAI 后重试。'
)
ERROR_SCHOOL_UNAVAILABLE_TEXT = (
    '学校 GenAI 反代暂不可用或学校登录已过期；'
    '请重新登录学校 GenAI，并确认反代服务在运行。'
)
ERROR_LOCAL_UNAVAILABLE_TEXT = '本机模型服务暂不可用；请确认本机服务已启动。'
ERROR_REMOTE_UNAVAILABLE_TEXT = '远程模型服务暂不可用，请稍后重试。'
ERROR_BUSY_TEXT = '模型服务正忙，请稍后重试。'
ERROR_HTTP_TEXT = '模型服务返回错误；请检查服务状态与模型名称。'
ERROR_TEST_FAILED_TEXT = '模型连接测试未能完成；请稍后重试。'
ERROR_CONFIG_UNAVAILABLE_TEXT = (
    '模型连接设置不可用；请打开“模型连接”重新应用有效设置。'
)
ERROR_CONFIG_STALE_TEXT = (
    '模型连接设置已被其他程序修改；为避免覆盖，本次未保存，原文件保持不变。'
    '请重新打开本窗口后再设置。'
)
ERROR_CONFIG_SAVE_TEXT = (
    '无法保存模型连接设置；本次未启用新设置。请检查本机数据目录是否可写。'
)
ERROR_CONFIG_CORRUPT_TEXT = (
    '模型连接设置已损坏或格式无效；为安全起见，本次不会使用任何模型连接，'
    '也不会改写原文件。请打开“模型连接”重新应用有效设置。'
)
ERROR_CONFIG_UNREADABLE_TEXT = (
    '模型连接设置文件存在但无法读取；为安全起见，本次不会使用任何模型连接，'
    '也不会改写原文件。请检查文件权限后重新应用。'
)

TEST_SUCCESS_TEXT = {
    SCHOOL_PROVIDER: '学校 GenAI 服务与登录可用；尚未验证实际模型回复，未发送聊天内容。',
    LOCAL_PROVIDER: '接口可达：已读取模型列表，未发送聊天内容；尚未验证实际模型回复。',
    CUSTOM_PROVIDER: 'HTTPS 接口可达：已读取模型列表，未发送聊天内容；尚未验证实际模型回复。',
    GEMINI_PROVIDER: 'Vercel 接口可达：已读取模型列表，未发送聊天内容；尚未验证实际模型回复。',
}

# Exact legacy fixed messages reused for diagnostics that QQCore/GroupChat
# already classify.  ``本机`` is retained because the adapter's fixed status
# text and existing tests share these categories; the model dialog reports the
# provider-specific messages above instead.
_LEGACY_AUTH_MESSAGE = '本机反代访问口令有误。请检查“反代访问口令”后重试。'
_LEGACY_SCHOOL_LOGIN_MESSAGE = (
    '本机反代或学校登录暂不可用。请先完成学校登录，并确认反代服务正在运行。'
)
_LEGACY_BUSY_MESSAGE = '本机模型服务正忙，请稍后重试。'
_LEGACY_HTTP_STATUS = '本机模型返回状态 {code}，请检查服务与模型名称。'

_FIXED_MESSAGES = frozenset({
    ERROR_PROVIDER_TEXT, ERROR_ENDPOINT_TEXT, ERROR_ENDPOINT_PATH_TEXT,
    ERROR_GEMINI_PRESET_TEXT, ERROR_GEMINI_KEY_TEXT,
    ERROR_MODEL_TEXT, ERROR_MODEL_LENGTH_TEXT, ERROR_KEY_TEXT,
    ERROR_SCHOOL_PRESET_TEXT, ERROR_SCHOOL_KEY_TEXT, ERROR_LOCAL_HTTP_TEXT,
    ERROR_CUSTOM_HTTPS_TEXT, ERROR_REQUEST_MESSAGES_TEXT,
    ERROR_RESPONSE_TEXT, ERROR_MODELS_FORMAT_TEXT, ERROR_MODELS_EMPTY_TEXT,
    ERROR_TOO_LARGE_TEXT, ERROR_REDIRECT_TEXT, ERROR_TLS_UNAVAILABLE_TEXT,
    ERROR_TLS_FAILURE_TEXT, ERROR_CONNECTION_TEXT, ERROR_LOCAL_ABSENT_TEXT,
    ERROR_REMOTE_CONNECTION_TEXT, ERROR_TIMEOUT_TEXT, ERROR_AUTH_TEXT,
    ERROR_SCHOOL_AUTH_TEXT, ERROR_SCHOOL_UNAVAILABLE_TEXT,
    ERROR_LOCAL_UNAVAILABLE_TEXT, ERROR_REMOTE_UNAVAILABLE_TEXT,
    ERROR_BUSY_TEXT, ERROR_HTTP_TEXT, ERROR_TEST_FAILED_TEXT,
    ERROR_CONFIG_UNAVAILABLE_TEXT, ERROR_CONFIG_STALE_TEXT,
    ERROR_CONFIG_SAVE_TEXT, ERROR_CONFIG_CORRUPT_TEXT,
    ERROR_CONFIG_UNREADABLE_TEXT,
    _LEGACY_AUTH_MESSAGE, _LEGACY_SCHOOL_LOGIN_MESSAGE,
    _LEGACY_BUSY_MESSAGE,
})


def fixed_message(exc, fallback):
    """Return only an allowlisted fixed message from *exc*.

    ``ModelConnectionError`` is the conversation error class, so callers must
    not assume every instance came from this module.  Arbitrary exception text
    (which might contain an endpoint, key, body, or chat content) is replaced
    by *fallback*.
    """
    try:
        args = exc.args
        if (type(args) is tuple and len(args) == 1
                and type(args[0]) is str and args[0] in _FIXED_MESSAGES):
            return args[0]
    except Exception:
        pass
    return fallback


@dataclass(frozen=True, slots=True)
class ConnectionSnapshot:
    """One immutable model-connection selection.

    ``api_key`` is excluded from ``repr`` so diagnostics and test failure
    messages cannot accidentally print it.
    """

    provider: str
    endpoint: str
    model: str
    api_key: str = field(default='', repr=False)


def _is_ascii_printable(value):
    return all(33 <= ord(ch) <= 126 for ch in value)


def validate_endpoint(value):
    """Validate and normalise an OpenAI-compatible API base URL.

    Rules:
    * bounded ASCII text with no control characters, spaces, userinfo,
      query, or fragment;
    * ``http`` only for a numeric loopback address or ``localhost``;
    * ``https`` for any ordinary host;
    * a plain, traversal-free base path such as ``/v1`` or ``/api/v1``.
    """
    if not isinstance(value, str):
        raise ModelConnectionError(ERROR_ENDPOINT_TEXT)
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ModelConnectionError(ERROR_ENDPOINT_TEXT)
    candidate = value.strip()
    if (not candidate or len(candidate) > MAX_ENDPOINT_LENGTH
            or not _is_ascii_printable(candidate)):
        raise ModelConnectionError(ERROR_ENDPOINT_TEXT)
    if '?' in candidate or '#' in candidate:
        raise ModelConnectionError(ERROR_ENDPOINT_TEXT)
    try:
        parsed = urllib.parse.urlsplit(candidate)
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        port = parsed.port
    except (AttributeError, ValueError, TypeError):
        raise ModelConnectionError(ERROR_ENDPOINT_TEXT) from None
    if (scheme not in ('http', 'https') or not host
            or not parsed.netloc or '@' in parsed.netloc
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment
            or parsed.netloc.endswith(':')):
        raise ModelConnectionError(ERROR_ENDPOINT_TEXT)

    path = parsed.path
    if not _PATH_PATTERN.fullmatch(path):
        raise ModelConnectionError(ERROR_ENDPOINT_PATH_TEXT)
    if any(part in ('.', '..') for part in path.split('/')):
        raise ModelConnectionError(ERROR_ENDPOINT_PATH_TEXT)

    # ``hostname`` is already lower-cased for DNS names.  IPv6 literals keep
    # their colons here but arrive without the URL brackets.
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
        if not _HOSTNAME_PATTERN.fullmatch(host):
            raise ModelConnectionError(ERROR_ENDPOINT_TEXT) from None

    if scheme == 'http':
        if host != 'localhost':
            if address is None or not address.is_loopback:
                raise ModelConnectionError(ERROR_ENDPOINT_TEXT)
    if port is not None and not (1 <= port <= 65535):
        raise ModelConnectionError(ERROR_ENDPOINT_TEXT)
    return candidate.rstrip('/')


def validate_model(value, *, provider=''):
    """Validate a domestic open-source model name without changing its case.

    The user's spelling is returned unchanged (apart from surrounding
    whitespace).  GPT/OpenAI/ChatGPT branding is rejected even when hidden
    inside an otherwise allowed name.
    """
    if not isinstance(value, str):
        raise ModelConnectionError(ERROR_MODEL_TEXT)
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ModelConnectionError(ERROR_MODEL_TEXT)
    candidate = value.strip()
    if not candidate:
        raise ModelConnectionError(ERROR_MODEL_TEXT)
    if len(candidate) > MAX_MODEL_LENGTH:
        raise ModelConnectionError(ERROR_MODEL_LENGTH_TEXT)
    folded = candidate.casefold()
    if any(token in folded for token in _BLOCKED_MODEL_TOKENS):
        raise ModelConnectionError(ERROR_MODEL_TEXT)
    if provider == GEMINI_PROVIDER:
        if not re.fullmatch(r'gemini-[a-z0-9][a-z0-9._-]*', folded):
            raise ModelConnectionError(ERROR_GEMINI_PRESET_TEXT)
        return candidate
    if not any(family in folded for family in _ALLOWED_FAMILIES):
        raise ModelConnectionError(ERROR_MODEL_TEXT)
    return candidate


def validate_api_key(value):
    """Validate an optional header-only key; never normalise or store it."""
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise ModelConnectionError(ERROR_KEY_TEXT)
    if (len(value) > MAX_API_KEY_LENGTH or '\r' in value or '\n' in value
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        raise ModelConnectionError(ERROR_KEY_TEXT)
    try:
        value.encode('latin-1')
    except UnicodeEncodeError:
        raise ModelConnectionError(ERROR_KEY_TEXT) from None
    return value


def prepare_snapshot(provider, endpoint, model, api_key=''):
    """Validate an explicit user choice and return an immutable snapshot."""
    if provider not in PROVIDERS:
        raise ModelConnectionError(ERROR_PROVIDER_TEXT)
    key = validate_api_key(api_key)
    base = validate_endpoint(endpoint)
    name = validate_model(model, provider=provider)
    scheme = urllib.parse.urlsplit(base).scheme.lower()

    if provider == GEMINI_PROVIDER:
        if base != GEMINI_ENDPOINT:
            raise ModelConnectionError(ERROR_GEMINI_PRESET_TEXT)
    elif provider == SCHOOL_PROVIDER:
        # No silent substitution: a school selection must still match the
        # documented preset exactly.  A user wanting another service selects
        # the local or custom preset instead.
        if base != SCHOOL_ENDPOINT or name != SCHOOL_MODEL:
            raise ModelConnectionError(ERROR_SCHOOL_PRESET_TEXT)
        if not key:
            raise ModelConnectionError(ERROR_SCHOOL_KEY_TEXT)
    elif provider == LOCAL_PROVIDER:
        if scheme != 'http':
            raise ModelConnectionError(ERROR_LOCAL_HTTP_TEXT)
    else:
        if scheme != 'https':
            raise ModelConnectionError(ERROR_CUSTOM_HTTPS_TEXT)
    return ConnectionSnapshot(provider, base, name, key)


def snapshot_payload(snapshot):
    """The complete on-disk schema: no key, no derived or transient fields."""
    return {
        'schema': MODEL_CONNECTION_SCHEMA,
        'provider': snapshot.provider,
        'endpoint': snapshot.endpoint,
        'model': snapshot.model,
    }


def _encode_payload(payload):
    return json.dumps(
        payload, ensure_ascii=False, separators=(',', ':')
    ).encode('utf-8')


def _read_config_bytes(path):
    """Read at most ``MAX_CONFIG_BYTES``; ``None`` means the file is absent."""
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CONFIG_BYTES:
        raise ValueError('invalid model connection file')
    raw = path.read_bytes()
    if len(raw) > MAX_CONFIG_BYTES:
        raise ValueError('invalid model connection file')
    return raw


def _snapshot_from_payload(payload):
    if type(payload) is not dict:
        raise ValueError('invalid model connection payload')
    if set(payload) != {'schema', 'provider', 'endpoint', 'model'}:
        raise ValueError('invalid model connection payload')
    if (type(payload.get('schema')) is not int
            or payload.get('schema') != MODEL_CONNECTION_SCHEMA):
        raise ValueError('invalid model connection schema')
    provider = payload.get('provider')
    endpoint = payload.get('endpoint')
    model = payload.get('model')
    if (type(provider) is not str or provider not in PROVIDERS
            or type(endpoint) is not str or type(model) is not str):
        raise ValueError('invalid model connection payload')
    # A key is never read from disk.  The school preset supplies its documented
    # public key from memory; other presets start with no key after a restart.
    api_key = SCHOOL_API_KEY if provider == SCHOOL_PROVIDER else ''
    # ``prepare_snapshot`` also enforces the preset's scheme and exact school
    # spelling, so a hand-edited or mismatched file fails closed.
    return prepare_snapshot(provider, endpoint, model, api_key)


def _school_snapshot():
    return ConnectionSnapshot(
        SCHOOL_PROVIDER, SCHOOL_ENDPOINT, SCHOOL_MODEL, SCHOOL_API_KEY
    )


class ModelConnection:
    """Active in-memory settings plus a small persisted adapter selection.

    The object is callable with the existing ``request_reply`` signature.  The
    endpoint/model/key arguments passed by old call sites are deliberately
    ignored; the active immutable snapshot is authoritative and is captured
    once per call.
    """

    def __init__(self, path, snapshot, *, load_error=None,
                 baseline_bytes=None, file_present=False, request_fn=None):
        self.path = Path(path)
        self._settings = snapshot
        self._load_error = load_error
        self._baseline_bytes = baseline_bytes
        self._file_present = bool(file_present)
        self._lock = threading.Lock()
        self._companion_tools_enabled = False
        from model_router import RouterConfig
        self._router_config = RouterConfig()
        self._search_enabled = True
        if request_fn is not None and not callable(request_fn):
            raise TypeError('request_fn must be callable')
        self._request_fn = request_fn

    @classmethod
    def load(cls, path, *, request_fn=None):
        """Load a saved selection without creating or modifying anything.

        A missing file keeps the documented school default.  An existing file
        that is unreadable, corrupt, or not an exact valid schema has no active
        snapshot at all; this is the fail-closed state and never silently
        enables a different endpoint.
        """
        path = Path(path)
        try:
            raw = _read_config_bytes(path)
        except OSError:
            return cls(path, None, load_error='unreadable',
                       baseline_bytes=None, file_present=True,
                       request_fn=request_fn)
        except (ValueError, TypeError):
            return cls(path, None, load_error='corrupt',
                       baseline_bytes=None, file_present=True,
                       request_fn=request_fn)
        if raw is None:
            return cls(path, _school_snapshot(), load_error=None,
                       baseline_bytes=None, file_present=False,
                       request_fn=request_fn)
        try:
            payload = json.loads(raw.decode('utf-8'))
            snapshot = _snapshot_from_payload(payload)
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError,
                ModelConnectionError):
            return cls(path, None, load_error='corrupt',
                       baseline_bytes=raw, file_present=True,
                       request_fn=request_fn)
        return cls(path, snapshot, baseline_bytes=raw, file_present=True,
                   request_fn=request_fn)

    @classmethod
    def failed(cls, path, load_error='corrupt', *, request_fn=None):
        """Construct an explicitly unusable connection (fail closed)."""
        return cls(path, None, load_error=load_error, baseline_bytes=None,
                   file_present=True, request_fn=request_fn)

    @property
    def load_error(self):
        return self._load_error

    @property
    def file_present(self):
        return self._file_present

    @property
    def requester(self):
        return self

    def current(self):
        with self._lock:
            return self._settings

    def configure_companion_tools(self, *, enabled=True, search_enabled=True):
        with self._lock:
            self._companion_tools_enabled = bool(enabled)
            self._search_enabled = bool(search_enabled)

    def commit(self, snapshot):
        """Install an already validated snapshot in memory only."""
        if not isinstance(snapshot, ConnectionSnapshot):
            raise TypeError('snapshot must be a ConnectionSnapshot')
        with self._lock:
            self._settings = snapshot
            self._load_error = None

    def unchanged_on_disk(self):
        """Whether the bytes this window loaded are still the current bytes."""
        try:
            raw = _read_config_bytes(self.path)
        except (OSError, ValueError, TypeError):
            return False
        if raw is None:
            return not self._file_present and self._baseline_bytes is None
        return (self._file_present
                and self._baseline_bytes is not None
                and raw == self._baseline_bytes)

    def apply(self, provider, endpoint, model, api_key=''):
        """Validate, refuse a stale overwrite, save, then activate.

        The key is only part of the returned in-memory snapshot.  It is never
        written to disk, and it is not installed if the atomic save fails.
        """
        snapshot = prepare_snapshot(provider, endpoint, model, api_key)
        if not self.unchanged_on_disk():
            raise ModelConnectionError(ERROR_CONFIG_STALE_TEXT)
        payload = snapshot_payload(snapshot)
        try:
            _atomic_write_json(self.path, payload)
        except Exception:
            raise ModelConnectionError(ERROR_CONFIG_SAVE_TEXT) from None
        with self._lock:
            self._settings = snapshot
            self._load_error = None
            self._baseline_bytes = _encode_payload(payload)
            self._file_present = True
        return snapshot

    def configure_router(self, config):
        from model_router import RouterConfig
        if not isinstance(config,RouterConfig):
            raise TypeError('Expected immutable RouterConfig')
        # Validate through the same local model-name policy as manual settings.
        validate_model(config.text_model,provider=LOCAL_PROVIDER)
        validate_model(config.vision_model,provider=LOCAL_PROVIDER)
        with self._lock:
            self._router_config=config

    def local_resource_context(self, stage):
        from contextlib import nullcontext
        from local_resources import resource_stage
        with self._lock:
            snapshot = self._settings
            injected = self._request_fn is not None
        if (snapshot is not None and snapshot.provider == LOCAL_PROVIDER and not injected
                and urllib.parse.urlsplit(snapshot.endpoint).hostname in ('127.0.0.1', 'localhost')
                and urllib.parse.urlsplit(snapshot.endpoint).port == 11434):
            return resource_stage(stage)
        return nullcontext()

    def request_visual_translation(self, messages):
        """Utility call using the selected model, without persona/history/media.

        Never toggle global tools or routing while other chats are active.
        """
        with self._lock:
            snapshot = self._settings
            router_config = self._router_config
            requester = self._request_fn
        if snapshot is None:
            raise ModelConnectionError(ERROR_CONFIG_UNAVAILABLE_TEXT)
        from model_router import select_route
        route = select_route(router_config, snapshot.provider, snapshot.endpoint,
                             snapshot.model, messages)
        args = (snapshot.endpoint, route.model, messages, snapshot.api_key)
        if requester is not None:
            return requester(*args)
        options = {'provider': snapshot.provider}
        if snapshot.provider == LOCAL_PROVIDER and urllib.parse.urlsplit(snapshot.endpoint).port == 11434:
            options['reasoning_effort'] = 'none'
        with self.local_resource_context('text'):
            return request_reply(*args, **options)

    from local_resources import guard_local_text as _guard_local_text

    @_guard_local_text
    def __call__(self, endpoint, model, messages, api_key=''):
        """Existing ``request_reply`` interface, backed by the active snapshot.

        ``endpoint``, ``model`` and ``api_key`` are accepted only for
        compatibility with QQCore/GroupChat; they are not used.  In
        particular, the old hard-coded loopback endpoint and school key can
        never override the explicitly selected connection.
        """
        with self._lock:
            snapshot = self._settings
            tools_enabled = self._companion_tools_enabled
            search_enabled = self._search_enabled
            router_config = self._router_config
        if snapshot is None:
            raise ModelConnectionError(ERROR_CONFIG_UNAVAILABLE_TEXT)
        if snapshot.provider == GEMINI_PROVIDER and not snapshot.api_key:
            raise ModelConnectionError(ERROR_GEMINI_KEY_TEXT)
        if not isinstance(messages, list) or not messages:
            raise ModelConnectionError(ERROR_REQUEST_MESSAGES_TEXT)
        sources = []
        generation_count = None
        requested_chars = None
        original_messages = messages
        if tools_enabled:
            from companion_style import grounded_recall_reply
            from media_support import has_pending_media
            # Do not invent yesterday's meal/drink when no such user fact is
            # available. Keep authorization, dedup and delivery in the cores;
            # skip this guard when a current attachment can provide evidence.
            if snapshot.provider==LOCAL_PROVIDER and not has_pending_media():
                grounded=grounded_recall_reply(messages)
                if grounded is not None:return grounded
            from companion_tools import (
                enrich, generation_count as requested_count,
                requested_char_count,
            )
            if snapshot.provider == LOCAL_PROVIDER and urllib.parse.urlsplit(snapshot.endpoint).port == 11434:
                generation_count = requested_count(messages)
            # Length is a transient creation constraint for every provider; it
            # is computed from the user's turn before search/media messages are
            # appended.
            requested_chars = requested_char_count(
                messages, require_creation=True
            )
            messages, sources = enrich(messages, search_enabled=search_enabled)
            from media_support import attach_pending
            messages = attach_pending(messages, local_enabled=snapshot.provider == LOCAL_PROVIDER)
        from model_router import select_route
        route=select_route(router_config,snapshot.provider,snapshot.endpoint,snapshot.model,messages)
        args = (snapshot.endpoint, route.model, messages, snapshot.api_key)
        if self._request_fn is None:
            options = {'provider':snapshot.provider}
            if route.reasoning_effort is not None:
                options['reasoning_effort']=route.reasoning_effort
            if generation_count is not None:
                options['generation_count'] = generation_count
            if requested_chars is not None:
                options['requested_chars'] = requested_chars
            reply = request_reply(*args, **options)
        else:
            reply = self._request_fn(*args)
        if tools_enabled:
            from companion_tools import polish_reply
            reply = polish_reply(reply, original_messages)
        if sources:
            from companion_tools import append_sources
            reply = append_sources(reply, sources)
        return reply


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Reject every redirect instead of following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ModelConnectionError(ERROR_REDIRECT_TEXT)


def _tls_context():
    # Deliberately lazy: an HTTP-only session must not initialise an SSL
    # context.  Default verification (certificate and hostname) is kept.
    try:
        return ssl.create_default_context()
    except Exception:
        raise ModelConnectionError(ERROR_TLS_UNAVAILABLE_TEXT) from None


def build_opener_for(scheme, *, tls_context_factory=None):
    """Build an opener without system proxies or redirects.

    The HTTPS handler is added only for an HTTPS endpoint.  This mirrors the
    explicit HTTP-only opener already used by ``si_pet.conversation`` and
    avoids the frozen-build failure caused by ``build_opener`` eagerly
    constructing an HTTPS handler for loopback HTTP.
    """
    scheme = str(scheme).lower()
    if scheme not in ('http', 'https'):
        raise ModelConnectionError(ERROR_ENDPOINT_TEXT)
    opener = urllib.request.OpenerDirector()
    handlers = [
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        NoRedirect(),
        urllib.request.HTTPErrorProcessor(),
    ]
    if scheme == 'https':
        factory = tls_context_factory or _tls_context
        try:
            context = factory()
        except ModelConnectionError:
            raise
        except Exception:
            raise ModelConnectionError(ERROR_TLS_UNAVAILABLE_TEXT) from None
        if context is None:
            raise ModelConnectionError(ERROR_TLS_UNAVAILABLE_TEXT)
        try:
            handlers.append(urllib.request.HTTPSHandler(context=context))
        except Exception:
            raise ModelConnectionError(ERROR_TLS_UNAVAILABLE_TEXT) from None
    for handler in handlers:
        opener.add_handler(handler)
    return opener


def _auth_headers(api_key):
    key = validate_api_key(api_key)
    return {'Authorization': 'Bearer ' + key} if key else {}


def _is_tls_failure(exc):
    reason = getattr(exc, 'reason', None)
    return isinstance(exc, ssl.SSLError) or isinstance(reason, ssl.SSLError)


def request_reply(endpoint, model, messages, api_key='', *, provider='',
                  opener=None, generation_count=None, requested_chars=None,
                  reasoning_effort=None):
    """POST one chat completion to exactly the supplied, validated endpoint.

    No retry and no fallback to another model or endpoint.  Redirects and
    system proxies are disabled.  The returned text is the model's content;
    error messages are fixed categories only.
    """
    base = validate_endpoint(endpoint)
    name = validate_model(model, provider=provider)
    key = validate_api_key(api_key)
    if provider == GEMINI_PROVIDER:
        prepare_snapshot(provider, base, name, key)
        if not key:
            raise ModelConnectionError(ERROR_GEMINI_KEY_TEXT)
    if not isinstance(messages, list) or not messages:
        raise ModelConnectionError(ERROR_REQUEST_MESSAGES_TEXT)
    from companion_tools import (
        LENGTH_INSTRUCTION_MARKER, MAX_REQUESTED_CHARS, length_instruction,
        requested_char_count, requested_person,
    )
    if requested_chars is None:
        requested_chars = requested_char_count(
            messages, require_creation=True
        )
    elif not (type(requested_chars) is int
              and 1 <= requested_chars <= MAX_REQUESTED_CHARS):
        requested_chars = None
    headers = {'Content-Type': 'application/json'}
    headers.update(_auth_headers(key))
    try:
        outgoing = messages
        if type(generation_count) is int and 1 <= generation_count <= 20:
            outgoing = list(messages)
            outgoing.insert(len(messages)-1, {'role':'system','content':
                f'当前任务要求生成{generation_count}项内容。请完整满足用户要求的数量与形式，'
                '不要用简短寒暄替代创作。按给定JSON格式输出 items 数组，每项对应用户要求的一句、一条或一段；程序会转换为普通消息。'})
            outgoing[-2]['content'] += (
                '你是祀，发言者身份以已有的管理员绑定与群聊规则为准，不因创作任务而改变。'
                '小葱是祀自己的外号，不可拿来称呼对方。'
                '每项直接给正文，不要添加用户未要求的称呼、姓名或昵称。')
        if requested_chars is not None:
            if outgoing is messages:
                outgoing = list(messages)
            already = any(
                isinstance(item, dict) and item.get('role') == 'system'
                and isinstance(item.get('content'), str)
                and LENGTH_INSTRUCTION_MARKER in item['content']
                for item in outgoing
            )
            if not already:
                outgoing.insert(len(outgoing)-1, {'role':'system','content':
                    length_instruction(requested_chars,
                                       requested_person(messages))})
        max_tokens = (4096 if provider == GEMINI_PROVIDER
                      else (2048 if provider == LOCAL_PROVIDER else 700))
        if requested_chars is not None:
            # Chinese prose needs roughly one to two tokens per character;
            # keep headroom for punctuation while bounding the request.
            max_tokens = max(
                max_tokens,
                min(MAX_LENGTH_TOKENS, requested_chars * 2 + 256),
            )
        payload = {'model': name, 'messages': outgoing, 'stream': False,
                   'max_tokens': max_tokens}
        if (provider==LOCAL_PROVIDER and urllib.parse.urlsplit(base).port==11434
                and name.lower()=='si-qwen3.5:9b'):
            # A high presence penalty made ordinary chat invent scene changes
            # and search for odd synonyms. Keep the app alias conversational;
            # original installed weights and all other providers are untouched.
            payload.update(temperature=.7,top_p=.9,presence_penalty=0)
        if (reasoning_effort is None and provider==LOCAL_PROVIDER
                and urllib.parse.urlsplit(base).port==11434
                and name.lower() in ('si-qwen3.5:9b','qwen3.5:9b-q4_k_m')):
            reasoning_effort='none'
        if reasoning_effort is not None:
            if (reasoning_effort!='none' or provider!=LOCAL_PROVIDER
                    or urllib.parse.urlsplit(base).port!=11434):
                raise ValueError('Unsupported thinking control')
            # Never expose a reasoning trace as a QQ answer. This explicitly
            # requests no thinking for the local conversational route only.
            payload['reasoning_effort']=reasoning_effort
        if type(generation_count) is int and 1 <= generation_count <= 20:
            payload['response_format'] = {'type':'json_schema','json_schema':{
                'name':'si_generated_items','schema':{
                    'type':'object','properties':{'items':{'type':'array','items':{'type':'string','minLength':1},
                     'minItems':generation_count,'maxItems':generation_count}},
                    'required':['items'],'additionalProperties':False}}}
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError):
        raise ModelConnectionError(ERROR_REQUEST_MESSAGES_TEXT) from None
    if len(body) > MAX_REQUEST_BYTES:
        raise ModelConnectionError(ERROR_REQUEST_MESSAGES_TEXT)

    request = urllib.request.Request(
        base + '/chat/completions', data=body, headers=headers, method='POST'
    )
    if opener is None:
        opener = build_opener_for(
            urllib.parse.urlsplit(base).scheme.lower()
        )
    try:
        timeout = LOCAL_MODEL_REQUEST_TIMEOUT if provider == LOCAL_PROVIDER else MODEL_REQUEST_TIMEOUT
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ModelConnectionError(ERROR_TOO_LARGE_TEXT)
        value = json.loads(raw.decode('utf-8'))
        reply = value['choices'][0]['message']['content']
        if generation_count is not None:
            structured = json.loads(reply)
            items = structured.get('items') if isinstance(structured,dict) else None
            if (not isinstance(items,list) or len(items)!=generation_count
                    or not all(isinstance(item,str) and item.strip() for item in items)):
                raise ValueError()
            reply = '\n'.join(item.strip() for item in items)
        if (not isinstance(reply, str) or not reply.strip()
                or len(reply) > MAX_REPLY_CHARS):
            raise ValueError()
        return reply.strip()
    except ModelConnectionError:
        raise
    except urllib.error.HTTPError as exc:
        code = exc.code
        try:
            exc.close()
        except Exception:
            pass
        if code in (401, 403):
            raise ModelConnectionError(_LEGACY_AUTH_MESSAGE) from None
        if code == 503 and provider == SCHOOL_PROVIDER:
            raise ModelConnectionError(
                _LEGACY_SCHOOL_LOGIN_MESSAGE
            ) from None
        if code == 503:
            raise ModelConnectionError(ERROR_HTTP_TEXT) from None
        if code == 429:
            raise ModelConnectionError(_LEGACY_BUSY_MESSAGE) from None
        raise ModelConnectionError(
            _LEGACY_HTTP_STATUS.format(code=code)
        ) from None
    except TimeoutError:
        raise
    except urllib.error.URLError as exc:
        if _is_tls_failure(exc):
            raise ConnectionError(ERROR_TLS_FAILURE_TEXT) from None
        raise ConnectionError(ERROR_CONNECTION_TEXT) from None
    except OSError:
        raise ConnectionError(ERROR_CONNECTION_TEXT) from None
    except (ValueError, KeyError, IndexError, TypeError, UnicodeError):
        raise ModelConnectionError(ERROR_RESPONSE_TEXT) from None
    except Exception:
        raise ModelConnectionError(ERROR_RESPONSE_TEXT) from None


def _fetch_model_ids(base, api_key, opener, timeout):
    headers = {'Accept': 'application/json'}
    headers.update(_auth_headers(api_key))
    request = urllib.request.Request(
        base + '/models', headers=headers, method='GET'
    )
    with opener.open(request, timeout=timeout) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ModelConnectionError(ERROR_TOO_LARGE_TEXT)
    value = json.loads(raw.decode('utf-8'))
    data = value.get('data') if isinstance(value, dict) else None
    if not isinstance(data, list):
        raise ModelConnectionError(ERROR_MODELS_FORMAT_TEXT)
    found = []
    seen = set()
    for item in data:
        ident = item.get('id') if isinstance(item, dict) else None
        if not isinstance(ident, str):
            continue
        ident = ident.strip()
        if (not ident or len(ident) > MAX_MODEL_ID_LENGTH or ident in seen):
            continue
        seen.add(ident)
        found.append(ident)
        if len(found) >= MAX_MODEL_IDS:
            break
    if not found:
        raise ModelConnectionError(ERROR_MODELS_EMPTY_TEXT)
    return found


def test_connection(provider, endpoint, model, api_key='', *, opener=None):
    """Test a selection without applying it or sending a chat request.

    Only GET requests are used (school /health; others /models).
    The result is ``(ok, fixed_message)``;
    no server body, endpoint, key, model list, profile, or chat content is
    returned.  A successful generic HTTPS test says that the model list was
    read, never that a model call succeeded.
    """
    try:
        snapshot = prepare_snapshot(provider, endpoint, model, api_key)
        if provider == GEMINI_PROVIDER and not snapshot.api_key:
            return False, ERROR_GEMINI_KEY_TEXT
    except ModelConnectionError as exc:
        return False, fixed_message(exc, ERROR_TEST_FAILED_TEXT)
    try:
        if opener is None:
            opener = build_opener_for(
                urllib.parse.urlsplit(snapshot.endpoint).scheme.lower()
            )
        if provider == SCHOOL_PROVIDER:
            health_url = snapshot.endpoint.removesuffix('/v1') + '/health'
            request = urllib.request.Request(health_url, method='GET')
            with opener.open(request, timeout=MODEL_TEST_TIMEOUT) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                return False, ERROR_TOO_LARGE_TEXT
            health = json.loads(raw.decode('utf-8'))
            if not isinstance(health, dict):
                return False, ERROR_MODELS_FORMAT_TEXT
            if health.get('upstream_auth') in ('expired', 'missing', 'invalid'):
                return False, ERROR_SCHOOL_AUTH_TEXT
            if health.get('status') != 'ok' or health.get('upstream_auth') != 'valid':
                return False, ERROR_SCHOOL_UNAVAILABLE_TEXT
        else:
            _fetch_model_ids(
                snapshot.endpoint, snapshot.api_key, opener, MODEL_TEST_TIMEOUT
            )
        return True, TEST_SUCCESS_TEXT[provider]
    except ModelConnectionError as exc:
        return False, fixed_message(exc, ERROR_TEST_FAILED_TEXT)
    except urllib.error.HTTPError as exc:
        code = exc.code
        try:
            exc.close()
        except Exception:
            pass
        if code in (401, 403):
            return False, (
                ERROR_SCHOOL_AUTH_TEXT if provider == SCHOOL_PROVIDER
                else ERROR_AUTH_TEXT
            )
        if code == 503:
            if provider == SCHOOL_PROVIDER:
                return False, ERROR_SCHOOL_UNAVAILABLE_TEXT
            if provider == LOCAL_PROVIDER:
                return False, ERROR_LOCAL_UNAVAILABLE_TEXT
            return False, ERROR_REMOTE_UNAVAILABLE_TEXT
        if code == 429:
            return False, ERROR_BUSY_TEXT
        return False, ERROR_HTTP_TEXT
    except TimeoutError:
        return False, ERROR_TIMEOUT_TEXT
    except urllib.error.URLError as exc:
        if _is_tls_failure(exc):
            return False, ERROR_TLS_FAILURE_TEXT
        return False, (
            ERROR_LOCAL_ABSENT_TEXT if provider in (
                SCHOOL_PROVIDER, LOCAL_PROVIDER
            ) else ERROR_REMOTE_CONNECTION_TEXT
        )
    except ssl.SSLError:
        return False, ERROR_TLS_FAILURE_TEXT
    except OSError:
        return False, (
            ERROR_LOCAL_ABSENT_TEXT if provider in (
                SCHOOL_PROVIDER, LOCAL_PROVIDER
            ) else ERROR_REMOTE_CONNECTION_TEXT
        )
    except (ValueError, KeyError, IndexError, TypeError, UnicodeError):
        return False, ERROR_MODELS_FORMAT_TEXT
    except Exception:
        return False, ERROR_TEST_FAILED_TEXT


def test_reply(provider, endpoint, model, api_key=''):
    """Explicit synthetic inference test; never reads a QQ/profile/history file."""
    try:
        snapshot = prepare_snapshot(provider, endpoint, model, api_key)
        request_reply(snapshot.endpoint, snapshot.model, [
            {'role': 'user', 'content': '请只回复：连接成功'}
        ], snapshot.api_key, provider=snapshot.provider)
        return True, '实际模型回复成功；只发送了界面注明的固定测试语，未发送聊天记录。'
    except ModelConnectionError as exc:
        return False, fixed_message(exc, ERROR_HTTP_TEXT)
    except TimeoutError:
        return False, ERROR_TIMEOUT_TEXT
    except Exception:
        return False, ERROR_CONNECTION_TEXT
