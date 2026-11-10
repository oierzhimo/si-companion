"""Package the 2.0.0a8 Windows desktop pet without replacing prior releases.

This tool only prepares new archives from an existing ``dist-2.0a8`` build.  It
does not run PyInstaller, does not contact the GenAI proxy, and never claims
that a real model conversation was verified.  A successful run proves ZIP/SHA
packaging and source boundaries only.  The source archive excludes the local
``art-source`` working material while leaving it untouched on disk.
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

VERSION = '2.0.0a8'
NAME = f'SiDesktopPet-{VERSION}-Windows-x64'
SOURCE_NAME = f'SiDesktopPet-{VERSION}-Source'
BUILT = ROOT / 'dist-2.0a8' / 'SiDesktopPet'
MOBILE_LINES = (ROOT / 'mobile/android/app/src/main/assets/lines.json',
                ROOT / 'mobile/harmony/entry/src/main/resources/rawfile/lines.json')

# Build outputs, local QA data, virtual environments, external tools and any
# versioned build/dist folder are excluded generically, not just through 2a7.
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
    if not (BUILT / 'Si-DesktopPet.exe').is_file():
        raise SystemExit('Build dist-2.0a8/SiDesktopPet first; this tool never runs PyInstaller.')

    RELEASE.mkdir(exist_ok=True)
    portable = RELEASE / f'{NAME}.zip'
    source = RELEASE / f'SiDesktopPet-{VERSION}-Source.zip'
    sha_file = RELEASE / f'SHA256SUMS-{VERSION}.json'
    if portable.exists() or source.exists() or sha_file.exists():
        raise SystemExit('A 2.0.0a8 release file already exists; bump the version instead of overwriting it.')
    previous = snapshot_archives()
    created_portable = False
    created_source = False
    created_sha = False
    temporary_sha = None

    try:
        with tempfile.TemporaryDirectory(dir=RELEASE, prefix='.release-2a8-') as directory:
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
                '用户存档保存在本目录。升级时先退出旧版，再把整个旧 data 目录复制到新版本；保留旧文件夹。',
                encoding='utf-8')
            (folder / 'START_HERE.txt').write_text(
                '祀桌宠 2.0.0a8 Windows 便携版（准备版）\n\n'
                '本包只有在主协作者完成真实检查并更新发布指针后才作为正式交付。\n'
                '完整解压本文件夹后，双击 Si-DesktopPet.exe。第一次会打开陪伴面板。\n'
                '退出：右键桌宠或系统托盘图标。找不到桌宠：从托盘选择“找回祀”。\n'
                '升级：先退出旧版，将旧文件夹整个 data 目录复制到新版；保留旧文件夹。\n'
                '自由聊聊新增“上科大 GenAI 反代”预设，当前本机地址为 http://127.0.0.1:8000/v1。\n'
                '新对话默认使用国内开源模型 DeepSeek-V4.1-Flash，可手动切换为 GLM-5.3-Flash；'
                '只列出这两个已核对官方 MIT 模型卡的模型，不再把任意 OpenAI 兼容模型或 GPT 作为选项。\n'
                '反代背后的实际模型权重、路由权重和运行环境由学校提供；'
                '本包不含模型权重或反代程序，也不是本机已下载大模型。\n'
                '学校登录由使用者本人完成；本包不含学校账号或登录凭据。\n'
                '桌宠不会自动登录学校账号，也不会自动启动或修改反代。\n'
                '窗口打开时不会自动获取模型、自动发送或发送空测试消息；'
                '请按需点击“获取模型”和“发送”。\n'
                '其他电脑必须自行部署并登录自己的本机反代。\n'
                '反代访问口令只保留在内存，不写入 conversation.json、日志或同步账本。\n'
                '旧对话和偏好保留。打开窗口时不重写原设置文件；'
                '旧 GenAI 的 GPT/未核验型号在窗口内改为 DeepSeek，普通本机的 GPT 型号会清空，发送前仍需通过国内型号检查。\n'
                '基础陪伴默认离线运行，不会自动同步；自由聊聊使用本机反代时，'
                '文字会经本机反代送到学校提供的模型。\n'
                '旧的 GPT QA 只是历史测试，不能作为新模型的通过证据；'
                '新版六轮 DeepSeek 验收和源码界面真实发送的实际结论，以独立发布核验报告为准。\n'
                '局域网配对与手动事件交换均为可选预览，需要时才双击 Start-Sync-Preview.cmd。'
                '请先阅读 MANUAL_SYNC_PREVIEW.txt。\n'
                '当前动作仍用旧角色图集；新的实机抠图仍在修整，未进入 runtime。\n'
                '语音默认关闭；默认声线仍是 A。\n'
                'Android 0.1.3 已安装，并完成单手机单 PC 的原生 TLS 部分实测；'
                '双 PC 与鸿蒙仍未验收。\n',
                encoding='utf-8')
            (folder / 'Start-Sync-Preview.cmd').write_text(
                '@echo off\r\ncd /d "%~dp0"\r\nstart "" "Si-DesktopPet.exe" --sync-preview\r\n',
                encoding='ascii')
            (folder / 'MANUAL_SYNC_PREVIEW.txt').write_text(
                '可选：Windows 手动协同预览\n\n'
                '先退出普通桌宠，再双击 Start-Sync-Preview.cmd。首次启用会备份旧存档并创建事件账本。\n'
                '在“相伴手记”页导出事件包，传到另一台 Windows PC 后导入；'
                '另一台也导出并传回本机导入，才能让两端取得相同事件。\n'
                '请保管事件包，其中有互动和手记。摘要只校验损坏，不做身份认证。\n'
                '不要复制另一台电脑的 settings.sync-v1.json；全新空白电脑可接收已有电脑的旧记录，'
                '旧存档已分叉时会拒绝自动合并。\n'
                '较早版本已生成的零值账本不会自动改写，请阅读 docs/LAN_SYNC_PREVIEW.md 的升级限制。\n'
                'Android 0.1.3 已安装，并完成单手机单 PC 的原生 TLS 部分实测；'
                '两台物理 PC、冻结 Windows 界面与旧存档组合、鸿蒙平板仍未验收。\n'
                '自由聊聊默认使用 GenAI 预设和 DeepSeek-V4.1-Flash；'
                '桌宠不自动登录学校账号，也不启动反代。\n',
                encoding='utf-8')
            portable_tmp = temporary / portable.name
            source_tmp = temporary / source.name
            zip_tree(folder, portable_tmp)
            zip_source(source_tmp)
            portable_check = validate_zip(portable_tmp, NAME)
            source_check = validate_zip(source_tmp, SOURCE_NAME, source=True)
            if portable.exists() or source.exists() or sha_file.exists():
                raise SystemExit('A 2.0.0a8 release file appeared during packaging; refusing to overwrite.')
            os.replace(portable_tmp, portable)
            created_portable = True
            if source.exists() or sha_file.exists():
                raise SystemExit('A 2.0.0a8 release file appeared during packaging; refusing to overwrite.')
            os.replace(source_tmp, source)
            created_source = True

        current = snapshot_archives()
        changed = sorted(name for name, value in previous.items() if current.get(name) != value)
        if changed:
            raise SystemExit(f'Existing release archives changed during packaging: {changed}')
        report = {
            'version': VERSION,
            'status': 'packaged_pending_live_and_main_collaborator',
            'built_from': 'dist-2.0a8/SiDesktopPet',
            'dialogue': dialogue,
            'genai_source': genai,
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
            'live_genai_verified': False,
            'source_ui_real_send_verified': False,
            'live_genai_note': ('Packaging cannot prove real model availability. The main '
                                'collaborator must run the new six-round DeepSeek check '
                                '(qa/genai-live-v3/report.json) and the source Qt UI real-send '
                                'check (qa/genai-ui-live-v2/report.json). Historical GPT v1/v2 '
                                'reports are not accepted for this release. Frozen UI send is '
                                'still not exercised.'),
            'final_delivery_ready': False,
        }
        temporary_sha = sha_file.with_suffix('.json.tmp')
        temporary_sha.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        if sha_file.exists():
            raise SystemExit('The 2.0.0a8 SHA file appeared during packaging; refusing to overwrite.')
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
