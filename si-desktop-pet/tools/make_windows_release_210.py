"""Package the 2.1.0 portable Windows x64 release for distribution to other PCs.

The packaged app offers two primary user-facing model connection modes out of
the box:
1. Local model (Ollama / LM Studio / local OpenAI-compatible loopback service)
2. DeepSeek Official API (https://api.deepseek.com/v1 with user-supplied key in RAM)
"""
from pathlib import Path
import argparse
import hashlib
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

VERSION = '2.1.0'
NAME = f'SiDesktopPet-{VERSION}-Windows-x64'
BUILT = ROOT / 'dist' / 'SiDesktopPet'

USER_GUIDE_TEXT = """祀桌宠（SiDesktopPet）2.1.0 Windows 客户端 · 快速使用说明

【一、解压与启动（绿色免安装）】
1. 请将 SiDesktopPet-2.1.0-Windows-x64.zip 完整解压到普通文件夹（例如 D:\\SiDesktopPet），
   切勿直接在压缩包预览窗口内双击运行。
2. 双击运行主程序：Si-DesktopPet.exe。
3. 若弹出“Windows 已保护你的电脑”提示，点击“更多信息” -> “仍要运行”即可。
4. 首次运行会弹出陪伴面板；右键桌宠或右下角系统托盘图标可呼出面板、找回桌宠或退出。

【二、两种主要连接方式（打开“自由聊聊”或“多模态聊聊”切换）】

方式一：DeepSeek 官方 API（推荐普通电脑使用，无需显卡、无需安装大模型）
1. 前往 DeepSeek 开放平台（https://platform.deepseek.com）注册登录，
   在“API Keys”页面创建一个 Key（以 sk- 开头），并确保账号内有可用余额。
2. 在桌宠面板打开“自由聊聊 · 本机 / DeepSeek API”或“多模态聊聊 · 本机 / DeepSeek / 语音”。
3. 在“连接方式”下拉框选择：DeepSeek 官方 API。
   - 接口地址自动填为：https://api.deepseek.com/v1
   - 模型默认：deepseek-chat（也可手动改为 deepseek-reasoner）
4. 将你的 DeepSeek API Key 粘贴到“DeepSeek API Key”框内，点击发送即可开始对话。
5. 注意事项：
   - API Key 仅保存在当前运行内存中，绝不会写入本地存档文件；下次重新打开软件需重新粘贴。
   - DeepSeek 官方接口当前仅支持纯文字对话；如在多模态窗口附带了图片，需先点“清除图片”再发送，或切换为本机视觉模型。

方式二：本机模型（Ollama / LM Studio 等，完全离线、零 API 费用）
1. 在本电脑安装 Ollama（https://ollama.com）或 LM Studio，并启动本地服务。
2. 在本机拉取或导入你要使用的模型：
   - 通用模型示例：在终端执行 ollama pull qwen2.5:7b
   - 若本机已导入祀微调模型，默认文字模型为 si-qwen2.5-3b-lang-lora:latest；
     在“多模态聊聊”附带图片发送时，会自动切换至视觉模型 si-qwen2.5vl-3b-lora:latest。
3. 在桌宠“连接方式”下拉框选择：本机模型（Ollama / 本地端口）。
   - Ollama 默认接口：http://127.0.0.1:11434/v1（LM Studio 为 http://127.0.0.1:1234/v1）
   - 访问口令留空，填写已加载的模型名（或点击“获取模型”自动读取）。

【三、日常功能、快捷键与存档】
- 离线互动：散步、坐下、摸摸头、送点心、专注计时（默认 25 分钟）、安静陪伴均无需联网。
- 找回桌宠：如不小心开启“鼠标穿透”，可在右下角托盘右键“找回祀”，或按快捷键 Ctrl+Alt+S。
- 存档迁移：所有陪伴记录与对话保存于同目录 data 文件夹；换电脑时复制整个文件夹即可，升级前请先完全退出旧版并备份 data。
"""

USER_GUIDE_MD = """# 祀桌宠（SiDesktopPet）2.1.0 客户端安装与使用指南

本软件为 **Windows 10 / 11（64 位）绿色便携版**，无需管理员安装或配置 Python 环境，解压后即可直接运行。提供 **DeepSeek 官方 API** 与 **本机模型** 两种连接方式，离线基础陪伴互动无需联网即可使用。

---

## 一、安装与启动

1. **完整解压**：右键压缩包 `SiDesktopPet-2.1.0-Windows-x64.zip` → 选择“全部解压缩”，放到非系统受限目录（例如 `D:\\SiDesktopPet`）。
   > 不要在压缩包预览窗口中直接双击运行，否则无法写入 `data` 存档或加载资源文件。
2. **启动程序**：进入解压后的文件夹，双击运行 **`Si-DesktopPet.exe`**。
3. **系统拦截放行（如有）**：若 Windows SmartScreen 弹出蓝框提示“Windows 已保护你的电脑”，点击 **“更多信息”** → **“仍要运行”**。
4. **基本操作**：
   - 双击桌宠可以轻轻跳起；拖动到桌面任意位置或普通窗口顶边会自动停靠。
   - 右键桌宠或系统托盘图标可随时呼出面板、切换状态或退出程序。
   - 若误启用了“鼠标穿透”，可从系统托盘图标右键选择 **“找回祀”**，或使用快捷键 **`Ctrl + Alt + S`**。

---

## 二、配置对话模型（二选一）

在主面板右侧点击 **“自由聊聊 · 本机 / DeepSeek API”** 或 **“多模态聊聊 · 本机 / DeepSeek / 语音”** 打开对话窗口。

### 方式 A：使用 DeepSeek 官方 API（最推荐，适合无独立显卡 / 不想本地部署模型）

1. **申请 API Key**：
   - 登录 DeepSeek 开放平台：`https://platform.deepseek.com`
   - 进入左侧 **API Keys** → 点击创建，复制生成的密钥（形如 `sk-xxxxxxxxxxxxxxxx`）。
   - 确认账户内有可用额度（通常充值几元即可日常对话使用很久）。
2. **在桌宠中填写**：
   - **连接方式**：选择 **`DeepSeek 官方 API`**
   - **DeepSeek 接口**：保持默认 `https://api.deepseek.com/v1`
   - **模型**：默认 `deepseek-chat`（如需深度推理可改为 `deepseek-reasoner`）
   - **DeepSeek API Key**：粘贴你的 `sk-...` 密钥
3. **点击“获取模型”测试连通性**（可选），确认无报错后输入想说的话点击 **发送**。
4. **安全与能力说明**：
   - **密钥绝不落盘**：API Key 只保留在当前运行内存中，不会写入 `data/` 下的任何配置文件；每次重新打开软件后需再次填入。
   - **纯文字限制**：DeepSeek 官方 API 目前仅支持纯文字对话；如果在“多模态聊聊”拖入/粘贴了图片，发送前请先点击“清除图片”，或切换回支持识图的本机模型。

---

### 方式 B：使用本机部署模型（Ollama / LM Studio 等，完全离线、零费用）

适合配置了显卡或已在本电脑本地运行大模型服务的用户。

1. **准备本机模型服务**（以 Ollama 为例）：
   - 下载并安装 Ollama：`https://ollama.com`
   - 打开终端拉取一个常用中文模型，例如：
     ```powershell
     ollama pull qwen2.5:7b
     ```
   - *(如果你已导入祀微调模型，默认文字模型为 `si-qwen2.5-3b-lang-lora:latest`，视觉模型为 `si-qwen2.5vl-3b-lora:latest`。)*
2. **在桌宠中填写**：
   - **连接方式**：选择 **`本机模型（Ollama / 本地端口）`**
   - **本机接口**：Ollama 默认为 `http://127.0.0.1:11434/v1`；若使用 LM Studio，一般改为 `http://127.0.0.1:1234/v1`
   - **访问口令 / Key**：留空即可
   - **模型**：填写本机已启动的模型名称（如 `qwen2.5:7b` 或 `si-qwen2.5-3b-lang-lora:latest`），也可直接点 **“获取模型”** 后在下拉补全中选择。
3. **多模态识图（仅本机 Ollama 11434）**：
   - 当你在“多模态聊聊”拖拽或 `Ctrl+V` 粘贴图片（最多 4 张）时，若当前填写的是 `si-qwen2.5-3b-lang-lora:latest`，系统会自动切换并请求本机视觉模型 `si-qwen2.5vl-3b-lora:latest`。

---

## 三、常见问题排查

| 现象 | 可能原因与解决办法 |
|---|---|
| DeepSeek 提示 **API Key 无效或余额不足** | 检查是否误复制了多余空格、Key 是否已失效，或在 DeepSeek 平台确认余额大于 0 |
| DeepSeek 提示 **无法连接 api.deepseek.com** | 检查本机是否断网；若开启了系统代理，确保代理本身工作正常 |
| 本机模型提示 **未连接或等待超时** | 确认 Ollama / LM Studio 已启动，端口号与接口地址一致（Ollama 默认 `11434`，LM Studio 默认 `1234`） |
| 切换 DeepSeek 后提示 **当前仅支持纯文字** | DeepSeek 官方接口不支持直接上传图片，请点击 **“清除图片”** 后发送文字，或切回本机模型识图 |
| 语音开关开启但无声音 | 语音需本机另行运行 VoiceStudio 服务（`127.0.0.1:3900`）；未启动时会平稳退化为纯文字回复，不影响正常聊天 |

---

## 四、数据保存与版本升级

- 本软件的所有偏好、亲密度及对话记录仅保存在解压目录内的 **`data/`** 文件夹中，不上传个人账号或云端服务器。
- **更换电脑**：将整个 `SiDesktopPet-2.1.0-Windows-x64` 文件夹打包拷走即可带走全部陪伴记录（DeepSeek API Key 不会随文件夹保存）。
- **版本升级**：升级覆盖前，请先**完全退出**正在运行的旧版桌宠，并先完整备份旧目录中的 `data/` 文件夹。
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
    """Extract ZIP into isolated qa dir and launch EXE with Python removed from PATH."""
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

    with tempfile.TemporaryDirectory(dir=RELEASE, prefix='.stage-210-') as tmpdir:
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
