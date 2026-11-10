"""Package the 2.0.0a9 Windows desktop pet preview without replacing prior releases.

This tool only prepares new archives from an existing ``dist-2.0a9`` build.  It
does not run PyInstaller, does not contact the GenAI proxy, and never claims
that a real model conversation was verified.  A successful run proves ZIP/SHA
packaging and source boundaries only.  The source archive excludes the local
``art-source`` working material while leaving it untouched on disk.

The 2.0.0a9 package is a local preview: the new full-body artwork and complete
action set are not delivered, and multi-PC plus HarmonyOS 5 real-device
acceptance is still outstanding.  The experimental ``genai_service.py`` is not
wired into the UI; this tool never describes an automatic or one-click proxy
start.  Historical GPT reports are not evidence for this release.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile

from make_release import ROOT, RELEASE

VERSION = '2.0.0a9'
NAME = f'SiDesktopPet-{VERSION}-Windows-x64'
SOURCE_NAME = f'SiDesktopPet-{VERSION}-Source'
BUILT = ROOT / 'dist-2.0a9' / 'SiDesktopPet'
MOBILE_LINES = (ROOT / 'mobile/android/app/src/main/assets/lines.json',
                ROOT / 'mobile/harmony/entry/src/main/resources/rawfile/lines.json')

# Build outputs, local QA data, virtual environments, external tools and any
# versioned build/dist folder are excluded generically, not just through 2a8.
EXCLUDED_DIRS = {
    'build', 'dist', 'release', 'qa', 'data', '__pycache__', '.git', '.venv',
    'venv', 'env', '.tools', '.build-tmp', '.pip-cache', 'tmp', 'portable-check',
    'acl-recovery', '.claude', 'node_modules', '.mypy_cache', '.pytest_cache',
    'genai-proxy', 'genai_proxy', 'external-tools', 'external_tools',
    # Local art/screenshot retouching candidates stay on this machine; they are
    # unfinished working material and must not enter the source release.
    'art-source',
}
EXCLUDED_PREFIXES = ('build-', 'dist-')
EXCLUDED_FILES = {'qa-settings.json', 'credentials.txt', '.env', '.claude'}
AUDIO_SUFFIXES = {'.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac', '.wma', '.opus'}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def is_link(path):
    """Refuse symlinks and Windows junctions, including external-tool links."""
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def source_excluded(rel):
    """Return True for source-tree paths that must never enter the archive."""
    for part in Path(rel).parts:
        lower = part.lower()
        if lower in EXCLUDED_DIRS:
            return True
        if lower.startswith(EXCLUDED_PREFIXES):
            return True
        if lower == '.env' or lower.startswith('.env.'):
            return True
        if lower == '.claude' or lower.startswith('.claude.'):
            return True
    name = Path(rel).name.lower()
    if name in EXCLUDED_FILES:
        return True
    if name.startswith('qa-') and name.endswith('.json'):
        return True
    if name.startswith('token') and name.endswith('.txt'):
        return True
    return Path(name).suffix.lower() in AUDIO_SUFFIXES


def sensitive_reason(rel):
    """Return a reason when a path is sensitive or points at an external tool."""
    for part in Path(rel).parts:
        lower = part.lower()
        if lower == '.env' or lower.startswith('.env.'):
            return 'environment file'
        if lower == '.claude' or lower.startswith('.claude.'):
            return 'Claude configuration'
        if lower == 'credentials.txt':
            return 'credentials file'
        if lower.startswith('token') and lower.endswith('.txt'):
            return 'token file'
        if lower in {'genai-proxy', 'genai_proxy'}:
            return 'external GenAI proxy'
    return None


def packaged_excluded_reason(rel):
    """Return a reason when a packaged tree contains build/QA leftovers."""
    for part in Path(rel).parts:
        lower = part.lower()
        if lower in {'qa', 'release', '.git', '.venv', 'venv', '.tools', '.build-tmp',
                     '.pip-cache', '__pycache__', 'acl-recovery', 'portable-check',
                     'node_modules'}:
            return 'build/QA path'
        if lower in {'build', 'dist'} or lower.startswith(EXCLUDED_PREFIXES):
            return 'build output path'
    name = Path(rel).name.lower()
    if name.startswith('qa-') and name.endswith('.json'):
        return 'QA state file'
    return None


def walk_source():
    """Yield (relative path, absolute path) with symlinks and escapes refused."""
    root = ROOT.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        rel_base = base.relative_to(root)
        kept = []
        for dirname in sorted(dirnames):
            rel = rel_base / dirname
            if source_excluded(rel):
                continue
            path = base / dirname
            if is_link(path):
                raise SystemExit(f'Refusing source link: {path}')
            kept.append(dirname)
        dirnames[:] = kept
        for filename in sorted(filenames):
            rel = rel_base / filename
            if source_excluded(rel):
                continue
            path = base / filename
            if is_link(path):
                raise SystemExit(f'Refusing source link: {path}')
            if not path.is_file():
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                raise SystemExit(f'Refusing path outside the project: {path}')
            yield rel, path


def safe_member_name(raw, target):
    """Validate a ZIP member using its raw name before any normalization."""
    if not isinstance(raw, str) or not raw or '\x00' in raw:
        raise SystemExit('ZIP contains an invalid member name.')
    normalized = raw.replace('\\', '/')
    if normalized.startswith('/') or normalized.startswith('//'):
        raise SystemExit(f'ZIP contains an absolute path: {raw!r}')
    if len(normalized) >= 2 and normalized[1] == ':':
        raise SystemExit(f'ZIP contains a drive-qualified path: {raw!r}')
    parts = normalized.split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise SystemExit(f'ZIP contains an unsafe path: {raw!r}')
    if any(':' in part or part.endswith((' ', '.')) for part in parts):
        raise SystemExit(f'ZIP contains a Windows-ambiguous path: {raw!r}')
    candidate = (target / normalized).resolve()
    if not candidate.is_relative_to(target.resolve()):
        raise SystemExit(f'ZIP member escapes the extraction directory: {raw!r}')
    return normalized


def assert_tree_safe(folder):
    root = folder.resolve()
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        base = Path(dirpath)
        for dirname in dirnames:
            if is_link(base / dirname):
                raise SystemExit(f'Refusing packaged link: {base / dirname}')
        for filename in filenames:
            path = base / filename
            if is_link(path):
                raise SystemExit(f'Refusing packaged link: {path}')
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                raise SystemExit(f'Refusing packaged path outside the folder: {path}')
            rel = resolved.relative_to(root)
            reason = sensitive_reason(rel) or packaged_excluded_reason(rel)
            if reason:
                raise SystemExit(f'Refusing {reason} in package: {rel}')


def zip_tree(folder, destination):
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                rel = path.relative_to(folder).as_posix()
                archive.write(path, f'{NAME}/{rel}')


def zip_source(destination):
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for rel, path in walk_source():
            archive.write(path, f'{SOURCE_NAME}/{rel.as_posix()}')


def validate_zip(path, prefix, source=False):
    """Validate raw ZIP names, boundaries and the exact expected file set."""
    target = Path(path).parent
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad:
            raise SystemExit(f'Corrupt ZIP entry in {path.name}: {bad}')
        expected_root = ROOT if source else target
        if source:
            expected = {f'{prefix}/{rel.as_posix()}': size
                        for rel, size in ((rel, p.stat().st_size) for rel, p in walk_source())}
        else:
            expected = {f'{prefix}/{p.relative_to(target / prefix).as_posix()}': p.stat().st_size
                        for p in sorted((target / prefix).rglob('*')) if p.is_file()}
        actual = {}
        seen = set()
        for info in archive.infolist():
            raw = info.orig_filename
            clean = safe_member_name(raw, expected_root)
            # ``filename`` is the normalized value; validate it too so neither
            # representation can smuggle a traversal or absolute path.
            safe_member_name(info.filename, expected_root)
            if not clean.startswith(prefix + '/'):
                raise SystemExit(f'Unexpected top-level entry in {path.name}: {raw!r}')
            rel = clean[len(prefix) + 1:]
            if not rel or clean.endswith('/'):
                raise SystemExit(f'Unexpected directory entry in {path.name}: {raw!r}')
            lower = clean.lower()
            if lower in seen:
                raise SystemExit(f'Duplicate ZIP entry in {path.name}: {raw!r}')
            seen.add(lower)
            reason = sensitive_reason(Path(rel))
            if reason:
                raise SystemExit(f'{path.name} contains {reason}: {rel}')
            if source:
                if source_excluded(Path(rel)):
                    raise SystemExit(f'{path.name} contains an excluded path: {rel}')
                if Path(rel).suffix.lower() in AUDIO_SUFFIXES:
                    raise SystemExit(f'{path.name} contains listening audio: {rel}')
            actual[clean] = info.file_size
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        if missing:
            raise SystemExit(f'{path.name} is missing: {missing[:5]}')
        if extra:
            raise SystemExit(f'{path.name} has unexpected entries: {extra[:5]}')
        changed = sorted(name for name, size in actual.items() if expected.get(name) != size)
        if changed:
            raise SystemExit(f'{path.name} has size mismatches: {changed[:5]}')
        return {'entries': len(actual), 'integrity': True, 'raw_names_safe': True,
                'exact_source_tree_match': True}


def snapshot_archives():
    if not RELEASE.exists():
        return {}
    return {p.name: digest(p) for p in sorted(RELEASE.iterdir())
            if p.is_file() and p.suffix.lower() in {'.zip', '.apk'}}


def genai_source_summary():
    conversation = (ROOT / 'src/si_pet/conversation.py').read_text(encoding='utf-8')
    ui = (ROOT / 'src/si_pet/conversation_ui.py').read_text(encoding='utf-8')
    checks = {
        'conversation_fetch_models': 'def fetch_models' in conversation,
        'conversation_request_reply': 'def request_reply' in conversation,
        'optional_bearer_header': ('Authorization' in conversation and 'Bearer ' in conversation),
        'provider_field': 'provider' in conversation,
        'genai_endpoint': 'http://127.0.0.1:8000/v1' in conversation,
        'public_loopback_key_present': 'sk-shanghaitech-genai' in conversation,
        'conversation_dialog': 'class ConversationDialog' in ui,
        'genai_ui_preset': '上科大 GenAI 反代' in ui and 'genai' in ui,
        'new_conversation_default_genai': (bool(re.search(
            r"session\.provider\s*=\s*['\"]genai['\"]", ui))
            and 'DEFAULT_GENAI_ENDPOINT' in ui),
        'genai_default_model': bool(re.search(
            r"GENAI_DEFAULT_MODEL\s*=\s*['\"]DeepSeek-V4\.1-Flash['\"]", conversation)),
        'genai_allowed_models': ('DeepSeek-V4.1-Flash' in conversation
                                 and 'GLM-5.3-Flash' in conversation),
        'genai_gpt_rejected': ('def looks_like_gpt_model' in conversation
                               and 'def filter_genai_models' in conversation
                               and 'def validate_model_for_send' in conversation),
        'existing_save_preserved': 'had_saved_config' in ui,
        'explicit_fetch_models': 'def fetch_models' in ui,
    }
    if not all(checks.values()):
        missing = sorted(name for name, ok in checks.items() if not ok)
        raise SystemExit(f'GenAI client features are missing from source: {missing}')
    return {
        'checks': checks,
        'proves_source_contains_feature_only': True,
        'does_not_prove_real_model_available': True,
    }


def companion_source_summary():
    """Confirm the 2.0.0a9 companion/voice changes exist in the packaged source."""
    ui = (ROOT / 'src/si_pet/conversation_ui.py').read_text(encoding='utf-8')
    voice = (ROOT / 'src/si_pet/voice.py').read_text(encoding='utf-8')
    checks = {
        'quiet_command_closed_list': ('QUIET_COMMANDS' in ui
                                      and "'安静陪我'" in ui
                                      and "'先安静陪我'" in ui),
        'quiet_exact_command_only': 'def is_quiet_command' in ui,
        'quiet_companion_button': "self.quiet_button = QPushButton('安静陪我')" in ui,
        'quiet_abandons_late_reply': 'def abandon_pending_reply' in ui,
        'late_reply_generation_guard': ('self.active_generation' in ui
                                        and 'self.generation' in ui),
        'voice_health_async_thread': ('_probe_health' in voice
                                      and 'SiVoiceStudioHealth' in voice),
        'voice_health_queued_signal': ('_health_ready' in voice
                                       and 'QueuedConnection' in voice),
        'genai_service_not_wired_into_ui': 'genai_service' not in ui,
    }
    if not all(checks.values()):
        missing = sorted(name for name, ok in checks.items() if not ok)
        raise SystemExit(f'Companion/voice features are missing from source: {missing}')
    return {
        'checks': checks,
        'proves_source_contains_feature_only': True,
        'does_not_prove_runtime_or_ui_acceptance': True,
        'genai_service_experimental_not_wired': True,
    }


def dialogue_summary():
    if len(LINES) != 11 or any(len(lines) != 6 for lines in LINES.values()):
        raise SystemExit('Expected 11 occasions with 6 lines each; update the release notes.')
    for path in MOBILE_LINES:
        data = json.loads(path.read_text(encoding='utf-8'))
        if list(data) != list(LINES) or any(data[kind] != lines for kind, lines in LINES.items()):
            raise SystemExit(f'Mobile dialogue table differs from Windows: {path}')
    return {'occasions': len(LINES), 'lines': sum(len(lines) for lines in LINES.values()),
            'mobile_tables_synced': True}


def main():
    if __version__ != VERSION:
        raise SystemExit('Source version changed; choose a new release name.')
    dialogue = dialogue_summary()
    genai = genai_source_summary()
    companion = companion_source_summary()
    if not (BUILT / 'Si-DesktopPet.exe').is_file():
        raise SystemExit('Build dist-2.0a9/SiDesktopPet first; this tool never runs PyInstaller.')

    RELEASE.mkdir(exist_ok=True)
    portable = RELEASE / f'{NAME}.zip'
    source = RELEASE / f'SiDesktopPet-{VERSION}-Source.zip'
    sha_file = RELEASE / f'SHA256SUMS-{VERSION}.json'
    if portable.exists() or source.exists() or sha_file.exists():
        raise SystemExit('A 2.0.0a9 release file already exists; bump the version instead of overwriting it.')
    previous = snapshot_archives()
    created_portable = False
    created_source = False
    created_sha = False
    temporary_sha = None

    try:
        with tempfile.TemporaryDirectory(dir=RELEASE, prefix='.release-2a9-') as directory:
            temporary = Path(directory)
            folder = temporary / NAME
            shutil.copytree(BUILT, folder, ignore=shutil.ignore_patterns('data', '__pycache__'))
            assert_tree_safe(folder)
            for name in ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md'):
                shutil.copy2(ROOT / name, folder / name)
            shutil.copytree(ROOT / 'docs', folder / 'docs')
            shutil.copytree(ROOT / 'licenses', folder / 'licenses')
            assert_tree_safe(folder)
            (folder / 'data').mkdir()
            (folder / 'data' / 'README.txt').write_text(
                '用户存档保存在本目录。升级前先备份整个旧 data 目录；'
                '完全退出旧版后，再按你的选择把需要的旧 data 内容迁移到新版本；保留旧文件夹。',
                encoding='utf-8')
            (folder / 'START_HERE.txt').write_text(
                '祀桌宠 2.0.0a9 Windows 本地预览版\n\n'
                '这是 Windows 本地预览版，不是正式交付版。只有在主协作者完成真实检查并更新发布指针后，才作为正式交付。\n'
                '完整解压本文件夹后，双击 Si-DesktopPet.exe。第一次会打开陪伴面板。\n'
                '退出：右键桌宠或系统托盘图标。找不到桌宠：从托盘选择“找回祀”。\n\n'
                '模型与本机反代\n'
                '- 自由聊聊的 GenAI 预设严格限定 DeepSeek-V4.1-Flash 与 GLM-5.3-Flash 两款，不提供 GPT；'
                '普通本机预设保留自部署模型名称的兼容性，按当前实现会拒绝名称中带 GPT/OpenAI/ChatGPT 的型号，'
                '但不会核验其他任意名称是否确属国产开源模型。\n'
                '- 本机反代必须由管理员按学校反代自身说明手动启动，并由使用者本人完成学校登录；'
                '确认 http://127.0.0.1:8000/v1 可用后，再在窗口内点击“获取模型”。\n'
                '- 本版没有自动启动反代的按钮；请先手动启动反代，再在窗口内操作。\n'
                '- 桌宠不会自动启动、自动登录或修改学校反代，也不包含反代程序、模型权重或学校凭据。\n'
                '- src/si_pet/genai_service.py 仍是未接入界面的实验代码；本版没有把它接入 UI，'
                '不能据此宣称反代可以自动启动。\n\n'
                '安静陪伴与语音\n'
                '- 对话窗口新增“安静陪我”按钮。只有完整、明确、肯定的“安静陪我”或“先安静陪我”命令'
                '才会触发本机安静陪伴；否定、疑问或含糊句子按普通聊天处理。\n'
                '- 进入安静陪伴后，之前仍在等待的迟到回复会作废，不再写入记录，也不会稍后说话。\n'
                '- 语音启动时的 VoiceStudio 健康检查已改为后台异步执行，不再阻塞界面；'
                '语音仍默认关闭，默认声线为 A。\n\n'
                '角色与内容边界\n'
                '- 角色和关系基线仍依据管理员确认的 111 框架 V1.2 资料；'
                '资料原文不随程序分发，其中的运行指令不作为本应用指令。\n'
                '- 尚未交付实机全身新图和完整动作；运行时仍使用旧动作图集。\n'
                '- 多 PC 与鸿蒙 5 实机验收尚未完成。当前只有单手机与单 PC 的 Android 同步部分实测，'
                '不能当作多 PC 或鸿蒙验收。\n\n'
                '验证边界（请勿混淆）\n'
                '- 源码 Qt 界面的真实聊天由主协作者独立运行 qa/check_genai_ui_live_v3.py'
                '（只发送合成测试文本，不读取真实用户对话或学校凭据），'
                '结果写入 qa/genai-ui-live-v3/report.json；'
                '六轮 DeepSeek 合成对话报告在 qa/genai-live-v3/report.json。\n'
                '- 打包 EXE 目前只验证启动、界面构造、本地渲染和可选的本机回环协同；'
                '没有在 EXE 内点击发送，也没有验证 EXE 的真实模型聊天。'
                '不能声称打包 EXE 已通过真实聊天验证。\n'
                '- 历史 GPT 测试只是旧记录，不能作为本版 DeepSeek/GLM 的新验证。\n'
                '- 离线测试记录：169 项通过，1 项符号链接测试跳过；'
                '这不等于真实模型、打包 EXE 聊天或多 PC/鸿蒙实机验收。\n\n'
                '升级与存档\n'
                '- 升级前先备份旧版整个 data 目录。\n'
                '- 先完全退出旧版，再按你的选择把需要的旧 data 内容迁移到新版本；保留旧文件夹。\n'
                '- 旧对话和偏好保留；打开窗口时不重写原设置文件。\n'
                '- 旧 GenAI 的 GPT/未核验型号在窗口内改为 DeepSeek；'
                '普通本机的 GPT 型号会清空；发送前各预设按当前实现拒绝 GPT/OpenAI/ChatGPT 命名，'
                '普通本机不核验其他名称是否确属国产开源模型。\n\n'
                '其他\n'
                '- 基础陪伴默认离线运行，不会自动同步；自由聊聊使用本机反代时，'
                '文字会经本机反代送到学校提供的模型。\n'
                '- 局域网配对与手动事件交换均为可选预览，需要时才双击 Start-Sync-Preview.cmd。'
                '请先阅读 MANUAL_SYNC_PREVIEW.txt。\n'
                '- Android 0.1.3 已安装，并完成单手机单 PC 的原生 TLS 部分实测；'
                '双 PC 与鸿蒙 5 仍未验收。\n',
                encoding='utf-8')
            (folder / 'Start-Sync-Preview.cmd').write_text(
                '@echo off\r\ncd /d "%~dp0"\r\nstart "" "Si-DesktopPet.exe" --sync-preview\r\n',
                encoding='ascii')
            (folder / 'MANUAL_SYNC_PREVIEW.txt').write_text(
                '可选：Windows 手动协同预览（2.0.0a9 本地预览版）\n\n'
                '先退出普通桌宠，再双击 Start-Sync-Preview.cmd。首次启用会备份旧存档并创建事件账本。\n'
                '在“相伴手记”页导出事件包，传到另一台 Windows PC 后导入；'
                '另一台也导出并传回本机导入，才能让两端取得相同事件。\n'
                '请保管事件包，其中有互动和手记。摘要只校验损坏，不做身份认证。\n'
                '不要复制另一台电脑的 settings.sync-v1.json；全新空白电脑可接收已有电脑的旧记录，'
                '旧存档已分叉时会拒绝自动合并。\n'
                '较早版本已生成的零值账本不会自动改写，请阅读 docs/LAN_SYNC_PREVIEW.md 的升级限制。\n'
                'Android 0.1.3 已安装，并完成单手机单 PC 的原生 TLS 部分实测；'
                '两台物理 PC、冻结 Windows 界面与旧存档组合、鸿蒙 5 平板仍未验收。\n'
                'GenAI 预设默认使用 DeepSeek-V4.1-Flash，备选 GLM-5.3-Flash；'
                '本机反代需管理员手动启动并自行完成学校登录，桌宠不会自动启动反代。\n'
                '打包 EXE 的协同检查只覆盖本机回环与 TLS 配对/交换，'
                '不等同于两台物理 PC 或鸿蒙 5 验收，也未验证 EXE 真实聊天。\n',
                encoding='utf-8')
            portable_tmp = temporary / portable.name
            source_tmp = temporary / source.name
            zip_tree(folder, portable_tmp)
            zip_source(source_tmp)
            portable_check = validate_zip(portable_tmp, NAME)
            source_check = validate_zip(source_tmp, SOURCE_NAME, source=True)
            if portable.exists() or source.exists() or sha_file.exists():
                raise SystemExit('A 2.0.0a9 release file appeared during packaging; refusing to overwrite.')
            os.replace(portable_tmp, portable)
            created_portable = True
            if source.exists() or sha_file.exists():
                raise SystemExit('A 2.0.0a9 release file appeared during packaging; refusing to overwrite.')
            os.replace(source_tmp, source)
            created_source = True

        current = snapshot_archives()
        changed = sorted(name for name, value in previous.items() if current.get(name) != value)
        if changed:
            raise SystemExit(f'Existing release archives changed during packaging: {changed}')
        report = {
            'version': VERSION,
            'status': 'packaged_pending_live_and_main_collaborator',
            'preview_release': True,
            'built_from': 'dist-2.0a9/SiDesktopPet',
            'dialogue': dialogue,
            'genai_source': genai,
            'companion_source': companion,
            'archives': {p.name: {'bytes': p.stat().st_size, 'sha256': digest(p)}
                         for p in (portable, source)},
            'sha256_file': sha_file.name,
            'source_boundaries': {
                'build_dist_qa_data_venv_external_excluded': source_check,
                'art_source_local_working_material_excluded': True,
                'portable_boundaries': portable_check,
                'sensitive_names_absent': True,
                'symlinks_rejected': True,
            },
            'previous_zip_apk_hashes_unchanged': True,
            'previous_zip_apk_count': len(previous),
            'previous_zip_apk_hashes': sorted(previous.values()),
            'genai_default_model': 'DeepSeek-V4.1-Flash',
            'genai_optional_model': 'GLM-5.3-Flash',
            'manual_start_required': True,
            'genai_service_wired_into_ui': not companion['checks'][
                'genai_service_not_wired_into_ui'],
            'live_genai_verified': False,
            'source_ui_real_send_verified': False,
            'frozen_executable_real_chat_tested': False,
            'offline_tests': {
                'note': 'Recorded by the main collaborator; this packaging tool runs no tests.',
                'passed': 169,
                'skipped_symlink': 1,
            },
            'not_delivered': [
                '实机全身新图',
                '完整动作',
                '多 PC 实机验收',
                '鸿蒙 5 实机验收',
                '打包 EXE 真实模型聊天验证',
            ],
            'live_genai_note': ('Packaging cannot prove real model availability. The main '
                                'collaborator must provide the current six-round DeepSeek '
                                'report (qa/genai-live-v3/report.json) and run the source Qt '
                                'UI real-send check (qa/genai-ui-live-v3/report.json). '
                                'Historical GPT v1/v2 reports are not accepted for this '
                                'release. The frozen UI real-chat path is still not exercised.'),
            'final_delivery_ready': False,
        }
        temporary_sha = sha_file.with_suffix('.json.tmp')
        temporary_sha.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        if sha_file.exists():
            raise SystemExit('The 2.0.0a9 SHA file appeared during packaging; refusing to overwrite.')
        os.replace(temporary_sha, sha_file)
        created_sha = True
        temporary_sha = None
    except BaseException:
        # Only remove files this run created; never touch prior releases.
        if temporary_sha is not None:
            try:
                temporary_sha.unlink()
            except OSError:
                pass
        for path, created in ((sha_file, created_sha), (source, created_source),
                              (portable, created_portable)):
            if created:
                try:
                    path.unlink()
                except OSError:
                    pass
        raise
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT / 'src'))
    from si_pet import __version__
    from si_pet.companion import LINES
    main()
