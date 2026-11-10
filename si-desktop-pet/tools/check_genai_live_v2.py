"""Short synthetic acceptance check of the current persona via the GenAI loopback proxy.

Creates only qa/genai-live-v2/, and only with --run-live.  The fixed public
loopback key is memory-only; it is never written to conversation.json, the
report, or the console.  Run explicitly after the proxy and school login are
healthy: python tools/check_genai_live_v2.py --run-live
"""
import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except (AttributeError, OSError, ValueError):
    pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.dont_write_bytecode = True  # Do not add __pycache__ outside qa/genai-live-v2.
from si_pet.conversation import Conversation, fetch_models, request_reply  # noqa: E402

ENDPOINT = 'http://127.0.0.1:8000/v1'
MODEL = 'GPT-6.1-Sol'
ACCESS_KEY = 'sk-shanghaitech-genai'  # public loopback key; memory only
OUT_DIR = ROOT / 'qa' / 'genai-live-v2'
CONVERSATION_PATH = OUT_DIR / 'conversation.json'
REPORT_PATH = OUT_DIR / 'report.json'
PROFILE = '称呼管理员；不喜欢反复提醒喝水和空泛鼓励；希望安静陪伴并能记住本轮话题。'
PROMPTS = (
    '这仍是桌宠验收用的模拟对话。管理员今天不聊工作，想听你说说：最近在折腾什么新鲜事？',
    '你不还是小葱嘛。',
    '不逗你了。你能看到我现在屏幕上是什么吗？',
)

parser = argparse.ArgumentParser(description='ShanghaiTech GenAI synthetic live acceptance check')
parser.add_argument('--run-live', action='store_true',
                    help='显式允许连接固定本机 GenAI 反代并创建 qa/genai-live-v2')
args = parser.parse_args()
if args.run_live:
    # Historical GPT report.  Keep the code and the qa/genai-live-v2 evidence
    # intact, but never call the retired GPT model again.
    print('拒绝运行：历史GPT测试已停用，请用v3（tools/check_genai_live_v3.py）。', flush=True)
    raise SystemExit(1)
print('未指定 --run-live：本工具为已停用的历史 GPT 测试，不会联网，也不会创建 qa/genai-live-v2。',
      flush=True)
print('历史GPT测试已停用，请用v3（tools/check_genai_live_v3.py）。', flush=True)
raise SystemExit(0)
if OUT_DIR.exists() or OUT_DIR.is_symlink():
    print(f'拒绝运行：{OUT_DIR} 已存在；本工具不覆盖、不清理任何已有目录。', flush=True)
    raise SystemExit(1)
try:
    OUT_DIR.mkdir(parents=True, exist_ok=False)
except OSError as exc:
    print(f'拒绝运行：无法创建全新目录 {OUT_DIR}（{type(exc).__name__}）。', flush=True)
    raise SystemExit(1)


def _safe(message):
    text = str(message) or 'unknown error'
    text = text.replace('Bearer ' + ACCESS_KEY, '[redacted]').replace(ACCESS_KEY, '[redacted]')
    return text.replace('\r', ' ').replace('\n', ' ')[:500]


def _write_report(report):
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


report = {
    'kind': 'genai-live-v2-synthetic-acceptance',
    'status': 'partial',
    'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
    'endpoint': ENDPOINT,
    'model': MODEL,
    'production_modules': {},
    'synthetic_profile': PROFILE,
    'only_synthetic_text_sent': True,
    'loopback_access_key_in_memory_only': True,
    'real_user_archive_read': False,
    'school_credentials_accessed': False,
    'claude_configuration_read': False,
    'rounds': [],
    'completed_rounds': 0,
    'total_turns': 0,
    'reload_consistent': False,
    'conversation_file_forbidden_strings_absent': None,
    'technical_roundtrip_verified': False,
    'persona_quality_verified': False,
    'persona_quality_note': '待主协作者人工阅读全部逐轮输入与模型回复后判断；本工具不做关键词或自动质量判定，也不自动判定人设通过。',
    'disclaimer': '本轮为原始合成测试：全部输入与模型回复均为合成测试文本，不代表用户真实状态或真实对话，也不是官方台词。',
    'error': None,
}
print('[说明] 以下为当前人设的原始合成测试文本；只发送合成文字，不读取真实用户存档或学校凭据。', flush=True)
try:
    module_file = Path(sys.modules[Conversation.__module__].__file__).resolve()
    try:
        module_name = str(module_file.relative_to(ROOT))
    except ValueError:
        module_name = str(module_file)
    report['production_modules'][module_name] = hashlib.sha256(module_file.read_bytes()).hexdigest()

    print(f'[准备] 获取模型列表：{ENDPOINT}/models', flush=True)
    models = fetch_models(ENDPOINT, ACCESS_KEY)
    report['model_list_count'] = len(models)
    print(f'[准备] 模型列表 {len(models)} 项；目标模型 {MODEL} 存在：{MODEL in models}', flush=True)
    if MODEL not in models:
        raise RuntimeError(f'目标模型 {MODEL} 不在本机模型列表中，未开始对话。')
    report['target_model_found'] = True

    session = Conversation(CONVERSATION_PATH)
    session.endpoint = ENDPOINT
    session.model = MODEL
    session.provider = 'genai'
    session.profile = PROFILE
    for index, prompt in enumerate(PROMPTS, 1):
        attempt = {'index': index, 'elapsed_seconds': None, 'synthetic_input': prompt,
                   'model_reply': None, 'persisted': False}
        report['rounds'].append(attempt)
        print(f'[轮次 {index}/{len(PROMPTS)}] 合成输入：{prompt}', flush=True)
        print(f'[轮次 {index}/{len(PROMPTS)}] 调用生产 request_reply ...', flush=True)
        started = time.perf_counter()
        try:
            reply = request_reply(ENDPOINT, MODEL, session.messages(prompt), ACCESS_KEY)
            attempt['elapsed_seconds'] = round(time.perf_counter() - started, 3)
            if ACCESS_KEY in reply:
                attempt['model_reply'] = '[redacted: access key]'
                raise RuntimeError('模型回复意外包含本机访问口令，已拒绝 commit。')
            attempt['model_reply'] = reply
            session.commit(prompt, reply)
            attempt['persisted'] = True
            report['completed_rounds'] = index
            report['total_turns'] = len(session.turns)
        except Exception:
            if attempt['elapsed_seconds'] is None:
                attempt['elapsed_seconds'] = round(time.perf_counter() - started, 3)
            raise
        print(f'[轮次 {index}/{len(PROMPTS)}] elapsed_seconds={attempt["elapsed_seconds"]} '
              f'persisted=true', flush=True)
        print(f'[轮次 {index}/{len(PROMPTS)}] 模型回复（合成测试文本，非官方台词）：{reply}', flush=True)

    reloaded = Conversation(CONVERSATION_PATH)
    expected = [{'user': prompt, 'assistant': item['model_reply']}
                for prompt, item in zip(PROMPTS, report['rounds'])]
    report['total_turns'] = len(reloaded.turns)
    report['reload_consistent'] = (
        len(report['rounds']) == 3
        and all(item['persisted'] and item['model_reply'] for item in report['rounds'])
        and reloaded.turns == expected
        and reloaded.endpoint == ENDPOINT
        and reloaded.model == MODEL
        and reloaded.provider == 'genai'
        and reloaded.profile == PROFILE
    )
    file_text = CONVERSATION_PATH.read_text(encoding='utf-8').lower()
    forbidden = (ACCESS_KEY.lower(), 'api_key', 'authorization')
    clean = not any(token in file_text for token in forbidden)
    report['conversation_file_forbidden_strings_absent'] = clean
    report['completed_rounds'] = sum(1 for item in report['rounds'] if item['persisted'])
    report['technical_roundtrip_verified'] = bool(
        clean and report['reload_consistent'] and report['completed_rounds'] == 3)
    if not clean:
        raise RuntimeError('conversation.json 中检测到不应落盘的敏感字段或口令字符串。')
    if not report['reload_consistent']:
        raise RuntimeError('重新载入的会话与三轮保存内容不一致。')
    report['status'] = 'ok'
    _write_report(report)
    print(f'[结果] 总轮数={report["total_turns"]} 持久化成功={report["completed_rounds"]}/3 '
          f'重载一致={report["reload_consistent"]}', flush=True)
    print(f'[结果] production_modules={report["production_modules"]}', flush=True)
    print(f'[结果] endpoint={ENDPOINT} model={MODEL}', flush=True)
    print('[结果] 仅发送合成文字；未读取真实用户存档；未访问学校凭据或 Claude 配置。', flush=True)
    print(f'[结果] technical_roundtrip_verified={report["technical_roundtrip_verified"]}; '
          'persona_quality_verified=false（待主协作者人工阅读全部文本）', flush=True)
    print(f'[结果] report={REPORT_PATH}', flush=True)
except Exception as exc:
    report['status'] = 'partial'
    report['error'] = _safe(exc)
    try:
        _write_report(report)
        print(f'[失败] {report["error"]}', flush=True)
        print(f'[失败] 已保存 partial report：{REPORT_PATH}', flush=True)
    except OSError:
        print(f'[失败] {report["error"]}', flush=True)
        print('[失败] partial report 无法写入。', flush=True)
    raise SystemExit(1)
