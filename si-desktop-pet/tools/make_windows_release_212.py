"""Package the 2.1.2 portable Windows x64 release with 3D anti-clipping, Soul Engine V1.2 persona, smart headphone/game TTS, and Foley SFX."""
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

VERSION = '2.1.2'
NAME = f'SiDesktopPet-{VERSION}-Windows-x64'
BUILT = ROOT / 'dist' / 'SiDesktopPet'

USER_GUIDE_TEXT = """祀桌宠（SiDesktopPet）2.1.2 Windows 客户端 · 快速使用说明

【一、解压与启动（绿色免安装）】
1. 请将 SiDesktopPet-2.1.2-Windows-x64.zip 完整解压到普通文件夹（例如 D:\\SiDesktopPet），
   切勿直接在压缩包预览窗口内双击运行。
2. 双击运行主程序：Si-DesktopPet.exe。
3. v2.1.2 核心升级：
   - 修复坐姿/蹲姿腿部穿模并升级水墨金玉视觉与五种立绘头像；
   - 搭载“芯语爱伴 Soul Engine V1.2”数字人格模型与防串词知识库；
   - 智能语音陪伴：接入耳机且未打开 Apex / 彩六等本地游戏时默认同时输出语音和文本；
     支持拟声词（哼、咳咳、噗嗤等）与动作声音（执笔落墨、尾巴盖戳、推过热茶、敲桌面等）同步渲染！
4. 若弹出“Windows 已保护你的电脑”提示，点击“更多信息” -> “仍要运行”即可。

【二、三种主要模型连接方式（打开“自由聊聊”或“多模态聊聊”切换）】

方式一：自定义模型接口（适合使用学校提供的 API / 第三方 OpenAI 兼容网关）
1. 在“连接方式”下拉框选择：自定义模型接口（学校 API / 第三方）。
2. 在“自定义接口地址”填写学校或平台给出的 OpenAI 兼容地址（如 https://.../v1）。
3. 在“API Key / 访问口令”填写 API Key / Token（仅保留在当前内存，绝不写盘）。
4. 在“模型”框填写模型名称，或点击右侧“获取模型”自动读取列表。

方式二：DeepSeek 官方 API（无需显卡、无需安装大模型）
1. 前往 DeepSeek 开放平台（https://platform.deepseek.com）创建 API Key（sk-...）。
2. 在“连接方式”选择：DeepSeek 官方 API，接口自动填为 https://api.deepseek.com/v1 ，默认模型 deepseek-chat。
3. 粘贴 API Key 后即可纯文字对话（Key 仅留内存，下次重开需重新填入）。

方式三：本机模型（Ollama / LM Studio 等，完全离线零费用）
1. 在本电脑启动 Ollama（http://127.0.0.1:11434/v1）或 LM Studio（http://127.0.0.1:1234/v1）。
2. 在“连接方式”选择：本机模型（Ollama / 本地端口），默认适配 si-qwen2.5-3b-lang-lora:latest（多模态带图自动切 si-qwen2.5vl-3b-lora:latest）。

【三、智能语音与日常存档】
- 本机安装 VoiceStudio（含声线 06332768）时，接入耳机且未运行本地竞技游戏（Apex、彩六、CS2、瓦罗兰特、永劫等）会自动开启“语音+文本同步输出”，外放或开游戏自动静音仅显示文本。
- 找回桌宠：托盘右键选择“找回祀”，或按快捷键 Ctrl+Alt+S。
- 所有本地对话与陪伴记录保存在同目录 data/ 下；升级覆盖前请先完全退出旧版并备份 data/ 目录。
"""

USER_GUIDE_MD = """# 祀桌宠（SiDesktopPet）2.1.2 客户端安装与使用指南

本软件为 **Windows 10 / 11（64 位）绿色便携版**，解压即用。**v2.1.2** 完成了 3D 坐/蹲防穿模与水墨金玉美术精修、**Soul Engine V1.2 数字人格**升级，以及 **耳机检测 + 本地游戏避让智能 TTS（含拟声词与动作音效）**。

---

## 一、安装与启动

1. **完整解压**：将 `SiDesktopPet-2.1.2-Windows-x64.zip` 完整解压到非系统受限目录（如 `D:\\SiDesktopPet`）。
2. **启动程序**：双击 **`Si-DesktopPet.exe`**；如遇 SmartScreen 提示，点击 **“更多信息” → “仍要运行”**。
3. **主题适配**：主面板、聊天气泡、可折叠配置抽屉及右键菜单自动随 Windows **浅色 / 深色模式**切换。
4. **找回与退出**：右键桌宠或右下角系统托盘图标可呼出面板、找回桌宠（快捷键 `Ctrl + Alt + S`）或退出。

---

## 二、智能语音陪伴（耳机感知 + 游戏避让 + 拟声词与动作声）

- **默认策略**：检测到 Windows 默认音频输出为**耳机 / 耳麦**，且未运行 **Apex、彩虹六号、CS2、瓦罗兰特、永劫无间**等本地游戏时，默认**同时输出语音与文本**；切换回显示器/扬声器外放或启动游戏时自动静音（仅输出文本），绝不干扰游戏听声辨位。
- **拟声词与动作音效**：完整保留台词中的 `哼`、`咳咳`、`噗嗤`、`唉` 等拟声词，并将 `（执笔落墨）`、`（尾巴盖戳）`、`（推过热茶）`、`（指尖敲桌面）` 等神态动作实时渲染为清脆自然的动作音效与语音无缝拼接。

---

## 三、配置模型连接（按需选择）

在面板右侧点击 **“自由聊聊（文字）”** 或 **“多模态聊聊（图文）”**：

1. **方式 A：DeepSeek 官方 API** — 选择 `DeepSeek 官方 API`，粘贴 `sk-...` Key 即可免部署对话（Key 仅留内存）。
2. **方式 B：自定义模型接口（学校 API / 第三方）** — 填入 OpenAI 兼容 Base URL 与 Key，点击“获取模型”即可。
3. **方式 C：本机模型（Ollama / LM Studio）** — 默认连接 `http://127.0.0.1:11434/v1`（文字 `si-qwen2.5-3b-lang-lora:latest`，带图自动切换 `si-qwen2.5vl-3b-lora:latest`）。

---

## 四、隐私与跨端同步

- 所有对话历史、亲密度与设置仅保存于同目录 **`data/`** 中，API Key 仅留在内存，绝不写盘。
- 使用 `--sync-preview` 启动可开启多设备事件账本（`settings.sync-v1.json`）与局域网/Web 实时陪伴记录同步。
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

    with tempfile.TemporaryDirectory(dir=RELEASE, prefix='.stage-212-') as tmpdir:
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
        (RELEASE / 'Windows-当前可用版本.txt').write_text(
            f'{portable_zip.name}\nSHA-256: pending\n', encoding='utf-8'
        )
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
    zip_sha = digest(portable_zip)
    sha_data = {
        portable_zip.name: {
            'bytes': portable_zip.stat().st_size,
            'sha256': zip_sha,
            'entries': entry_count,
        }
    }
    sha_file.write_text(json.dumps(sha_data, ensure_ascii=False, indent=2), encoding='utf-8')
    (RELEASE / 'Windows-当前可用版本.txt').write_text(
        f'{portable_zip.name}\nSHA-256: {zip_sha}\n', encoding='utf-8'
    )
    print(json.dumps({
        'version': VERSION,
        'portable_dir': str(portable_dir),
        'portable_zip': str(portable_zip),
        'sha_file': str(sha_file),
        'zip_entries': entry_count,
        'zip_bytes': portable_zip.stat().st_size,
        'zip_sha256': zip_sha,
        'smoke_test': smoke,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
