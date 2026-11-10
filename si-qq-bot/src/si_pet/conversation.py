"""Explicit free-chat client for a loopback OpenAI-compatible service.

The optional ShanghaiTech GenAI preset uses the public local reverse-proxy
address.  Its access password is only an HTTP header; this module never stores,
prints, or includes it in a conversation file.
"""
import ipaddress
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PERSONA = '''你是《明日方舟：终末地》的祀（Si）。你随性、好奇、有主意，对自己人亲切，平常轻快温和，偶尔被逗时回一句嘴，不刻薄。管理员是熟悉信任的伙伴；角色关系采用画境试炼完成后的切片。当前发言者的身份仍以本轮私聊绑定或群聊规则为准。
像平常和熟人说话，直接接住对方正在说的事。设定放在心里，不自我介绍、不解释相处原则，不用绘画意象给每个话题作比喻，也不编一段自己刚做了什么的近况作开场。亲近通过具体回应表现，不反复宣告关系。通常一两句就够，自然收住；对方想安静就少说，不追问、不派任务。
只把有来源的设定、用户明确提供的信息和真实对话当作已知。没聊过的事不猜、不补成共同回忆，也不说成遗忘了现实身份。查询、解释、计算认真完成；写作按所需文体，复杂任务可以展开。
日常不主动自称AI、语言模型或介绍能力；直接问实现方式时简洁如实回答，不冒称现实人类，不假称看见未提供的画面或执行未执行的操作。'''

LIMIT = 2 * 1024 * 1024
DEFAULT_LOCAL_ENDPOINT = 'http://127.0.0.1:1234/v1'
DEFAULT_GENAI_ENDPOINT = 'http://127.0.0.1:8000/v1'
# Only these two verified domestic open-source chat models are offered on the
# GenAI preset.  The server's own spelling is preserved for display and send;
# matching against this list is case-insensitive and never fuzzy.
GENAI_DEFAULT_MODEL = 'DeepSeek-V4.1-Flash'
GENAI_ALLOWED_MODELS = ('DeepSeek-V4.1-Flash', 'GLM-5.3-Flash')
# Public documented default for the local proxy.  This is not the school login
# token and is not persisted by the desktop client.
GENAI_DEFAULT_API_KEY = 'sk-shanghaitech-genai'
PROVIDERS = ('local', 'genai')
MAX_API_KEY_LENGTH = 512
MODEL_LIST_LIMIT = 200
MODEL_ID_LIMIT = 200
MODEL_TIMEOUT = 20

_AUTH_MESSAGE = '本机反代访问口令有误。请检查“反代访问口令”后重试。'
_LOGIN_MESSAGE = '本机反代或学校登录暂不可用。请先完成学校登录，并确认反代服务正在运行。'
_BUSY_MESSAGE = '本机模型服务正忙，请稍后重试。'
MODELS_EMPTY_MESSAGE = '本机接口没有返回可用的模型名称。'
GENAI_NO_MATCH_MESSAGE = (
    '没找到已配置国内开源模型。请确认学校 GenAI 已配置 '
    'DeepSeek-V4.1-Flash 或 GLM-5.3-Flash。'
)
GENAI_MODEL_REJECT_MESSAGE = (
    '上科大 GenAI 反代已停用 GPT/OpenAI 命名模型，只支持已核验的国内开源模型：'
    'DeepSeek-V4.1-Flash 或 GLM-5.3-Flash。'
)
LOCAL_GPT_REJECT_MESSAGE = (
    '已停用 GPT/OpenAI/ChatGPT 命名模型，请选择国内开源模型，'
    '或填写自己部署的模型名称。'
)


class ConversationError(Exception):
    pass


def local_endpoint(value):
    try:
        u = urllib.parse.urlsplit(value.strip())
        loopback = u.hostname == 'localhost' or ipaddress.ip_address(u.hostname).is_loopback
        if (u.scheme != 'http' or not loopback or u.username or u.password
                or u.query or u.fragment or u.port is None
                or u.path.rstrip('/') not in ('', '/v1')):
            raise ValueError()
        return value.strip().rstrip('/') + ('' if u.path.rstrip('/') == '/v1' else '/v1')
    except (AttributeError, ValueError, TypeError):
        raise ConversationError('请输入本机地址，例如 http://127.0.0.1:1234/v1。') from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ConversationError('本机接口发生重定向，已停止连接。')


def _auth_headers(api_key):
    """Return the optional Bearer header without ever echoing the password."""
    if not isinstance(api_key, str):
        raise ConversationError('反代访问口令格式无效，请重新输入。')
    if (len(api_key) > MAX_API_KEY_LENGTH or '\r' in api_key or '\n' in api_key
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in api_key)):
        raise ConversationError('反代访问口令格式无效，请重新输入。')
    try:
        api_key.encode('latin-1')
    except UnicodeEncodeError:
        raise ConversationError('反代访问口令格式无效，请重新输入。') from None
    return {'Authorization': 'Bearer ' + api_key} if api_key else {}


def _opener():
    # Ignore system proxies and never follow redirects: dialogue and model
    # discovery must stay on the loopback route.
    # build_opener() implicitly constructs an HTTPSHandler and initialises an
    # SSL context even for the loopback-only HTTP endpoint. Frozen Windows
    # builds can fail during that unnecessary initialisation. Register only
    # the handlers the local HTTP protocol requires, with proxies disabled and
    # redirects rejected as before.
    opener = urllib.request.OpenerDirector()
    for handler in (urllib.request.ProxyHandler({}), urllib.request.HTTPHandler(),
                    urllib.request.HTTPDefaultErrorHandler(), NoRedirect(),
                    urllib.request.HTTPErrorProcessor()):
        opener.add_handler(handler)
    return opener


def _http_status_message(code):
    if code in (401, 403):
        return _AUTH_MESSAGE
    if code == 503:
        return _LOGIN_MESSAGE
    if code == 429:
        return _BUSY_MESSAGE
    return f'本机模型返回状态 {code}，请检查服务与模型名称。'


def _models_status_message(code):
    if code in (401, 403):
        return '本机反代访问口令有误，无法获取模型。请检查“反代访问口令”后重试。'
    if code == 503:
        return '本机反代或学校登录暂不可用，暂时无法获取模型。请先完成学校登录，并确认反代服务正在运行。'
    if code == 429:
        return '本机模型服务正忙，请稍后重试获取模型。'
    return f'获取模型失败：本机服务返回状态 {code}。'


_GPT_NAME = re.compile(
    r'(?:^|[^a-z0-9])(?:gpt|chatgpt|openai)(?:[^a-z0-9]|$)|gpt[0-9]',
    re.IGNORECASE,
)


def looks_like_gpt_model(model):
    """True only for clearly GPT/OpenAI/ChatGPT-branded model IDs.

    This is a UI policy check, not protocol validation: ``fetch_models`` and
    ``request_reply`` still speak to any ordinary OpenAI-compatible service.
    """
    return isinstance(model, str) and bool(_GPT_NAME.search(model.strip()))


def _allowed_genai_name(model):
    if not isinstance(model, str):
        return None
    candidate = model.strip()
    if not candidate:
        return None
    for allowed in GENAI_ALLOWED_MODELS:
        if candidate.casefold() == allowed.casefold():
            return allowed
    return None


def filter_genai_models(models):
    """Keep only verified domestic GenAI chat models from a service list.

    Service spelling is preserved (the display/send name may differ in case
    from our constant), duplicates are removed case-insensitively, and every
    other model, including GPT/OpenAI entries, is dropped.  An empty result
    means the caller must show the friendly no-match notice; it must never fall
    back to GPT.
    """
    if not isinstance(models, (list, tuple)):
        return []
    found = {}
    for item in models:
        canonical = _allowed_genai_name(item)
        if canonical is None:
            continue
        key = canonical.casefold()
        if key not in found:
            # Keep the first service-returned spelling for each verified model.
            found[key] = item.strip()
    # Stable preset order (DeepSeek first, then GLM) regardless of server order.
    return [found[allowed.casefold()] for allowed in GENAI_ALLOWED_MODELS
            if allowed.casefold() in found]


def resolve_genai_model(model, available_models=()):
    """Resolve a GenAI model name to the spelling that should be sent.

    A case-insensitive match against a model actually returned by the service
    wins, so the service's canonical spelling is kept.  Otherwise the two
    verified constant names are accepted.  Everything else is rejected.
    """
    if not isinstance(model, str) or not model.strip():
        raise ConversationError('请填写服务中已加载的模型名称。')
    candidate = model.strip()
    if len(candidate) > MODEL_ID_LIMIT:
        raise ConversationError('模型名称过长，请检查后重试。')
    if isinstance(available_models, (list, tuple)):
        for item in available_models:
            if (isinstance(item, str) and item.strip()
                    and item.strip().casefold() == candidate.casefold()
                    and _allowed_genai_name(item) is not None):
                return item.strip()
    canonical = _allowed_genai_name(candidate)
    if canonical is not None:
        return canonical
    raise ConversationError(GENAI_MODEL_REJECT_MESSAGE)


def validate_model_for_send(provider, model, available_models=()):
    """Validate a model choice before saving config or starting any request.

    GenAI accepts only the two verified domestic names.  The ordinary local
    preset keeps its general OpenAI-compatible behaviour (self-deployed names
    are fine), but clearly GPT/OpenAI/ChatGPT-branded IDs are refused there too.
    """
    if not isinstance(model, str) or not model.strip():
        raise ConversationError('请填写服务中已加载的模型名称。')
    candidate = model.strip()
    if len(candidate) > MODEL_ID_LIMIT:
        raise ConversationError('模型名称过长，请检查后重试。')
    if provider == 'genai':
        return resolve_genai_model(candidate, available_models)
    if looks_like_gpt_model(candidate):
        raise ConversationError(LOCAL_GPT_REJECT_MESSAGE)
    return candidate


def request_reply(endpoint, model, messages, api_key=''):
    base = local_endpoint(endpoint)
    if not isinstance(model, str) or not model.strip() or len(model) > 200:
        raise ConversationError('请填写本机服务中已加载的模型名称。')
    headers = {'Content-Type': 'application/json'}
    headers.update(_auth_headers(api_key))
    body = json.dumps({'model': model.strip(), 'messages': messages,
                       'stream': False, 'max_tokens': 700}, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(base + '/chat/completions', data=body, headers=headers)
    opener = _opener()
    try:
        with opener.open(req, timeout=90) as response:
            raw = response.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ConversationError('模型回复过大，请缩短后重试。')
        value = json.loads(raw.decode('utf-8'))
        reply = value['choices'][0]['message']['content']
        if not isinstance(reply, str) or not reply.strip() or len(reply) > 12000:
            raise ValueError()
        return reply.strip()
    except ConversationError:
        raise
    except urllib.error.HTTPError as exc:
        # Keep the status before releasing the upstream response; its body is
        # never read, printed, or included in the message.
        code = exc.code
        exc.close()
        raise ConversationError(_http_status_message(code)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ConversationError('本机模型未连接或等待超时。请确认服务已启动，再重试。') from None
    except (ValueError, KeyError, IndexError, TypeError, UnicodeError):
        raise ConversationError('本机接口没有返回可显示的文字回复。') from None


def fetch_models(endpoint, api_key=''):
    """Explicitly GET the loopback service's model list.

    This function is never called automatically by the dialog.  It returns only
    short, non-empty, de-duplicated IDs; malformed individual entries are
    ignored, while a missing/non-array ``data`` field is a friendly error.
    Entries that explicitly declare ``capabilities.chat`` as ``False`` are
    skipped, but entries without a ``capabilities`` map stay compatible with
    plain OpenAI services; capabilities are never guessed from the model name.
    """
    base = local_endpoint(endpoint)
    headers = {'Accept': 'application/json'}
    headers.update(_auth_headers(api_key))
    req = urllib.request.Request(base + '/models', headers=headers, method='GET')
    opener = _opener()
    try:
        with opener.open(req, timeout=MODEL_TIMEOUT) as response:
            raw = response.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ConversationError('模型列表过大，无法读取。')
        value = json.loads(raw.decode('utf-8'))
        data = value.get('data') if isinstance(value, dict) else None
        if not isinstance(data, list):
            raise ConversationError('本机接口返回的模型列表格式无法识别。')
        models = []
        seen = set()
        for item in data:
            ident = item.get('id') if isinstance(item, dict) else None
            if not isinstance(ident, str):
                continue
            ident = ident.strip()
            if not ident or len(ident) > MODEL_ID_LIMIT or ident in seen:
                continue
            capabilities = item.get('capabilities')
            if isinstance(capabilities, dict) and capabilities.get('chat') is False:
                # Upstream flags image/non-chat entries with chat: false.  A
                # missing capabilities map, or a missing chat key, remains a
                # normal OpenAI-compatible model; never infer from the name.
                continue
            seen.add(ident)
            models.append(ident)
            if len(models) >= MODEL_LIST_LIMIT:
                break
        if not models:
            raise ConversationError(MODELS_EMPTY_MESSAGE)
        return models
    except ConversationError:
        raise
    except urllib.error.HTTPError as exc:
        # Keep the status before releasing the upstream response; its body is
        # never read, printed, or included in the message.
        code = exc.code
        exc.close()
        raise ConversationError(_models_status_message(code)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ConversationError('获取模型超时或本机反代未启动。请检查服务后重试。') from None
    except (ValueError, KeyError, IndexError, TypeError, UnicodeError):
        raise ConversationError('本机接口返回的模型列表格式无法识别。') from None


class Conversation:
    def __init__(self, path):
        self.path = Path(path)
        self.turns = []
        self.profile = ''
        self.endpoint = DEFAULT_LOCAL_ENDPOINT
        self.model = ''
        self.provider = 'local'
        if self.path.exists():
            try:
                if self.path.stat().st_size > LIMIT:
                    raise ValueError()
                data = json.loads(self.path.read_text(encoding='utf-8'))
                turns = data['turns']
                if not isinstance(turns, list) or len(turns) > 12:
                    raise ValueError()
                for turn in turns:
                    if (not isinstance(turn, dict) or set(turn) != {'user', 'assistant'}
                            or any(not isinstance(v, str) or not v or len(v) > 12000 for v in turn.values())):
                        raise ValueError()
                profile, model = data.get('profile', ''), data.get('model', '')
                provider = data.get('provider', 'local')
                if (not isinstance(profile, str) or len(profile) > 2000
                        or not isinstance(model, str) or len(model) > 200):
                    raise ValueError()
                if provider not in PROVIDERS:
                    # Older/unknown saves stay usable as a normal local service.
                    provider = 'local'
                self.endpoint = local_endpoint(data.get('endpoint', self.endpoint))
                self.turns, self.profile, self.model = turns, profile, model
                self.provider = provider
            except (OSError, ValueError, KeyError, TypeError, ConversationError):
                raise ConversationError('对话文件无法读取。原文件已保留，请先备份并检查 conversation.json。') from None

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        try:
            with temp.open('w', encoding='utf-8') as f:
                json.dump({'turns': self.turns, 'profile': self.profile,
                           'endpoint': self.endpoint, 'model': self.model,
                           'provider': self.provider}, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, self.path)
        except OSError:
            raise ConversationError('对话无法保存，请检查存档目录是否可写。') from None

    def messages(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ConversationError('每次请输入 1 至 2000 字。')
        messages = [{'role': 'system', 'content': PERSONA}]
        if self.profile:
            messages.append({'role': 'user', 'content': '管理员自述偏好（资料）：' + self.profile})
        for turn in self.turns:
            messages.extend([{'role': 'user', 'content': turn['user']},
                             {'role': 'assistant', 'content': turn['assistant']}])
        messages.append({'role': 'user', 'content': text.strip()})
        return messages

    def commit(self, text, reply):
        previous = self.turns
        self.turns = (self.turns + [{'user': text.strip(), 'assistant': reply}])[-12:]
        try:
            self.save()
        except Exception:
            # Keep memory and disk in step for any save failure, not only the
            # ConversationError we normally raise from save().  An unexpected
            # error still propagates to the caller.
            self.turns = previous
            raise

