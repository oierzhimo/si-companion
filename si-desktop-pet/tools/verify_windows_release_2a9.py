"""Verify the 2.0.0a9 preview archives, relocated frozen app and GenAI boundaries.

The tool never runs a live model conversation and never writes a positive live
claim.  It reads the new six-round domestic-model report at
``qa/genai-live-v3/report.json`` and the source-Qt real-send report at
``qa/genai-ui-live-v3/report.json``.  Missing, historical or inconsistent
evidence refuses final delivery.  The UI report proves source Qt send only; it
never proves that the frozen executable's UI sent a message.  The tool also
checks that the packaged manual states the preview limits instead of claiming
one-click proxy startup, delivered full-body artwork or multi-PC/HarmonyOS 5
acceptance.
"""
from pathlib import Path
from collections import Counter
import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import types
import zipfile
import zlib
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / 'release'
VERSION = '2.0.0a9'
NAME = f'SiDesktopPet-{VERSION}-Windows-x64'
SOURCE_NAME = f'SiDesktopPet-{VERSION}-Source'
LIVE_REPORT = ROOT / 'qa' / 'genai-live-v3' / 'report.json'
UI_LIVE_REPORT = ROOT / 'qa' / 'genai-ui-live-v3' / 'report.json'
LIVE_KIND = 'genai-live-v3-synthetic-acceptance'
LIVE_MODEL = 'DeepSeek-V4.1-Flash'
LIVE_ROUNDS = 6

# The packaged manual must state these preview boundaries.  Missing phrases
# fail the release instead of letting an over-claiming manual ship.
MANUAL_REQUIRED = {
    'preview_label': '预览版',
    'deepseek_default': 'DeepSeek-V4.1-Flash',
    'glm_optional': 'GLM-5.3-Flash',
    'manual_proxy_start': '手动启动',
    'admin_111_framework': '111 框架',
    'quiet_companion': '安静陪我',
    'quiet_late_reply_handled': '迟到回复',
    'voice_health_check': '健康检查',
    'voice_async_startup': '异步',
    'fullbody_art_not_delivered': '全身新图',
    'complete_actions_not_delivered': '完整动作',
    'multi_pc_not_accepted': '多 PC',
    'harmony5_not_accepted': '鸿蒙 5',
    'source_qt_named': '源码 Qt',
    'frozen_exe_named': '打包 EXE',
    'frozen_exe_real_chat_boundary': '真实模型聊天',
    'old_data_backup': '备份',
    'exit_old_before_migration': '退出旧版',
    'migration_by_user_choice': '按你的选择',
    'historical_gpt_not_new_evidence': '历史 GPT',
    'qa_privacy_boundary': '不读取真实用户对话',
    'offline_test_count': '169 项通过',
    'symlink_skip_recorded': '符号链接测试跳过',
}
MANUAL_FORBIDDEN = ('一键启动',)

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
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def file_crc32(path):
    value = 0
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value = zlib.crc32(chunk, value)
    return value & 0xffffffff


def source_excluded(rel):
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


def expected_source():
    root = ROOT.resolve()
    expected = {}
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
                raise RuntimeError(f'Source tree contains a link: {path}')
            kept.append(dirname)
        dirnames[:] = kept
        for filename in sorted(filenames):
            rel = rel_base / filename
            if source_excluded(rel):
                continue
            path = base / filename
            if is_link(path):
                raise RuntimeError(f'Source tree contains a link: {path}')
            if not path.is_file():
                continue
            if not path.resolve().is_relative_to(root):
                raise RuntimeError(f'Source path escapes the project: {path}')
            expected[rel.as_posix()] = path.stat().st_size
    return expected


def safe_raw_name(raw, target):
    """Check the raw ZIP name, not only Python's normalized ``filename``."""
    if not isinstance(raw, str) or not raw or '\x00' in raw:
        raise RuntimeError('ZIP contains an invalid member name')
    normalized = raw.replace('\\', '/')
    if normalized.startswith('/') or normalized.startswith('//'):
        raise RuntimeError(f'ZIP contains an absolute path: {raw!r}')
    if len(normalized) >= 2 and normalized[1] == ':':
        raise RuntimeError(f'ZIP contains a drive-qualified path: {raw!r}')
    parts = normalized.split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise RuntimeError(f'ZIP contains an unsafe path: {raw!r}')
    if any(':' in part or part.endswith((' ', '.')) for part in parts):
        raise RuntimeError(f'ZIP contains a Windows-ambiguous path: {raw!r}')
    if not (target / normalized).resolve().is_relative_to(target.resolve()):
        raise RuntimeError(f'ZIP member escapes extraction: {raw!r}')
    return normalized


def zip_members(archive, target, prefix):
    bad = archive.testzip()
    if bad:
        raise RuntimeError(f'Corrupt ZIP entry: {bad}')
    members = []
    seen = set()
    for info in archive.infolist():
        raw = info.orig_filename
        clean = safe_raw_name(raw, target)
        safe_raw_name(info.filename, target)
        if not clean.startswith(prefix + '/'):
            raise RuntimeError(f'Unexpected top-level ZIP entry: {raw!r}')
        rel = clean[len(prefix) + 1:]
        if not rel or clean.endswith('/'):
            raise RuntimeError(f'Unexpected directory ZIP entry: {raw!r}')
        lower = clean.lower()
        if lower in seen:
            raise RuntimeError(f'Duplicate ZIP entry: {raw!r}')
        seen.add(lower)
        members.append((info, clean, rel))
    return members


def verify_source_archive():
    path = RELEASE / f'{SOURCE_NAME}.zip'
    if not path.is_file():
        raise RuntimeError('Source archive is missing')
    expected = expected_source()
    with zipfile.ZipFile(path) as archive:
        members = zip_members(archive, ROOT, SOURCE_NAME)
        entries = {rel: info for info, clean, rel in members}
        if set(entries) != set(expected):
            missing = sorted(set(expected) - set(entries))
            extra = sorted(set(entries) - set(expected))
            raise RuntimeError(f'Source archive differs from source tree: missing={missing[:5]} extra={extra[:5]}')
        for rel, info in entries.items():
            reason = sensitive_reason(Path(rel))
            if reason:
                raise RuntimeError(f'Source archive contains {reason}: {rel}')
            if source_excluded(Path(rel)):
                raise RuntimeError(f'Source archive contains an excluded path: {rel}')
            if Path(rel).suffix.lower() in AUDIO_SUFFIXES:
                raise RuntimeError(f'Source archive contains listening audio: {rel}')
            if expected[rel] != info.file_size:
                raise RuntimeError(f'Source archive size mismatch: {rel}')
            if info.CRC != file_crc32(ROOT / rel):
                raise RuntimeError(f'Source archive content differs from the source tree: {rel}')
        for required in ('src/si_pet/__init__.py', 'src/si_pet/conversation.py',
                         'src/si_pet/conversation_ui.py', 'src/si_pet/voice.py',
                         'tools/make_windows_release_2a9.py',
                         'tools/verify_windows_release_2a9.py'):
            if required not in entries:
                raise RuntimeError(f'Source archive is missing {required}')
        version_text = archive.read(entries['src/si_pet/__init__.py']).decode('utf-8')
        if not re.search(r"__version__\s*=\s*['\"]2\.0\.0a9['\"]", version_text):
            raise RuntimeError('Source archive version is not 2.0.0a9')
        conversation = archive.read(entries['src/si_pet/conversation.py']).decode('utf-8')
        ui = archive.read(entries['src/si_pet/conversation_ui.py']).decode('utf-8')
        voice = archive.read(entries['src/si_pet/voice.py']).decode('utf-8')
        genai_features = {
            'fetch_models': 'def fetch_models' in conversation,
            'request_reply': 'def request_reply' in conversation,
            'optional_bearer_header': ('_auth_headers' in conversation
                                       and 'Authorization' in conversation
                                       and 'Bearer ' in conversation),
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
        if not all(genai_features.values()):
            missing = sorted(name for name, ok in genai_features.items() if not ok)
            raise RuntimeError(f'GenAI source features are missing: {missing}')
        companion_features = {
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
        if not all(companion_features.values()):
            missing = sorted(name for name, ok in companion_features.items() if not ok)
            raise RuntimeError(f'Companion/voice source features are missing: {missing}')
        android = archive.read(entries['mobile/android/app/src/main/assets/lines.json'])
        harmony = archive.read(entries['mobile/harmony/entry/src/main/resources/rawfile/lines.json'])
        if android != harmony:
            raise RuntimeError('The two mobile dialogue tables differ')
        dialogue = json.loads(android.decode('utf-8'))
        if len(dialogue) != 11 or any(len(lines) != 6 for lines in dialogue.values()):
            raise RuntimeError('Source archive dialogue table is not 11 occasions by 6 lines')
    return {
        'integrity': True,
        'raw_zip_names_safe': True,
        'source_tree_matches_archive': True,
        'build_dist_qa_data_audio_venv_external_excluded': True,
        'art_source_local_working_material_excluded': True,
        'sensitive_names_absent': True,
        'genai_source_features': genai_features,
        'companion_source_features': companion_features,
        'genai_source_note': ('Source presence proves the client feature was packaged, '
                              'not that a real model is available or replies well.'),
        'companion_source_note': ('Source presence proves the quiet button/command, '
                                  'late-reply invalidation and asynchronous voice health '
                                  'probe were packaged; it does not prove runtime '
                                  'behaviour or GUI acceptance.'),
        'genai_service_wired_into_ui': not companion_features['genai_service_not_wired_into_ui'],
        'mobile_lines_identical': True,
        'occasions': len(dialogue),
        'lines': sum(len(lines) for lines in dialogue.values()),
        'sha256': digest(path),
    }


def verify_sha_file():
    path = RELEASE / f'SHA256SUMS-{VERSION}.json'
    if not path.is_file():
        raise RuntimeError('Versioned SHA256 file is missing')
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('version') != VERSION:
        raise RuntimeError('SHA256 file version is not 2.0.0a9')
    if data.get('preview_release') is not True:
        raise RuntimeError('SHA256 file does not mark this as a preview release')
    if data.get('genai_default_model') != LIVE_MODEL:
        raise RuntimeError('SHA256 file does not record the new default domestic model')
    if data.get('genai_optional_model') != 'GLM-5.3-Flash':
        raise RuntimeError('SHA256 file does not record the optional domestic model')
    if data.get('manual_start_required') is not True:
        raise RuntimeError('SHA256 file does not record the manual proxy-start requirement')
    if data.get('genai_service_wired_into_ui') is not False:
        raise RuntimeError('SHA256 file claims the experimental proxy service is wired into the UI')
    if data.get('live_genai_verified') is not False:
        raise RuntimeError('Packaging SHA file must not claim live GenAI verification')
    if data.get('source_ui_real_send_verified') is not False:
        raise RuntimeError('Packaging SHA file must not claim source UI real-send verification')
    if data.get('frozen_executable_real_chat_tested') is not False:
        raise RuntimeError('Packaging SHA file must not claim frozen EXE real-chat verification')
    if data.get('final_delivery_ready') is not False:
        raise RuntimeError('Packaging SHA file must not claim final delivery readiness')
    if data.get('previous_zip_apk_hashes_unchanged') is not True:
        raise RuntimeError('Packaging SHA file does not record unchanged prior archives')
    archives = data.get('archives')
    expected_names = {f'{NAME}.zip', f'{SOURCE_NAME}.zip'}
    if not isinstance(archives, dict) or set(archives) != expected_names:
        raise RuntimeError('SHA256 file does not describe exactly the two new archives')
    for name, metadata in archives.items():
        archive_path = RELEASE / name
        if not archive_path.is_file():
            raise RuntimeError(f'Archive listed in SHA256 file is missing: {name}')
        if metadata.get('bytes') != archive_path.stat().st_size:
            raise RuntimeError(f'Archive size differs from SHA256 file: {name}')
        if metadata.get('sha256') != digest(archive_path):
            raise RuntimeError(f'Archive hash differs from SHA256 file: {name}')
    previous_hashes = data.get('previous_zip_apk_hashes')
    previous_count = data.get('previous_zip_apk_count')
    if (not isinstance(previous_hashes, list)
            or not all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value)
                       for value in previous_hashes)
            or not isinstance(previous_count, int)
            or previous_count != len(previous_hashes)):
        raise RuntimeError('SHA256 file lacks the prior ZIP/APK hash snapshot')
    current_hashes = [digest(p) for p in RELEASE.iterdir()
                      if p.is_file() and p.suffix.lower() in {'.zip', '.apk'}
                      and p.name not in expected_names]
    available = Counter(current_hashes)
    for value in previous_hashes:
        if available[value] <= 0:
            raise RuntimeError('A prior ZIP/APK archive changed or disappeared after packaging')
        available[value] -= 1
    return {
        'versioned_sha_file_consistent': True,
        'preview_release': True,
        'previous_zip_apk_hashes_unchanged': True,
        'previous_zip_apk_count': len(previous_hashes),
        'genai_default_model': LIVE_MODEL,
        'genai_optional_model': 'GLM-5.3-Flash',
        'manual_start_required': True,
        'genai_service_wired_into_ui': False,
        'packaging_does_not_claim_live': True,
        'packaging_does_not_claim_source_ui_send': True,
        'packaging_does_not_claim_frozen_real_chat': True,
    }


def verify_manual(folder):
    """Require the packaged manual to state preview limits, not over-claim."""
    start = (folder / 'START_HERE.txt').read_text(encoding='utf-8')
    sync = (folder / 'MANUAL_SYNC_PREVIEW.txt').read_text(encoding='utf-8')
    checks = {name: phrase in start for name, phrase in MANUAL_REQUIRED.items()}
    checks['sync_manual_preview'] = '预览版' in sync
    checks['sync_manual_manual_start'] = '手动启动' in sync
    checks['sync_manual_domestic_models'] = ('DeepSeek-V4.1-Flash' in sync
                                             and 'GLM-5.3-Flash' in sync)
    checks['sync_manual_backup'] = '备份' in sync
    checks['sync_manual_frozen_boundary'] = '打包 EXE' in sync
    checks['sync_manual_harmony5'] = '鸿蒙 5' in sync
    checks['no_one_click_proxy_claim'] = not any(
        phrase in start for phrase in MANUAL_FORBIDDEN)
    if not all(checks.values()):
        missing = sorted(name for name, ok in checks.items() if not ok)
        raise RuntimeError(f'Packaged manual is missing preview/boundary statements: {missing}')
    return {
        'preview_label_present': True,
        'manual_proxy_start_present': True,
        'domestic_models_present': True,
        'quiet_companion_present': True,
        'voice_async_startup_present': True,
        'fullbody_art_not_delivered_present': True,
        'complete_actions_not_delivered_present': True,
        'multi_pc_and_harmony_not_accepted_present': True,
        'source_qt_vs_frozen_exe_present': True,
        'frozen_exe_real_chat_boundary_present': True,
        'old_data_backup_and_migration_present': True,
        'historical_gpt_not_new_evidence_present': True,
        'qa_privacy_boundary_present': True,
        'offline_test_counts_present': True,
        'no_one_click_proxy_claim': True,
        'checks': checks,
    }


def code_symbols(code):
    names = set()
    strings = set()

    def walk(item):
        names.update(item.co_names)
        for constant in item.co_consts:
            if isinstance(constant, str):
                strings.add(constant)
            elif isinstance(constant, types.CodeType):
                walk(constant)

    walk(code)
    return names, strings


def inspect_frozen_modules(exe, workdir):
    """Read the bundled PYZ without changing app source; never uses the network."""
    result = {
        'available': False,
        'reason': None,
        'modules': {},
        'executed_bundled_conversation_module': False,
        'conversation_api_verified': False,
        'conversation_ui_executed': False,
        'conversation_ui_symbols_verified': False,
        'optional_bearer_header_verified': False,
        'network_used': False,
        'caveat': ('Bundled bytecode/API presence and in-memory header checks do not '
                   'prove that a real proxy or model is reachable.'),
    }
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except Exception as exc:
        result['reason'] = f'PyInstaller archive reader unavailable: {type(exc).__name__}'
        return result
    try:
        reader = CArchiveReader(str(exe))
        pyz_names = [name for name, entry in reader.toc.items() if entry[-1] == 'z']
        if not pyz_names:
            raise RuntimeError('No embedded PYZ archive was found')
        pyz = reader.open_embedded_archive(pyz_names[0])
        module_names = ('si_pet', 'si_pet.conversation', 'si_pet.conversation_ui')
        result['modules'] = {name: name in pyz.toc for name in module_names}
        if not (result['modules']['si_pet.conversation']
                and result['modules']['si_pet.conversation_ui']):
            raise RuntimeError('The frozen PYZ is missing a required si_pet module')

        conversation_code = pyz.extract('si_pet.conversation')
        namespace = {
            '__name__': 'si_pet.conversation',
            '__package__': 'si_pet',
            '__file__': str(exe),
        }
        exec(conversation_code, namespace)
        result['executed_bundled_conversation_module'] = True
        auth = namespace.get('_auth_headers')
        optional_bearer = (callable(auth) and auth('') == {}
                           and auth('probe') == {'Authorization': 'Bearer probe'})
        conversation = namespace.get('Conversation')
        default_conversation = (isinstance(conversation, type)
                                and conversation(workdir / 'qa-frozen-no-save.json').provider == 'local')
        conversation_api = (
            callable(namespace.get('fetch_models'))
            and callable(namespace.get('request_reply'))
            and isinstance(conversation, type)
            and namespace.get('DEFAULT_GENAI_ENDPOINT') == 'http://127.0.0.1:8000/v1'
            and isinstance(namespace.get('PROVIDERS'), tuple)
            and {'local', 'genai'}.issubset(namespace.get('PROVIDERS'))
            and namespace.get('GENAI_DEFAULT_API_KEY') == 'sk-shanghaitech-genai'
            and namespace.get('GENAI_DEFAULT_MODEL') == 'DeepSeek-V4.1-Flash'
            and namespace.get('GENAI_ALLOWED_MODELS') == ('DeepSeek-V4.1-Flash',
                                                          'GLM-5.3-Flash')
            and optional_bearer
            and default_conversation
        )
        result['optional_bearer_header_verified'] = bool(optional_bearer)
        result['conversation_api_verified'] = bool(conversation_api)

        ui_code = pyz.extract('si_pet.conversation_ui')
        ui_names, ui_strings = code_symbols(ui_code)
        ui_symbols = {
            'ConversationDialog': 'ConversationDialog' in ui_names,
            'fetch_models': 'fetch_models' in ui_names,
            'DEFAULT_GENAI_ENDPOINT': 'DEFAULT_GENAI_ENDPOINT' in ui_names,
            'genai_preset_string': '上科大 GenAI 反代' in ui_strings,
            'genai_provider_string': 'genai' in ui_strings,
            'quiet_command': 'QUIET_COMMANDS' in ui_names,
            'quiet_button': 'quiet_companion' in ui_names,
            'quiet_button_string': '安静陪我' in ui_strings,
            'late_reply_abandon': 'abandon_pending_reply' in ui_names,
        }
        result['conversation_ui_symbols'] = ui_symbols
        result['conversation_ui_symbols_verified'] = all(ui_symbols.values())
        result['available'] = True
        result['api_verified'] = bool(conversation_api and all(ui_symbols.values()))
    except Exception as exc:
        result['reason'] = f'{type(exc).__name__}: {exc}'
    return result


def verify_frozen():
    archive_path = RELEASE / f'{NAME}.zip'
    if not archive_path.is_file():
        raise RuntimeError('Portable archive is missing')
    qa = ROOT / 'qa'
    qa.mkdir(exist_ok=True)
    target = Path(tempfile.mkdtemp(dir=qa, prefix=f'portable-check-{NAME}-'))
    with zipfile.ZipFile(archive_path) as archive:
        members = zip_members(archive, target, NAME)
        for info, clean, rel in members:
            reason = sensitive_reason(Path(rel)) or packaged_excluded_reason(Path(rel))
            if reason:
                raise RuntimeError(f'Portable archive contains {reason}: {rel}')
        archive.extractall(target)
    folder = target / NAME
    manual = verify_manual(folder)
    exe = folder / 'Si-DesktopPet.exe'
    env = os.environ.copy()
    system = Path(env.get('SystemRoot', r'C:\Windows'))
    env['PATH'] = str(system / 'System32') + ';' + str(system)
    for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'PYTHONSTARTUP', 'CONDA_PREFIX'):
        env.pop(key, None)
    report = {
        'version': VERSION,
        'zip_integrity': True,
        'relocated_extract': True,
        'python_removed_from_path': True,
        'modes': {},
        'conversation_ui_constructed': False,
        'conversation_ui_send_exercised': False,
        'frozen_executable_real_chat_tested': False,
        'conversation_ui_note': ('Smoke constructs and renders the frozen dialog only; '
                                 'it never clicks send or contacts a model. The frozen '
                                 'EXE real-chat path is not verified.'),
    }
    sync_ok = False
    for mode in ('ordinary', 'sync'):
        run = target / mode
        run.mkdir()
        output = run / 'smoke.json'
        args = [str(exe), '--smoke-test', str(output)]
        if mode == 'sync':
            args.extend(['--sync-preview', '--check-lan'])
        result = subprocess.run(args, cwd=run, env=env, timeout=45,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode or not output.is_file():
            raise RuntimeError(f'{mode} startup failed with exit code {result.returncode}')
        data = json.loads(output.read_text(encoding='utf-8'))
        if not data.get('ok') or data.get('version') != VERSION:
            raise RuntimeError(f'{mode} startup report failed')
        if not data.get('conversation_ui_constructed'):
            raise RuntimeError(f'{mode} frozen conversation dialog did not construct')
        if mode == 'sync':
            if not data.get('lan_check', {}).get('ok'):
                raise RuntimeError('Frozen app LAN pairing/exchange failed')
            sync_ok = True
        ledger = run / 'qa-settings.sync-v1.json'
        if ledger.exists() != (mode == 'sync'):
            raise RuntimeError('Sync was not opt-in')
        if not (run / 'conversation-preview.png').is_file():
            raise RuntimeError(f'{mode} did not render the conversation preview')
        report['modes'][mode] = data
        report['conversation_ui_constructed'] = True
    preview = target / 'panel.png'
    result = subprocess.run([str(exe), '--render-preview', str(preview)], cwd=target,
                            env=env, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode or not preview.is_file():
        raise RuntimeError('Frozen panel rendering failed')
    log_path = folder / 'data' / 'app.log'
    if not log_path.is_file():
        raise RuntimeError('Frozen app did not write its log')
    log = log_path.read_text(encoding='utf-8')
    if 'Unhandled exception' in log:
        raise RuntimeError('Frozen app logged an unhandled exception')
    report.update(
        ordinary_startup=True,
        optional_sync_tls=sync_ok,
        local_tls_pair_and_exchange=sync_ok,
        conversation_ui_constructed=bool(report['conversation_ui_constructed']),
        panel_preview=str(preview),
        archive_sha256=digest(archive_path),
        log_has_no_unhandled_exception=True,
        manual=manual,
        frozen_module_api=inspect_frozen_modules(exe, target),
    )
    return report


def current_source_hashes():
    """Return SHA256 values for the two production GenAI modules, or None."""
    hashes = {}
    for relative in ('src/si_pet/conversation.py', 'src/si_pet/conversation_ui.py'):
        path = ROOT / relative
        if not path.is_file():
            return None
        try:
            hashes[relative] = digest(path)
        except OSError:
            return None
    return hashes


def normalize_report_path(value):
    """Normalize a report path key so Windows backslashes compare consistently."""
    text = str(value).strip().replace('\\', '/')
    while text.startswith('./'):
        text = text[2:]
    return text.lstrip('/').casefold()


def normalized_hash_map(value):
    """Return normalized path -> lowercase SHA256, or None when malformed."""
    if not isinstance(value, dict):
        return None
    result = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            return None
        normalized = normalize_report_path(key)
        digest_value = item.strip().lower()
        if (not normalized
                or not re.fullmatch(r'[0-9a-f]{64}', digest_value)):
            return None
        # Conflicting duplicates after slash normalization must not be accepted.
        if normalized in result and result[normalized] != digest_value:
            return None
        result[normalized] = digest_value
    return result


SECRET_KEY_HINTS = ('api_key', 'apikey', 'access_key', 'secret', 'password',
                    'authorization', 'bearer', 'credential')


def contains_secret_material(value):
    """True when a report records a non-empty string under a secret-like key."""
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                return True
            lowered = key.lower()
            if (isinstance(item, str) and item.strip()
                    and any(hint in lowered for hint in SECRET_KEY_HINTS)):
                return True
            if contains_secret_material(item):
                return True
    elif isinstance(value, list):
        return any(contains_secret_material(item) for item in value)
    return False


def round_has_reply(item):
    if not isinstance(item, dict):
        return False
    for key in ('reply', 'model_reply'):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def verify_live():
    current = current_source_hashes() or {}
    current_conversation = current.get('src/si_pet/conversation.py')
    result = {
        'path': 'qa/genai-live-v3/report.json',
        'present': LIVE_REPORT.is_file(),
        'expected_kind': LIVE_KIND,
        'expected_model': LIVE_MODEL,
        'expected_rounds': LIVE_ROUNDS,
        'technical_roundtrip_verified': None,
        'status': None,
        'kind': None,
        'model': None,
        'completed_rounds': None,
        'total_turns': None,
        'rounds_count': None,
        'reload_consistent': None,
        'production_conversation_sha256': None,
        'current_conversation_sha256': current_conversation,
        'checks': {},
        'verified': False,
        'final_delivery': 'refused_missing_live_report',
        'persona_quality_verified': None,
        'note': ('The verification tool never creates, edits or deletes this report. '
                 'Historical GPT v1/v2 reports cannot satisfy the new domestic-model '
                 'acceptance. No reply text or access key is copied into this report.'),
    }
    if not LIVE_REPORT.is_file():
        result['reason'] = 'qa/genai-live-v3/report.json is missing'
        return result
    try:
        raw = LIVE_REPORT.read_bytes()
    except OSError as exc:
        result['reason'] = f'Live report cannot be read: {type(exc).__name__}'
        result['final_delivery'] = 'refused_live_report_unreadable'
        return result
    result['sha256'] = hashlib.sha256(raw).hexdigest()
    try:
        report = json.loads(raw.decode('utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        result['reason'] = f'Live report is unreadable: {type(exc).__name__}'
        result['final_delivery'] = 'refused_live_report_unreadable'
        return result
    if not isinstance(report, dict):
        result['reason'] = 'Live report is not a JSON object'
        result['final_delivery'] = 'refused_live_report_invalid'
        return result
    for key in ('kind', 'status', 'technical_roundtrip_verified', 'completed_rounds',
                'total_turns', 'endpoint', 'model', 'reload_consistent',
                'persona_quality_verified'):
        result[key] = report.get(key)
    rounds = report.get('rounds')
    result['rounds_count'] = len(rounds) if isinstance(rounds, list) else None
    modules = normalized_hash_map(report.get('production_modules'))
    recorded_conversation = (modules.get(normalize_report_path('src/si_pet/conversation.py'))
                             if modules is not None else None)
    result['production_conversation_sha256'] = recorded_conversation
    checks = {
        'kind_is_v3': report.get('kind') == LIVE_KIND,
        'status_ok': report.get('status') == 'ok',
        'model_is_deepseek': report.get('model') == LIVE_MODEL,
        'technical_roundtrip_verified': report.get('technical_roundtrip_verified') is True,
        'completed_rounds_six': (type(report.get('completed_rounds')) is int
                                 and report.get('completed_rounds') == LIVE_ROUNDS),
        'total_turns_six': (type(report.get('total_turns')) is int
                            and report.get('total_turns') == LIVE_ROUNDS),
        'rounds_list_six': isinstance(rounds, list) and len(rounds) == LIVE_ROUNDS,
        'rounds_persisted': (isinstance(rounds, list)
                             and all(isinstance(item, dict)
                                     and item.get('persisted') is True
                                     for item in rounds)),
        'rounds_replies_non_empty': (isinstance(rounds, list)
                                     and all(round_has_reply(item) for item in rounds)),
        'reload_consistent': report.get('reload_consistent') is True,
        'production_conversation_hash_matches': (recorded_conversation is not None
                                                 and current_conversation is not None
                                                 and recorded_conversation
                                                 == current_conversation),
        'no_secret_material_recorded': not contains_secret_material(report),
    }
    result['checks'] = checks
    verified = all(checks.values())
    result['verified'] = verified
    result['final_delivery'] = ('accepted_live_evidence' if verified
                                else 'refused_live_acceptance_incomplete')
    if not verified:
        failed = sorted(name for name, ok in checks.items() if not ok)
        result['reason'] = 'Live acceptance conditions not met: ' + ', '.join(failed)
    return result


def verify_source_ui_live():
    current = current_source_hashes()
    result = {
        'path': 'qa/genai-ui-live-v3/report.json',
        'present': UI_LIVE_REPORT.is_file(),
        'expected_model': LIVE_MODEL,
        'status': None,
        'model': None,
        'qt_send_to_real_model_verified': None,
        'frozen_executable_claimed': None,
        'source_hashes_match_current': False,
        'source_ui_real_send_verified': False,
        'frozen_conversation_ui_send_exercised': False,
        'checks': {},
        'verified': False,
        'final_delivery': 'refused_missing_source_ui_live_report',
        'note': ('This evidence comes from the source Qt dialog sending through the real '
                 'loopback service. It is not the frozen executable UI and never sets '
                 'frozen conversation_ui_send_exercised to true.'),
    }
    if not UI_LIVE_REPORT.is_file():
        result['reason'] = 'qa/genai-ui-live-v3/report.json is missing'
        return result
    try:
        raw = UI_LIVE_REPORT.read_bytes()
    except OSError as exc:
        result['reason'] = f'Source UI report cannot be read: {type(exc).__name__}'
        result['final_delivery'] = 'refused_source_ui_report_unreadable'
        return result
    result['sha256'] = hashlib.sha256(raw).hexdigest()
    try:
        report = json.loads(raw.decode('utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        result['reason'] = f'Source UI report is unreadable: {type(exc).__name__}'
        result['final_delivery'] = 'refused_source_ui_report_unreadable'
        return result
    if not isinstance(report, dict):
        result['reason'] = 'Source UI report is not a JSON object'
        result['final_delivery'] = 'refused_source_ui_report_invalid'
        return result
    for key in ('status', 'model', 'qt_send_to_real_model_verified'):
        result[key] = report.get(key)
    result['frozen_executable_claimed'] = report.get('frozen_executable')
    hashes = normalized_hash_map(report.get('source_hashes'))
    expected_paths = ('src/si_pet/conversation.py', 'src/si_pet/conversation_ui.py')
    hashes_match = (hashes is not None and current is not None
                    and all(hashes.get(normalize_report_path(relative))
                            == current.get(relative) for relative in expected_paths))
    result['source_hashes_match_current'] = bool(hashes_match)
    model = report.get('model')
    checks = {
        'status_ok': report.get('status') == 'ok',
        'model_is_deepseek': (isinstance(model, str)
                              and model.strip().lower().startswith('deepseek')),
        'qt_send_to_real_model_verified': report.get('qt_send_to_real_model_verified') is True,
        'source_hashes_match_current': hashes_match,
        'source_not_claimed_as_frozen': report.get('frozen_executable') is not True,
        'synthetic_only': report.get('synthetic_only') is True,
        'no_school_credentials_read': report.get('school_credentials_read') is False,
        'no_real_user_data_read': report.get('real_user_data_read') is False,
        'no_secret_material_recorded': not contains_secret_material(report),
    }
    result['checks'] = checks
    verified = all(checks.values())
    result['verified'] = verified
    result['source_ui_real_send_verified'] = verified
    result['final_delivery'] = ('accepted_source_ui_evidence' if verified
                                else 'refused_source_ui_acceptance_incomplete')
    if not verified:
        failed = sorted(name for name, ok in checks.items() if not ok)
        result['reason'] = 'Source UI acceptance conditions not met: ' + ', '.join(failed)
    return result


def protected_live_report_directory(path):
    """Protect every genai-live*/genai-ui-live* QA directory, not only v1."""
    try:
        relative = path.relative_to(ROOT)
    except ValueError:
        return None
    parts = relative.parts
    if parts and parts[0].lower() == 'qa':
        for part in parts[1:]:
            candidate = part.lower()
            if (candidate.startswith('genai-live')
                    or candidate.startswith('genai-ui-live')):
                return part
    return None


def main():
    parser = argparse.ArgumentParser(description='Verify the 2.0.0a9 Windows preview without live chat')
    parser.add_argument('--report', type=Path, default=None,
                        help='new report path under the project (default: qa/release-2.0.0a9-verification.json)')
    args = parser.parse_args()
    output = args.report or (ROOT / 'qa' / f'release-{VERSION}-verification.json')
    output = output if output.is_absolute() else (Path.cwd() / output)
    resolved = output.resolve()
    if not resolved.is_relative_to(ROOT):
        raise SystemExit('Verification report must stay inside the project.')
    protected = protected_live_report_directory(resolved)
    if protected:
        raise SystemExit('Refusing to write inside the main collaborator live-report directory: '
                         f'qa/{protected}')
    if resolved.exists():
        raise SystemExit(f'Refusing to overwrite an existing report: {resolved}')

    report = {
        'kind': 'windows-release-verification',
        'version': VERSION,
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'status': 'failed',
        'final_delivery': 'refused_verification_failed',
        'checks': {},
        'limits': {
            'conversation_ui_send_exercised': False,
            'frozen_conversation_ui_send_exercised': False,
            'frozen_executable_real_chat_tested': False,
            'frozen_executable_network_chat_verified': False,
            'source_ui_real_send_is_not_frozen_ui': True,
            'real_model_quality_not_tested': True,
            'persona_quality_not_judged': True,
            'android_or_harmony_not_retested': True,
        },
    }
    report['live_genai'] = verify_live()
    report['source_ui_live'] = verify_source_ui_live()
    exit_code = 1
    try:
        source = verify_source_archive()
        sha = verify_sha_file()
        frozen = verify_frozen()
        report['source_archive'] = source
        report['sha256_file'] = sha
        report['frozen'] = frozen
        report['conversation_ui'] = {
            'constructed_in_frozen_smoke': frozen['conversation_ui_constructed'],
            'send_exercised': False,
            'frozen_conversation_ui_send_exercised': False,
            'frozen_executable_real_chat_tested': False,
            'source_ui_real_send_verified': report['source_ui_live']['verified'],
            'note': frozen['conversation_ui_note'],
            'source_ui_note': report['source_ui_live']['note'],
        }
        report['checks'] = {
            'packaging_and_source_boundaries': True,
            'relocated_frozen_startup': frozen['ordinary_startup'],
            'optional_sync_tls_pair_and_exchange': frozen['local_tls_pair_and_exchange'],
            'conversation_ui_constructed': frozen['conversation_ui_constructed'],
            'conversation_ui_send_exercised': False,
            'frozen_conversation_ui_send_exercised': False,
            'frozen_executable_real_chat_tested': False,
            'source_ui_real_send_verified': report['source_ui_live']['verified'],
            'frozen_module_api_verified': bool(
                frozen['frozen_module_api'].get('api_verified', False)),
            'live_genai_technical_roundtrip': report['live_genai']['verified'],
            'packaged_manual_preview_boundaries': True,
        }
        if not report['checks']['frozen_module_api_verified']:
            # The frozen package is the artifact users run.  If its bundled
            # conversation module cannot be inspected independently, say the
            # verification failed instead of silently accepting the package.
            api = frozen['frozen_module_api']
            raise RuntimeError('Frozen PYZ conversation API could not be verified: '
                               + str(api.get('reason') or api.get('conversation_ui_symbols')))
        live_ok = report['live_genai']['verified']
        source_ui_ok = report['source_ui_live']['verified']
        if live_ok and source_ui_ok:
            report['status'] = 'ok'
            report['final_delivery'] = 'ready_for_main_collaborator_acceptance'
            exit_code = 0
        elif not live_ok:
            report['status'] = 'pending_live_genai'
            report['final_delivery'] = report['live_genai']['final_delivery']
            exit_code = 2
        else:
            report['status'] = 'pending_source_ui_live'
            report['final_delivery'] = report['source_ui_live']['final_delivery']
            exit_code = 2
    except Exception as exc:
        report['status'] = 'failed'
        report['error'] = f'{type(exc).__name__}: {exc}'
        report['final_delivery'] = 'refused_verification_failed'
        exit_code = 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f'[结果] report={output}')
    if exit_code == 2:
        print('[结果] 打包/冻结检查已完成，但缺少合格的 DeepSeek 六轮真实报告或'
              '源码界面真实发送报告；拒绝最终交付。')
    raise SystemExit(exit_code)


if __name__ == '__main__':
    main()
