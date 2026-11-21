"""Package the 2.1.1 portable Windows x64 release with adaptive dark/light theme and custom school API support."""
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / 'src'))

from si_pet import __version__
from make_release import ROOT, RELEASE
from make_windows_release_2a9 import assert_tree_safe, digest, safe_member_name

VERSION = '2.1.1'
NAME = f'SiDesktopPet-{VERSION}-Windows-x64'
BUILT = ROOT / 'dist' / 'SiDesktopPet'

USER_GUIDE_TEXT = """祀桌宠（SiDesktopPet）2.1.1 Windows 客户端 · 快速使用说明

【一、解压与启动（绿色免安装）】
1. 请将 SiDesktopPet-2.1.1-Windows-x64.zip 完整解压到普通文件夹（例如 D:\\SiDesktopPet），
   切勿直接在压缩包预览窗口内双击运行。
2. 双击运行主程序：Si-DesktopPet.exe。
3. 本版本已自动适配 Windows 浅色 / 深色主题（主面板、对话框输入区、右键菜单均随系统配色自动调整文字与背景对比度）。
4. 若弹出“Windows 已保护你的电脑”提示，点击“更多信息” -> “仍要运行”即可。

【二、三种主要模型连接方式（打开“自由聊聊”或“多模态聊聊”切换）】

方式一：自定义模型接口（适合协助者使用学校提供的 API / 第三方 OpenAI 兼容网关）
1. 在“连接方式”下拉框选择：自定义模型接口（学校 API / 第三方）。
2. 在“自定义接口地址”填写学校或平台给出的 OpenAI 兼容地址（如 https://.../v1 或自定义前缀）。
3. 在“API Key / 访问口令”填写你的学校 API Key / Token（仅保留在当前内存，绝不写盘）。
4. 在“模型”框填写学校接口提供的模型名称，或点击右侧“获取模型”自动读取列表。

方式二：DeepSeek 官方 API（无需显卡、无需安装大模型）
1. 前往 DeepSeek 开放平台（https://platform.deepseek.com）创建 API Key（sk-...）。
2. 在“连接方式”选择：DeepSeek 官方 API，接口自动填为 https://api.deepseek.com/v1 ，默认模型 deepseek-chat。
3. 粘贴 API Key 后即可纯文字对话（Key 仅留内存，下次重开需重新填入）。

方式三：本机模型（Ollama / LM Studio 等，完全离线零费用）
1. 在本电脑启动 Ollama（http://127.0.0.1:11434/v1）或 LM Studio（http://127.0.0.1:1234/v1）。
2. 在“连接方式”选择：本机模型（Ollama / 本地端口），填写已拉取的模型（默认 si-qwen2.5-3b-lang-lora:latest，多模态带图自动切 si-qwen2.5vl-3b-lora:latest）。

【三、日常功能与存档】
- 离线互动（散步、摸摸头、送点心、专注 25 分钟、安静陪伴）无需联网即可使用。
- 找回桌宠：托盘右键选择“找回祀”，或按快捷键 Ctrl+Alt+S。
- 所有本地对话与陪伴记录保存在同目录 data/ 下；升级覆盖前请先完全退出旧版并备份 data/ 目录。
"""

USER_GUIDE_MD = """# 祀桌宠（SiDesktopPet）2.1.1 客户端安装与使用指南

本软件为 **Windows 10 / 11（64 位）绿色便携版**，解压即用，已自动适配 **Windows 深色 / 浅色系统主题**，支持 **自定义学校/第三方 OpenAI 兼容 API**、**DeepSeek 官方 API** 与 **本机模型**。

---

## 一、安装与启动

1. **完整解压**：将 `SiDesktopPet-2.1.1-Windows-x64.zip` 完整解压到非系统受限目录（如 `D:\\SiDesktopPet`）。
2. **启动程序**：双击 **`Si-DesktopPet.exe`**；如遇 SmartScreen 提示，点击 **“更多信息” → “仍要运行”**。
3. **主题适配**：主面板、聊天输入框、下拉菜单及桌宠右键菜单会自动随 Windows **浅色 / 深色模式**切换高对比度字体颜色与底色。
4. **找回与退出**：右键桌宠或右下角系统托盘图标可呼出面板、找回桌宠（快捷键 `Ctrl + Alt + S`）或退出。

---

## 二、配置模型连接（按需选择）

在面板右侧点击 **“自由聊聊 · 本机 / DeepSeek / 自定义”** 或 **“多模态聊聊 · 本机 / DeepSeek / 自定义”**。

### 方式 A：自定义模型接口（推荐协助者使用学校提供的 API / 校内外兼容网关）

1. **连接方式**：选择 **`自定义模型接口（学校 API / 第三方）`**。
2. **自定义接口地址**：填入学校或中转平台提供的 OpenAI 兼容 Base URL（例如 `https://api.school.edu.cn/v1`、`https://.../api/v1`；即使误复制了末尾 `/chat/completions` 也会自动识别规范化）。
3. **API Key / 访问口令**：粘贴学校或平台发放的 API Key（仅留当前内存，不会写入 `data/` 存档）。
4. **模型**：点击右侧 **“获取模型”** 自动获取可用模型列表，或直接填写学校 API 文档指定的模型 ID。
5. 若所连学校模型本身支持视觉输入，在“多模态聊聊”中拖入或粘贴图片即可一并发送。

---

### 方式 B：DeepSeek 官方 API（普通电脑免部署即用）

1. 在 `https://platform.deepseek.com` 创建 API Key（`sk-...`）。
2. **连接方式**选择 **`DeepSeek 官方 API`**，接口自动填为 `https://api.deepseek.com/v1`，默认模型 `deepseek-chat`（也可改 `deepseek-reasoner`）。
3. 填入 API Key 后即可发送纯文字对话（发图前请先点击“清除图片”或切回支持视觉的模型）。

---

### 方式 C：本机模型（Ollama / LM Studio，完全离线）

1. 在本机启动 Ollama（默认 `http://127.0.0.1:11434/v1`）或 LM Studio（默认 `http://127.0.0.1:1234/v1`）。
2. **连接方式**保持/选择 **`本机模型（Ollama / 本地端口）`**，口令留空，填写已拉取模型或点“获取模型”。
3. 默认配置适配祀微调模型 `si-qwen2.5-3b-lang-lora:latest`（11434 端口发图时自动切换视觉模型 `si-qwen2.5vl-3b-lora:latest`）。

---

## 三、隐私与存档

- 所有对话历史、亲密度与设置仅保存于同目录 **`data/`** 中，API Key 仅留在内存，绝不写盘。
- 换电脑携带记录时直接复制整个文件夹即可；版本升级前请先完全退出旧版并备份 **`data/`**。
"""


def write_clean_data_dir(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / 'README.txt').write_text(
        '用户存档与对话记录保存在本目录。更换电脑时复制整个程序文件夹即可；\n'
        '升级前请先完全退出旧版并备份本目录。\n',
        encoding='utf-8',
    )
    default_cfg = {
        'turns': [],
        'profile': '',
        'endpoint': 'http://127.0.0.1:11434/v1',
        'model': 'si-qwen2.5-3b-lang-lora:latest',
        'provider': 'local',
    }
    for fname in ('conversation.json', 'multimodal_conversation.json'):
        (data_dir / fname).write_text(
            json.dumps(default_cfg, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )


def zip_portable(folder: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                rel = path.relative_to(folder).as_posix()
                archive.write(path, f'{NAME}/{rel}')


def verify_zip_entries(zip_path: Path, expected_folder: Path) -> int:
    with zipfile.ZipFile(zip_path) as archive:
        bad = archive.testzip()
        if bad:
            raise SystemExit(f'Corrupt ZIP entry in {zip_path.name}: {bad}')
        expected = {
            f'{NAME}/{p.relative_to(expected_folder).as_posix()}': p.stat().st_size
            for p in sorted(expected_folder.rglob('*'))
            if p.is_file()
        }
        actual = {}
        for info in archive.infolist():
            clean = safe_member_name(info.orig_filename, zip_path.parent)
            safe_member_name(info.filename, zip_path.parent)
            if not clean.startswith(NAME + '/'):
                raise SystemExit(f'Unexpected entry in {zip_path.name}: {info.orig_filename!r}')
            actual[clean] = info.file_size
        if set(expected) != set(actual):
            missing = sorted(set(expected) - set(actual))[:5]
            extra = sorted(set(actual) - set(expected))[:5]
            raise SystemExit(f'ZIP file mismatch missing={missing} extra={extra}')
        return len(actual)


def smoke_verify_relocated(zip_path: Path) -> dict:
    target = ROOT / 'qa' / f'portable-check-{NAME}'
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(target)
    if os.name == 'nt':
        subprocess.run(
            ['icacls', str(target), '/setintegritylevel', '(OI)(CI)M', '/T', '/Q'],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    folder = target / NAME
    exe = folder / 'Si-DesktopPet.exe'
    report_path = ROOT / 'qa' / f'portable-smoke-{NAME}.json'
    if report_path.exists():
        report_path.unlink()
    env = os.environ.copy()
    sys_root = env.get('SystemRoot', r'C:\Windows')
    env['PATH'] = str(Path(sys_root) / 'System32') + ';' + sys_root
    for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'):
        env.pop(key, None)
    completed = subprocess.run(
        [str(exe), '--quiet', '--smoke-test', str(report_path)],
        cwd=folder,
        env=env,
        timeout=30,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    if completed.returncode != 0 or not report_path.exists():
        raise SystemExit(f'Portable smoke-test failed with code {completed.returncode}')
    payload = json.loads(report_path.read_text(encoding='utf-8'))
    if not payload.get('ok'):
        raise SystemExit(f'Portable smoke-test reported ok=False: {payload}')
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--built', type=Path, default=BUILT)
    args = parser.parse_args()
    built = args.built.resolve()
    if __version__ != VERSION:
        raise SystemExit(f'Package __version__ ({__version__}) != {VERSION}')
    if not (built / 'Si-DesktopPet.exe').is_file():
        raise SystemExit(f'Missing built executable under {built}')

    RELEASE.mkdir(exist_ok=True)
    portable_dir = RELEASE / NAME
    portable_zip = RELEASE / f'{NAME}.zip'
    sha_file = RELEASE / f'SHA256SUMS-{VERSION}.json'

    if portable_dir.exists():
        shutil.rmtree(portable_dir)

    with tempfile.TemporaryDirectory(dir=RELEASE, prefix='.stage-211-') as tmpdir:
        stage = Path(tmpdir) / NAME
        shutil.copytree(built, stage, ignore=shutil.ignore_patterns('data', '__pycache__'))
        assert_tree_safe(stage)
        for name in ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md'):
            if (ROOT / name).is_file():
                shutil.copy2(ROOT / name, stage / name)
        if (ROOT / 'docs').is_dir():
            shutil.copytree(ROOT / 'docs', stage / 'docs')
        if (ROOT / 'licenses').is_dir():
            shutil.copytree(ROOT / 'licenses', stage / 'licenses')
        write_clean_data_dir(stage / 'data')
        (stage / 'START_HERE.txt').write_text(USER_GUIDE_TEXT, encoding='utf-8')
        (stage / '使用说明-必读.txt').write_text(USER_GUIDE_TEXT, encoding='utf-8')
        (stage / '使用指南.md').write_text(USER_GUIDE_MD, encoding='utf-8')
        (RELEASE / f'SiDesktopPet-{VERSION}-使用指南.md').write_text(USER_GUIDE_MD, encoding='utf-8')
        assert_tree_safe(stage)

        tmp_zip = Path(tmpdir) / f'{NAME}.zip'
        zip_portable(stage, tmp_zip)
        entry_count = verify_zip_entries(tmp_zip, stage)
        shutil.copytree(stage, portable_dir)
        if os.name == 'nt':
            subprocess.run(
                ['icacls', str(portable_dir), '/setintegritylevel', '(OI)(CI)M', '/T', '/Q'],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        os.replace(tmp_zip, portable_zip)

    smoke = smoke_verify_relocated(portable_zip)
    sha_data = {
        portable_zip.name: {
            'bytes': portable_zip.stat().st_size,
            'sha256': digest(portable_zip),
            'entries': entry_count,
        }
    }
    sha_file.write_text(json.dumps(sha_data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({
        'version': VERSION,
        'portable_dir': str(portable_dir),
        'portable_zip': str(portable_zip),
        'sha_file': str(sha_file),
        'zip_entries': entry_count,
        'zip_bytes': portable_zip.stat().st_size,
        'zip_sha256': sha_data[portable_zip.name]['sha256'],
        'smoke_test': smoke,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
