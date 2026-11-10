# 小祀 · 跨设备桌宠

小祀是面向 Windows、Android 和 HarmonyOS 的桌面伙伴。她可以安静陪伴，在用户召唤或发送内容时自然回应。产品目标是在不同设备上接续同一会话，并根据文字、图片、文件和语音内容给出适合的回复。QQ 原型提供附件处理、模型调用、媒体生成和发送确认的参考实现。

**接手后的第一项交付是在 Windows 桌宠的「自由聊聊」里跑通多模态收发。** 随后验证 Windows 和 Android 的跨端会话与媒体同步，再扩展到第二台 PC 和 HarmonyOS。

## 工程入口

| 目录 | 交接基线与职责 |
|---|---|
| [si-desktop-pet](si-desktop-pet/) | Windows 2.0.0a9。PySide6 应用、桌宠行为、自由聊聊、存储和局域网配对。入口为 `main.py` |
| [mobile/android](si-desktop-pet/mobile/android/) | Android 0.1.3-preview。原生 Java 客户端、悬浮与事件交换 |
| [mobile/harmony](si-desktop-pet/mobile/harmony/) | HarmonyOS 0.1.0 工程预览。ArkTS 界面与 TypeScript 协议实现 |
| [si-qq-bot](si-qq-bot/) | QQ 0.2.5。多模态交互原型、OneBot 适配与回归入口。入口为 `main.py` |
| [candidates/desktop-a10](candidates/desktop-a10/) | Windows 2.0.0a10 隔离候选。包含六个叠加文件、基线哈希与复现说明 |
| [si-language-lab](si-language-lab/) | 人设、训练样本和留出题的实验区 |

Windows 会话界面在 `si-desktop-pet/src/si_pet/conversation_ui.py`，会话状态和模型调用在同目录的 `conversation.py`。对照 QQ 的 `media_support.py`、`image_prepare.py`、`model_router.py`、`service_reply.py`，从附件准备到媒体回复追踪一条完整调用链。通用能力可由桌宠和 QQ 共享，OneBot 登录、群聊与消息段的转换留在 QQ 适配器。两个 Python 工程都有 `si_pet` 命名空间，运行时分别进入各自工程根目录。

现有同步账本可交换互动事件、手记和有限偏好。跨端聊天和附件是下一阶段的开发工作，可基于已有配对、TLS 指纹校验和事件交换机制。架构、文件索引和协议边界见 [项目介绍](docs/PROJECT.md) 与 [同步协议](si-desktop-pet/docs/SYNC_PROTOCOL-2.0.md)。

## Windows 快速验证

用 Python 3.12 和 PowerShell，从仓库根目录安装依赖。以下启动检查使用临时数据目录，可在无 QQ 账号、无模型的环境运行。

```powershell
py -3.12 -m venv .venv
$siPython = (Resolve-Path .\.venv\Scripts\python.exe).Path
& $siPython -m pip install -r .\si-desktop-pet\requirements-build.txt -r .\si-qq-bot\requirements.txt

$siProbe = Join-Path ([System.IO.Path]::GetTempPath()) ('si-handoff-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path "$siProbe\Local", "$siProbe\Roaming" | Out-Null
$env:LOCALAPPDATA = "$siProbe\Local"
$env:APPDATA = "$siProbe\Roaming"
$env:QT_QPA_PLATFORM = 'offscreen'

Push-Location .\si-desktop-pet
& $siPython main.py --smoke-test "$siProbe\desktop.json" --sync-preview --check-lan
Pop-Location

Push-Location .\si-qq-bot
& $siPython main.py --smoke-test "$siProbe\qq.png"
Pop-Location
```

两个结果文件都写到 `$siProbe`。需要观察实际桌宠窗口时，在保留临时数据目录的测试终端里清除 `QT_QPA_PLATFORM`，进入 `si-desktop-pet`，执行 `& $siPython main.py`。桌宠和 QQ 的测试命令、打包入口、移动 SDK 与本地服务配置见 [接手指南](docs/TAKEOVER.md)。

本地媒体联调按需配置 Ollama、VoiceStudio 和 ComfyUI。NapCat / OneBot 用于 QQ 适配测试。安装签名、账号连接与真实消息发送使用接手者单独配置的测试环境。

## 基线证据与验收边界

| 检查 | 2026-11-10 交接复验 |
|---|---|
| Windows a9 | 离屏启动与本机 TLS 交换通过。223 项单测中 221 项通过、1 项跳过。`test_genai_status_widget` 有一项 Qt 枚举兼容性失败 |
| Windows a10 | 叠加候选后 227 项通过、1 项跳过。真实前台全屏、恢复和拖拽仍待验收 |
| QQ 0.2.5 | 444 项单测通过，源码与冻结程序的离屏启动检查通过。实际媒体接收与播放按用户端结果单独验收 |
| Android | Java/Python TLS 回环通过。隔离副本以临时签名构建并验签。真机安装及跨端媒体留待下一轮 |
| HarmonyOS | TypeScript 协议回环和公开接口声明检查通过。商用 SDK 构建、HAP 和平板真机仍待验证 |

具体命令、范围和已知例外见 [复验记录](docs/VERIFICATION.md)。a10 的复现方式见 [候选说明](candidates/desktop-a10/README.md)。

## 交给 Codex 的第一个任务

在接手者自己的 Codex 中打开克隆目录，发送下面这段话。完整验收用例见 [首轮开发任务](docs/FIRST_TASK.md)。

> 先读 README、`docs/PROJECT.md`、`docs/TAKEOVER.md`、`docs/FIRST_TASK.md` 和 `docs/VERIFICATION.md`，在隔离环境复现 Windows a9 启动与现有测试基线。沿「自由聊聊」的正常发送入口，实现最小的通用消息、附件与回复链路，先用替身和合成附件验证成功、失败、重试以及旧纯文本历史。保持 OneBot 接入留在 QQ 适配层。完成后列出本次确实运行的测试、可在桌宠界面复验的操作、真实模型或设备仍需验证的部分。a10 候选按独立任务验收。

[可直接交给 Codex 的开工说明](CODEX_START.md)

## 资源与数据

运行素材和复验所需的测试夹具随源码交接。模型权重、账号状态、聊天记录、实际同步账本、签名密钥和原始游戏截图由各自环境保管。现有角色素材的来源及第三方许可见 [素材署名](si-desktop-pet/art-source/SOURCES.md) 与 [第三方许可](si-desktop-pet/THIRD_PARTY_NOTICES.md)。
