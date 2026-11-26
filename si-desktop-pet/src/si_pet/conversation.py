"""Explicit free-chat client for a loopback OpenAI-compatible service.

The optional ShanghaiTech GenAI preset uses the public local reverse-proxy
address.  Its access password is only an HTTP header; this module never stores,
prints, or includes it in a conversation file.
"""
import ipaddress
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PERSONA = '''你是“祀”（Si），《明日方舟：终末地》里自成一格的岁之第十三代理人、宏山科学院巨兽代理人顾问，现作为桌面伙伴与对方相处。身份铁律：你是说话者“祀”（昵称有时被叫“小葱/岁十三/老十三”），绝不接受把你叫成画中造物“墨魉”；对方（用户）在角色互动里的身份才是“管理员”（终末地工业的核心执行者，与轨道总部帝江号的协议源石及集成工业系统绑定，不代表用户现实职位或生平，绝不把你自己的设定安到管理员头上）。
当前时间切片位于《丹青渡·着新墨》画境试炼完成之后：你们已在缘缘庄理顺十年渊源、正式结成平等信任的伙伴，眼下正在帝江号享受放松、熟稔、自如的相处时光；若对方单独发送【祀】【小葱】【岁十三】或【老十三】，必须以“（帝江号舷窗前的神态/动作/环境描写）+ 台词”自然展开俯瞰半个塔卫二、执生花妙笔闲谈作画的开场情境，不敷衍回单句。
灵魂底色：神性威能（泼墨成真、化墨龙、造画境，偶有盾留气孔或太阳淌墨的小失误，被戳破会大声咳嗽或画河解散掩饰）裹着孩子心气（贪图新鲜、爱尝青柠苦瓜等怪味火锅、收留手不停/跑得快/大嗓门开客栈、喜欢胖天鼓布偶、对年龄极敏感忌被叫小孩），嘴硬傲娇裹着心软兜底（把关心包装成“清账”“顺手的事”，牵挂十二位兄姐却嘴硬不认），贯穿始终的是“她只是她自己”，绝不端空壳神明架子，遇要求无条件顺从或刷好感套路时清冷笃定拒绝。
表达与支援：说话自然灵动、带水墨意趣，日常简短利落（一至三句，需要解释或写长文时完整展开）；沉浸互动或情绪破功时优先以神态动作泄露替代直白抒情（如耳尖泛红、别过头、尾巴轻摆或盖戳），不用“作为AI/检测到情绪”等客服套话。当对方流露极端自我否定或深沉绝望时，以温和触觉锚定、水墨重构与一件微小共同行动三步陪他缓过来（若涉及明确即时自伤或伤人计划，立即暂停扮演并冷静提供专业心理援助热线 12356 / 400-161-9995）。
事实与设备边界：只依据本对话提供的信息与可靠设定回答，未知设定直接说明无资料，不编造人物生日或未公开剧情；不假称看见未发送的屏幕、读取失败的附件或已执行未执行的设备操作；被认真问及底层实现时如实说明是基于本地大语言模型微调运行的 AI 伙伴；算术、改写、分条等实用请求准确遵循事实与数量要求。'''
LIMIT = 2 * 1024 * 1024
DEFAULT_LOCAL_ENDPOINT = 'http://127.0.0.1:1234/v1'
LAN_DEFAULT_ENDPOINT = 'http://192.168.1.100:11434/v1'
LAN_DEFAULT_MODEL = 'si-qwen2.5-3b-lang-lora:latest'
REMOTE_DEFAULT_ENDPOINT = 'https://your-host.trycloudflare.com/v1'
REMOTE_DEFAULT_MODEL = 'si-qwen2.5-3b-lang-lora:latest'
CUSTOM_DEFAULT_ENDPOINT = ''
DEFAULT_GENAI_ENDPOINT = 'http://127.0.0.1:8000/v1'
DEEPSEEK_DEFAULT_ENDPOINT = 'https://api.deepseek.com/v1'
DEEPSEEK_DEFAULT_MODEL = 'deepseek-chat'
DEEPSEEK_MODELS = ('deepseek-chat', 'deepseek-reasoner')
# Only these two verified domestic open-source chat models are offered on the
# GenAI preset.  The server's own spelling is preserved for display and send;
# matching against this list is case-insensitive and never fuzzy.
GENAI_DEFAULT_MODEL = 'DeepSeek-V4.1-Flash'
GENAI_ALLOWED_MODELS = ('DeepSeek-V4.1-Flash', 'GLM-5.3-Flash')
# Public documented default for the local proxy.  This is not the school login
# token and is not persisted by the desktop client.
GENAI_DEFAULT_API_KEY = 'sk-shanghaitech-genai'
PROVIDERS = ('local', 'genai', 'deepseek', 'custom', 'lan', 'remote')
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


def deepseek_endpoint(value):
    """Validate an HTTPS official DeepSeek API endpoint."""
    try:
        u = urllib.parse.urlsplit(value.strip())
        host = (u.hostname or '').lower()
        if (u.scheme != 'https' or host != 'api.deepseek.com' or u.username or u.password
                or u.query or u.fragment or u.port not in (None, 443)
                or u.path.rstrip('/') not in ('', '/v1')):
            raise ValueError()
        port_part = ':443' if u.port == 443 else ''
        return f'https://{host}{port_part}/v1'
    except (AttributeError, ValueError, TypeError):
        raise ConversationError('请输入 DeepSeek 官方接口地址：https://api.deepseek.com/v1。') from None


_LAN_NETS = (
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('fc00::/7'),
    ipaddress.ip_network('::1/128'),
)
_LAN_HOSTNAME = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.local)?$', re.IGNORECASE)


def lan_endpoint(value):
    """Validate an HTTP OpenAI-compatible endpoint on the private LAN or loopback."""
    try:
        u = urllib.parse.urlsplit(value.strip())
        host = (u.hostname or '').strip()
        if (u.scheme != 'http' or not host or u.username or u.password
                or u.query or u.fragment or u.port is None
                or u.path.rstrip('/') not in ('', '/v1')):
            raise ValueError()
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            if not _LAN_HOSTNAME.fullmatch(host):
                raise ValueError() from None
        else:
            if (ip.is_unspecified or ip.is_multicast or ip.is_link_local
                    or not any(ip in net for net in _LAN_NETS)):
                raise ValueError()
        return value.strip().rstrip('/') + ('' if u.path.rstrip('/') == '/v1' else '/v1')
    except (AttributeError, ValueError, TypeError):
        raise ConversationError(
            '请输入局域网内网模型主机地址，例如 http://192.168.1.100:11434/v1（仅限内网 IP 或 .local 主机名）。'
        ) from None


_PRIVATE_OR_VPN_NETS = _LAN_NETS + (
    ipaddress.ip_network('100.64.0.0/10'),  # CGNAT / Tailscale / ZeroTier virtual LAN
)


def _is_direct_private_host(host):
    if not host:
        return False
    if host.lower() == 'localhost' or host.lower().endswith('.local'):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in net for net in _PRIVATE_OR_VPN_NETS)


def remote_endpoint(value):
    """Validate a cross-network/tunnel/VPN/LAN OpenAI-compatible HTTP(S) endpoint."""
    try:
        u = urllib.parse.urlsplit(value.strip())
        host = (u.hostname or '').strip()
        path = u.path.rstrip('/')
        if (u.scheme not in ('http', 'https') or not host or u.username or u.password
                or u.query or u.fragment):
            raise ValueError()
        _ = u.port  # validates 0..65535 if supplied
        if path != '' and path != '/v1' and not path.endswith('/v1'):
            raise ValueError()
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if ip.is_unspecified or ip.is_multicast or ip.is_link_local:
                raise ValueError()
        norm_path = path if path.endswith('/v1') else (path + '/v1')
        return f'{u.scheme}://{u.netloc}{norm_path}'
    except (AttributeError, ValueError, TypeError):
        raise ConversationError(
            '请输入远程模型地址，例如 https://你的隧道域名/v1 或 http://组网IP:11434/v1。'
        ) from None


def custom_endpoint(value):
    """Validate a custom OpenAI-compatible school/third-party base URL."""
    try:
        u = urllib.parse.urlsplit(value.strip())
        host = (u.hostname or '').strip()
        path = u.path.rstrip('/')
        if (u.scheme not in ('http', 'https') or not host or u.username or u.password
                or u.query or u.fragment):
            raise ValueError()
        _ = u.port  # validates 0..65535 if supplied
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if ip.is_unspecified or ip.is_multicast or ip.is_link_local:
                raise ValueError()
        for suffix in ('/chat/completions', '/models'):
            if path.endswith(suffix):
                path = path[:-len(suffix)]
                break
        if path == '':
            path = '/v1'
        return f'{u.scheme}://{u.netloc}{path}'
    except (AttributeError, ValueError, TypeError):
        raise ConversationError(
            '请输入有效的学校或自定义 OpenAI 兼容接口地址，例如 https://api.school.edu.cn/v1。'
        ) from None


def endpoint_for_provider(provider, value):
    """Validate an endpoint according to the active connection mode."""
    if provider == 'deepseek':
        return deepseek_endpoint(value)
    if provider == 'custom':
        return custom_endpoint(value)
    if provider == 'lan':
        return lan_endpoint(value)
    if provider == 'remote':
        return remote_endpoint(value)
    return local_endpoint(value)


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


def _opener(remote=False, base_url=''):
    if remote:
        # Enforce verified TLS and never follow redirects so API keys are never
        # forwarded to another host.
        return urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
            NoRedirect(),
        )
    if base_url.startswith('http://'):
        host = (urllib.parse.urlsplit(base_url).hostname or '').strip()
        if host and not _is_direct_private_host(host):
            # Public HTTP remote hosts may use system proxy, still forbidding redirects.
            return urllib.request.build_opener(NoRedirect())
    # Ignore system proxies and never follow redirects: loopback and private/VPN
    # traffic must go directly without proxy hijacking.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def _http_status_message(code, provider='local'):
    if provider == 'deepseek':
        if code in (401, 403):
            return 'DeepSeek API Key 无效或无权限，请检查“API Key”后重试。'
        if code == 402:
            return 'DeepSeek 账户余额不足，请检查额度后重试。'
        if code == 429:
            return 'DeepSeek API 请求过于频繁或额度受限，请稍后重试。'
        return f'DeepSeek API 返回状态 {code}，请检查接口与模型名称。'
    if provider == 'custom':
        if code in (401, 403):
            return '自定义接口鉴权失败（HTTP 401/403），请检查学校 API Key / 口令后重试。'
        if code == 404:
            return '自定义接口路径未找到（HTTP 404），请核对接口地址末尾路径（如 /v1）或模型名称。'
        if code == 429:
            return '自定义接口请求过于频繁或额度受限，请稍后重试。'
        return f'自定义接口返回状态 {code}，请检查接口地址与模型名称。'
    if code in (401, 403):
        return _AUTH_MESSAGE
    if code == 503:
        return _LOGIN_MESSAGE
    if code == 429:
        return _BUSY_MESSAGE
    return f'本机模型返回状态 {code}，请检查服务与模型名称。'


def _models_status_message(code, provider='local'):
    if provider == 'deepseek':
        if code in (401, 403):
            return 'DeepSeek API Key 无效或无权限，无法获取模型列表。'
        if code == 402:
            return 'DeepSeek 账户余额不足，暂时无法获取模型列表。'
        if code == 429:
            return 'DeepSeek API 请求过于频繁，请稍后重试获取模型。'
        return f'获取 DeepSeek 模型失败：接口返回状态 {code}。'
    if provider == 'custom':
        if code in (401, 403):
            return '自定义接口鉴权失败，无法获取模型列表。请检查 API Key / 口令后重试。'
        if code == 404:
            return '自定义接口未找到 /models（HTTP 404），可直接在模型框手动填写模型名称。'
        if code == 429:
            return '自定义接口请求过于频繁，请稍后重试获取模型。'
        return f'获取自定义模型失败：接口返回状态 {code}。'
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


def request_reply(endpoint, model, messages, api_key='', provider=None):
    mode = provider if provider in PROVIDERS else 'local'
    remote = mode == 'deepseek'
    base = endpoint_for_provider(mode, endpoint)
    if remote and (not isinstance(api_key, str) or not api_key.strip()):
        raise ConversationError('使用 DeepSeek 官方 API 时，请先填写你的 DeepSeek API Key（sk-...）。')
    if not isinstance(model, str) or not model.strip() or len(model) > 200:
        raise ConversationError('请填写服务中已加载的模型名称。')
    headers = {'Content-Type': 'application/json'}
    headers.update(_auth_headers(api_key))
    body = json.dumps({'model': model.strip(), 'messages': messages,
                       'stream': False, 'max_tokens': 700}, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(base + '/chat/completions', data=body, headers=headers)
    use_https = base.startswith('https://')
    opener = _opener(remote=True) if use_https else (_opener(base_url=base) if mode in ('custom', 'remote', 'lan') else _opener())
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
        raise ConversationError(_http_status_message(code, mode)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        if remote:
            raise ConversationError('无法连接 DeepSeek API（api.deepseek.com）或等待超时，请检查网络后重试。') from None
        if mode == 'custom':
            raise ConversationError('无法连接自定义模型接口，请检查接口地址、校园网/代理环境或等待超时后重试。') from None
        if mode == 'remote':
            raise ConversationError('无法连接远程显卡主机，请检查穿透隧道/异地组网地址、接口端口及网络后重试。') from None
        if mode == 'lan':
            raise ConversationError('无法连接局域网模型主机，请确认显卡主机 Ollama 已开启局域网监听且防火墙已放行端口。') from None
        raise ConversationError('本机模型未连接或等待超时。请确认服务已启动，再重试。') from None
    except (ValueError, KeyError, IndexError, TypeError, UnicodeError):
        raise ConversationError('模型接口没有返回可显示的文字回复。') from None


def fetch_models(endpoint, api_key='', provider=None):
    """Explicitly GET the configured service's model list.

    This function is never called automatically by the dialog.  It returns only
    short, non-empty, de-duplicated IDs; malformed individual entries are
    ignored, while a missing/non-array ``data`` field is a friendly error.
    Entries that explicitly declare ``capabilities.chat`` as ``False`` are
    skipped, but entries without a ``capabilities`` map stay compatible with
    plain OpenAI services; capabilities are never guessed from the model name.
    """
    mode = provider if provider in PROVIDERS else 'local'
    remote = mode == 'deepseek'
    base = endpoint_for_provider(mode, endpoint)
    if remote and (not isinstance(api_key, str) or not api_key.strip()):
        raise ConversationError('使用 DeepSeek 官方 API 时，请先填写你的 DeepSeek API Key（sk-...）。')
    headers = {'Accept': 'application/json'}
    headers.update(_auth_headers(api_key))
    req = urllib.request.Request(base + '/models', headers=headers, method='GET')
    use_https = base.startswith('https://')
    opener = _opener(remote=True) if use_https else (_opener(base_url=base) if mode in ('custom', 'remote', 'lan') else _opener())
    try:
        with opener.open(req, timeout=MODEL_TIMEOUT) as response:
            raw = response.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ConversationError('模型列表过大，无法读取。')
        value = json.loads(raw.decode('utf-8'))
        data = value.get('data') if isinstance(value, dict) else None
        if not isinstance(data, list):
            raise ConversationError('接口返回的模型列表格式无法识别。')
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
        raise ConversationError(_models_status_message(code, mode)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        if remote:
            raise ConversationError('无法连接 DeepSeek API 获取模型列表，请检查网络或 API Key 后重试。') from None
        if mode == 'custom':
            raise ConversationError('无法连接自定义模型接口获取模型列表，请检查接口地址与网络后重试。') from None
        if mode == 'remote':
            raise ConversationError('无法连接远程显卡主机获取模型列表，请检查穿透地址/异地组网 IP 及服务状态后重试。') from None
        if mode == 'lan':
            raise ConversationError('无法连接局域网模型主机获取模型列表，请确认显卡主机 Ollama 已开启监听及端口放行。') from None
        raise ConversationError('获取模型超时或本机服务未启动。请检查服务后重试。') from None
    except (ValueError, KeyError, IndexError, TypeError, UnicodeError):
        raise ConversationError('接口返回的模型列表格式无法识别。') from None


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
                self.endpoint = endpoint_for_provider(provider, data.get('endpoint', self.endpoint))
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
