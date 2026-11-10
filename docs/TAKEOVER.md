# 接手指南

## 1. 在解压副本中建立开发环境

在 Windows 上用 Python 3.12 建一个独立环境。交接前的实际验证使用 Python 3.12.14、PySide6-Essentials 6.12.0、Pillow 12.3.0、cryptography 50.0.2、PyInstaller 6.22.3、websockets 15.0.1 和 pypdf 6.21.4。以下命令从解压后的交接目录执行。

```powershell
py -3.12 -m venv .venv
$siPython = (Resolve-Path .\.venv\Scripts\python.exe).Path
& $siPython -m pip install -r .\si-desktop-pet\requirements-build.txt -r .\si-qq-bot\requirements.txt
```

若机器通过独立安装路径提供 Python，使用该 Python 执行 `-m venv`。运行和测试时分别进入对应工程根目录。两个程序各有同名 Python 模块，保持工程目录和导入路径清晰。

初次验证使用独立数据目录。以下变量只影响当前 PowerShell 及其子进程。

```powershell
$siProbe = Join-Path ([System.IO.Path]::GetTempPath()) ('si-handoff-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path "$siProbe\Local", "$siProbe\Roaming" | Out-Null
$env:LOCALAPPDATA = "$siProbe\Local"
$env:APPDATA = "$siProbe\Roaming"
$env:QT_QPA_PLATFORM = 'offscreen'

Push-Location .\si-desktop-pet
& $siPython -m unittest discover -s tests -p 'test_*.py'
& $siPython main.py --smoke-test "$siProbe\desktop.json" --sync-preview --check-lan
Pop-Location

Push-Location .\si-qq-bot
& $siPython -m unittest discover -s tests
& $siPython main.py --smoke-test "$siProbe\qq.png"
Pop-Location
```

Qt 离屏启动和本机 TLS 互测可在无账号、无模型时运行。需要看真实窗口或原生全屏效果时，在测试终端清掉 `QT_QPA_PLATFORM` 后进入工程执行 `main.py`。Windows 普通启动为 `python main.py`，手动同步预览为 `python main.py --sync-preview`。保留隔离数据路径直至确认可用。在新终端恢复原环境。

## 2. 使用本地模型和媒体服务

| 本机服务 | 交接时使用的接口 | 接手注意点 |
|---|---|---|
| Ollama | `http://127.0.0.1:11434/v1` | 原机器模型目录 `E:\Ollama\models`。当前 QQ 路由默认 `si-qwen3.5:9b`，对应 Qwen3.5 9B Q4_K_M 本地别名，先核对 `ollama list` 与 GUI 选择。旧 `prepare_ollama_profile.py` 建立的是 Qwen2.5-VL 别名 |
| VoiceStudio | `http://127.0.0.1:3900`，`/profiles` 与 `/generate` | `voice_reply.py` 原型使用本地 profile ID `06332768`，换机时替换为实际可用的声线。冷启动可能需要等待。语音输入识别另走 ASR |
| ComfyUI | `http://127.0.0.1:8188` | 原型使用 `outbound_images.py` 中的两套 checkpoint 路径与工作流。先确认实际模型名及队列，再做端到端绘图 |
| NapCat / OneBot | `ws://127.0.0.1:3001/` | QQ 原型真机联调才需要。使用本机已经授权的独立测试账号与绑定流程 |
| 旧学校代理 | `http://127.0.0.1:8000/v1` | 兼容路径，第三方 `cgz12345/shanghaitech-genai-proxy`，原工作区 HEAD `4b70794b80704dc9baa90314149e8e04d0705260`。桌宠后续优先采用本地服务 |

QQ 联调从 `python main.py --local-router` 进入，并在“模型连接”中明确选择 Ollama。`--local-router` 只开路由。旧 `Start-LocalServices.ps1` 与 `Start-SiQQ.cmd` 记录原机器安装位置和原发布包布局，异机用源码入口并单独启动各服务。管理员绑定、授权和口令在本机配置。

Windows 的旧“自由聊聊”入口仍有学校代理路径。接手时可先用模型替身验证界面、超时和错误分支，再接 Ollama、VoiceStudio 和 ComfyUI。先检查每一项服务就绪，再跑连续的文字、语音、图片交互。GPU 资源紧张时参考 `local_resources.py` 的本地互斥与卸载流程，保持同一生成请求的状态连续。

## 3. Windows 源码构建与 a10 候选

桌宠在 `si-desktop-pet/` 运行以下命令。老 `build_windows.ps1` 面向 1.2.0 的历史发布流程。

```powershell
& $siPython -m PyInstaller --noconfirm --distpath dist-handoff --workpath build-handoff SiDesktopPet.spec
```

QQ 在 `si-qq-bot/` 使用交接包内的相对路径配方。

```powershell
& $siPython -m PyInstaller --noconfirm --distpath dist-handoff --workpath build-handoff SiQQ-Handoff.spec
```

程序目录需要整体复制，包含 PyInstaller 生成的依赖资源。用绝对路径参数启动新打包程序的 `--smoke-test`，确认启动报告、版本、运行入口和资源加载。

若要继续 a10，先复制整份 `si-desktop-pet/` 到单独目录，再按 [候选说明](../candidates/desktop-a10/README.md) 覆盖六个路径。a9 基线可作为回退和对照。正常桌面会话中，在确认测试窗口取得真实前台焦点后才判读原生全屏测试；Qt 离屏不具备这项条件。

## 4. 移动端开发

Android 使用 JDK 17、SDK API 35 与 build-tools 35。协议测试按 `mobile/android/tools/TEST_DEPENDENCY.md` 准备 `org.json` 测试 JAR，放入对应 `tools/` 目录。APK 使用 Android 自带 `org.json`。

```powershell
# 工作目录是 si-desktop-pet。以下路径换成本机实际安装位置。
$env:SI_ANDROID_TEST_JDK = '<JDK 17 根目录>'
& $siPython mobile/android/tests/run_protocol_checks.py

# 在本机预先设置 SI_ANDROID_KEYSTORE_PASSWORD、SI_ANDROID_KEY_PASSWORD。
& .\mobile\android\build-apk.ps1 `
  -JavaDirectory '<JDK 17 根目录>' `
  -BuildToolsDirectory '<Android SDK build-tools 35 目录>' `
  -AndroidJar '<Android SDK platforms/android-35/android.jar>' `
  -SigningKey '<本机开发签名文件>' `
  -SigningAlias '<签名别名>'
```

交接副本的 Android 构建脚本要求显式签名文件，并通过环境变量读密码。使用原设备升级时需要包名与原签名一致。用新密钥构建时先选择干净测试环境。保留 `NormalizeApkPaths.java` 在 `zipalign` 前的步骤，以保证 APK 资源路径正确。

HarmonyOS 使用 DevEco 打开 `mobile/harmony/`，配置 HarmonyOS 5 API 12 SDK、工程与本地签名。可先在 Windows 下用 Node 和 TypeScript 5.6.3 做协议检查。公开 API 检查使用 `openharmony/interface_sdk-js` 的 `0ebbb2136397fd2b190547bc3c2cdcf6b4ba257a` 提交下的 `api/`。这一项验证的是接口声明。

```powershell
$env:SI_NODE = '<node.exe 路径>'
$env:SI_TSC = '<TypeScript 5.6.3 的 tsc.js 路径>'
$env:SI_OH_API = '<固定公开声明版本的 api 目录>'
& $siPython mobile/harmony/tests/run_core_checks.py
& $siPython mobile/harmony/tests/check_public_api.py
```

移动端打包脚本和老说明里的 APK、HAP、QA 真机报告是已有发行流程的输入。用本次本机环境生成新的验收记录，再运行对应发行脚本。交接包附带当前资源，因此构建 APK 时可直接使用，无需先重绘角色或重生台词资源。

## 5. 资产、数据与回退

`assets/` 是桌宠运行资源，`art-source/` 包含制作素材与未采用候选。`art-source/v1.3/si-standing-candidate-v18.png` 和 `qa/v13-motion-preview-v18.png` 是历史运动预览测试夹具。072 草稿与实机参考说明用于后续视觉验收。涉及原始截图和区域遮罩的再处理脚本需在原工作区配齐其输入。

工程源码基线以交接包的逐文件清单为准。接手时可在解压副本初始化本地版本控制。分别提交基线、消息契约、桌宠接入和设备同步，便于复现与回退。旧数据保存在原设备，切换新持久化或同步格式前先在复制的存档上验证迁移。真实应用登录、设备安装和 QQ 发送各自走用户已经授权的范围。
