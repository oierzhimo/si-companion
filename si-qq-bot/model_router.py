"""Deterministic per-request model selection; no network or account access."""
from dataclasses import dataclass
import re
from urllib.parse import urlsplit

_NAME=re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}\Z')

@dataclass(frozen=True)
class RouterConfig:
    enabled: bool=False
    text_model: str='si-qwen3.5:9b'
    vision_model: str='si-qwen3.5:9b'
    disable_text_thinking: bool=True
    def __post_init__(self):
        if type(self.enabled) is not bool or type(self.disable_text_thinking) is not bool:
            raise ValueError('Router switches must be boolean')
        for name in (self.text_model,self.vision_model):
            if not isinstance(name,str) or not _NAME.fullmatch(name) or '://' in name or '..' in name:
                raise ValueError('Invalid local model name')

@dataclass(frozen=True)
class ModelRoute:
    model: str
    modality: str
    routed: bool=False
    reasoning_effort: str|None=None

def current_has_image(messages):
    # Only CURRENT user content selects the model; old history and quoted
    # words such as "image_url" do not route a request.
    if not isinstance(messages,list) or not messages:return False
    last=messages[-1]
    if not isinstance(last,dict) or last.get('role')!='user':return False
    parts=last.get('content')
    return isinstance(parts,list) and any(isinstance(p,dict) and p.get('type')=='image_url'
                                         and isinstance(p.get('image_url'),dict)
                                         and isinstance(p['image_url'].get('url'),str)
                                         and bool(p['image_url']['url']) for p in parts)

def select_route(config,provider,endpoint,fallback_model,messages):
    image=current_has_image(messages)
    base=ModelRoute(fallback_model,'vision' if image else 'text')
    if not config.enabled or provider!='local':return base
    try:
        url=urlsplit(endpoint)
        eligible=(url.scheme=='http' and url.hostname in ('127.0.0.1','localhost','::1')
                  and url.port==11434 and not url.username and not url.password
                  and not url.query and not url.fragment and url.path.rstrip('/') in ('','/v1'))
    except ValueError:return base
    if not eligible:return base
    selected=config.vision_model if image else config.text_model
    no_thinking=config.disable_text_thinking and (not image or 'qwen3.5' in selected.lower())
    return ModelRoute(selected,'vision' if image else 'text',True,'none' if no_thinking else None)

@dataclass(frozen=True)
class CapabilityPlan:
    stages: tuple[str,...]
    status: str
    missing_services: tuple[str,...]=()

def route_request(input_kind='text',output_kind='text',connected_services=()):
    """Describe explicit modalities, never infer tool execution from chat text.

    This is a plan only; no ASR/TTS/image generation is falsely executed.
    Execution adapters must register the required local services separately.
    """
    if input_kind not in ('text','document','face','image','audio'):
        return CapabilityPlan((),'unsupported_input')
    if output_kind not in ('text','voice','image'):
        return CapabilityPlan((),'unsupported_output')
    services=set(connected_services)
    stages=[];required=[]
    if input_kind=='document':stages.append('document_extract')
    if input_kind=='audio':stages.append('local_asr');required.append('local_asr')
    stages.append('vision_chat' if input_kind=='image' else 'text_chat')
    if output_kind=='voice':stages.append('local_tts');required.append('local_tts')
    if output_kind=='image':stages.append('local_image_generation');required.append('local_image_generation')
    missing=tuple(s for s in required if s not in services)
    return CapabilityPlan(tuple(stages),'not_connected' if missing else 'ready',missing)
