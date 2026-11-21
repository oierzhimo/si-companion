"""Execute explicit local media requests before consulting a text model."""
from pathlib import Path
import re
import outbound_images
import voice_reply
from image_prompt import compile_prompt
from contextlib import nullcontext


def _resources(requester, stage):
    method = getattr(type(requester), 'local_resource_context', None)
    return method(requester, stage) if callable(method) else nullcontext()


def _generate(prompt, requester, args):
    translated = compile_prompt(prompt, requester, args)
    with _resources(requester, 'image'):
        segment = outbound_images.generate_segment(translated, checkpoint=outbound_images.select_checkpoint(prompt))
    from image_review import review_image
    review = review_image(prompt, segment, requester, args)
    return segment if review.matches else None


_REACTION_SYNONYMS = {
    '高兴': '开心', '偷笑': '开心', '得意': '开心', '笑': '开心', '微笑': '开心', '愉快': '开心',
    '困': '困了', '打盹': '困了', '累了': '困了', '睡觉': '困了', '晚安': '困了',
    '好': '收到', '好的': '收到', '明白': '收到', 'ok': '收到', '点头': '收到', '没问题': '收到',
    '哼': '生气', '气呼呼': '生气', '不满': '生气', '傲娇': '生气', '鼓腮': '生气',
}


def _reactions_dir(data_dir):
    candidate = Path(data_dir) / 'reactions'
    if (candidate / 'manifest.json').is_file():
        return candidate
    return Path(__file__).parent / 'reactions'


def _parse_model_media(answer, data_dir):
    """Extract optional inline [语音] and [表情:名称] cues and || bubble breaks."""
    want_voice = bool(re.search(r'[【\[]语音[】\]]', answer))
    clean = re.sub(r'[【\[]语音[】\]]', '', answer)
    reaction_matches = re.findall(r'[【\[]表情[：:]([^\]】\n]{1,16})[】\]]', clean)
    clean = re.sub(r'[【\[]表情[：:][^\]】\n]{1,16}[】\]]', '', clean)
    bubbles = tuple(part.strip() for part in re.split(r'\s*\|\|\s*', clean) if part.strip())
    clean_text = '\n'.join(bubbles) if bubbles else clean.strip()

    extra = []
    if reaction_matches:
        rdir = _reactions_dir(data_dir)
        for name in reaction_matches[:1]:
            key = name.strip()
            mapped = _REACTION_SYNONYMS.get(key.lower(), key)
            for candidate in (key, mapped):
                try:
                    seg = outbound_images.reaction_segment(rdir, candidate)
                    extra.append(seg)
                    break
                except Exception:
                    pass
            if extra:
                break
    return clean_text, bubbles, want_voice, tuple(extra)


class ServiceReply(str):
    def __new__(cls, text, segment=None, extra_segments=(), bubbles=()):
        value = super().__new__(cls, text)
        value.segment = segment
        value.extra_segments = tuple(extra_segments)
        value.bubbles = tuple(bubbles)
        return value


def respond(requester, args, text, data_dir, *, private=False):
    request = outbound_images.request_kind(text)
    if request:
        kind, prompt = request
        try:
            segment = (_generate(prompt, requester, args) if kind == 'generate'
                       else outbound_images.reaction_segment(_reactions_dir(data_dir), prompt))
            if segment is None:
                return ServiceReply('画面核对没有通过，这张先不发给你。')
            return ServiceReply('画好了。' if kind == 'generate' else '给你。', segment)
        except Exception:
            return ServiceReply('这次没画出来，本机绘图服务暂时没完成。' if kind == 'generate'
                                else '这个表情还没收进本机表情库，暂时发不了。')
    spoken = re.fullmatch(r'(?:/朗读\s+|(?:请)?(?:用语音说|语音说|念一下|读一下)[：:\s]*)(.{1,500})', text.strip())
    voice_requested = bool(re.search(r'(?:用|通过|以)语音(?:回复|回答|说|讲)|(?:发|来|回)(?:我|给我)?(?:一|个|条|段)?语音|语音(?:回复|回答)', text))
    if text.strip() == '/语音':
        answer = '管理员，我在。现在可以听到我说话吗？'
        enabled = True
        explicit_voice = True
    elif text.strip() == '/文字':
        return ServiceReply('这一条用文字回复你。')
    elif spoken:
        answer = spoken.group(1)
        enabled = True
        explicit_voice = True
    else:
        answer = requester(*args)
        enabled = voice_requested
        explicit_voice = voice_requested
        if private:
            import reply_settings
            try:
                enabled = enabled or reply_settings.read_mode(data_dir)[1] == 'voice'
            except (OSError, ValueError):
                pass
    if not isinstance(answer, str) or not answer.strip():
        return answer
    clean_text, bubbles, tag_voice, extra_segments = _parse_model_media(answer, data_dir)
    if not clean_text:
        clean_text = '管理员，我在听。'
        bubbles = (clean_text,)
    if enabled:
        with _resources(requester, 'voice'):
            prepared = voice_reply.prepare_reply(clean_text, enabled=True)
        if prepared.voice_ready:
            return ServiceReply(clean_text, prepared.segments[0], extra_segments=extra_segments)
        if spoken or voice_requested or text.strip() == '/语音':
            return ServiceReply('这次语音没合成出来，先把文字给你：'+clean_text)
    if tag_voice and not explicit_voice and len(clean_text) <= voice_reply.MAX_TEXT:
        try:
            if voice_reply.check_service(timeout=0.4) == voice_reply.SERVICE_AVAILABLE:
                with _resources(requester, 'voice'):
                    prepared = voice_reply.prepare_reply(clean_text, enabled=True)
                if prepared.voice_ready:
                    extra_segments = (*extra_segments, prepared.segments[0])
        except Exception:
            pass
    if extra_segments or len(bubbles) > 1:
        return ServiceReply(clean_text, None, extra_segments=extra_segments, bubbles=bubbles)
    return clean_text
