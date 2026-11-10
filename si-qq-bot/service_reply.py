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


class ServiceReply(str):
    def __new__(cls, text, segment=None):
        value = super().__new__(cls, text)
        value.segment = segment
        return value


def respond(requester, args, text, data_dir, *, private=False):
    request = outbound_images.request_kind(text)
    if request:
        kind, prompt = request
        try:
            segment = (_generate(prompt, requester, args) if kind == 'generate'
                       else outbound_images.reaction_segment((Path(data_dir)/'reactions') if (Path(data_dir)/'reactions/manifest.json').is_file() else Path(__file__).parent/'reactions', prompt))
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
    elif text.strip() == '/文字':
        return ServiceReply('这一条用文字回复你。')
    elif spoken:
        answer = spoken.group(1)
        enabled = True
    else:
        answer = requester(*args)
        enabled = voice_requested
        if private:
            import reply_settings
            try:
                enabled = enabled or reply_settings.read_mode(data_dir)[1] == 'voice'
            except (OSError, ValueError):
                pass
    if not isinstance(answer, str) or not answer.strip():
        return answer
    if enabled:
        answer = re.sub(r'[【\[]语音[】\]]', '', answer).strip()
        if not answer:
            answer = '管理员，我在听。'
        with _resources(requester, 'voice'):
            prepared = voice_reply.prepare_reply(answer, enabled=True)
        if prepared.voice_ready:
            return ServiceReply(answer, prepared.segments[0])
        if spoken or voice_requested or text.strip() == '/语音':
            return ServiceReply('这次语音没合成出来，先把文字给你：'+answer)
    return answer
