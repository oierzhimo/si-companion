# 祀 · 独立桌面伙伴

以《明日方舟：终末地》的祀为形象的 Windows 同人桌宠。基础陪伴可离线运行，默认 **2 倍**显示，附完整源码、动画素材、构建脚本和免安装便携包；自由对话可按需连接已登录的学校 GenAI 反代。

## 直接运行

1. 国内模型自由对话预览：使用 `release/SiDesktopPet-2.0.0a8-Windows-x64.zip`，完整解压后双击 `Si-DesktopPet.exe`，从首页进入“自由聊聊”。默认 DeepSeek-V4.1-Flash，可选 GLM-5.3-Flash；需本机 GenAI 反代已启动并登录。原 66 句情境短句、安静停靠、全屏隐藏和可选语音继续保留。局域网协同另用 `Start-Sync-Preview.cmd` 启动。a3/a4 旧账本的升级限制见 [局域网说明](docs/LAN_SYNC_PREVIEW.md)。a7 及所有旧版保留。
2. 旧稳定版：`release/SiDesktopPet-1.2.0-Windows-x64.zip`；旧互动预览：`release/SiDesktopPet-1.2.2-InteractionPreview-Windows-x64.zip`。
3. **先完整解压**到可写文件夹。不要只复制 EXE，也不要在压缩包预览中启动。
4. 基础离线陪伴不需要 Python、Java、Codex 或网络。2.0.0a8 自由聊聊通过本机 `127.0.0.1:8000` 的学校 GenAI 反代使用国内开源模型；反代需单独部署，学校账号由使用者本人登录。真实模型与源码界面检查结果见下文，最终归档核验以独立发布报告为准。

2.0.0a2 保留了现有互动和可选的本机语音、手动事件交换，并加固了启动日志路径。它不是 1.3 形象/动画完成版。所有旧包保留；升级时请解压到新文件夹，再按下方说明迁移旧 `data/`。详见 [2.0.0a2 说明](docs/CHANGELOG-2.0.0a2-Windows.md)。

程序首次启动会显示陪伴面板与桌宠。关闭陪伴面板后，桌宠继续运行；右键桌宠或系统托盘图标可退出。数据默认保存在 EXE 旁的 `data/` 文件夹，可随整个文件夹一起搬走。若程序位置不可写，会使用当前用户的 `%LOCALAPPDATA%/SiDesktopPet`。

## 2.0.0a7 自然陪伴台词与跨端一致

- Windows 本地短句覆盖摸头、点心、专注开始与结束、找回、日常问候、创作、卡住、疲惫、聊画和安静陪伴，共 11 类、每类 6 句、合计 66 句。全部为本项目原创角色文本，不声称看见屏幕或知道管理员的实际工作状态。
- `Companion.choose()` 对每个情境使用独立洗牌袋：一轮内不重复，跨轮不立即重复，不同情境互不干扰；保留可注入随机源的测试接口。
- Android 与鸿蒙的 `lines.json` 已和 Windows 台词表逐字一致，两端的 `line(kind)` 也使用按情境洗牌轮换。Android 0.1.2 已完成部分界面与悬浮真机检查；0.1.3 又在 V2564A / Android 16 与一台 PC 的私网直连中通过原生 TLS 配对、双向交换、重复同步、重开恢复与错误证书拒绝。两台物理 PC、冻结 Windows 界面与旧存档的端到端组合、鸿蒙平板仍未验收。
- 语音功能仍默认关闭；启用时使用的默认声线仍是当前 VoiceStudio 声线 A。另一条 B 只是在本机做的实验试听；其原档案引用 MP3，当前引擎会返回 HTTP 500，因此没有加入桌宠的可选声线。试听文件留在开发机的 `qa/` 目录，不随便携包或源码包分发。
- 1.3 立绘 v18 仍待管理员确认，运行时继续使用旧动作图集。本版不包含形象修复，也没有把 v18 接入动画。
- 局域网协议、事件账本和存档格式在本版未改动；a3/a4 已生成账本的升级限制保持不变。第二台物理 PC、安卓真机和鸿蒙平板仍未完成协同验收。

2.0.0a7 的发布验证由主协作者独立完成（2026-10-26）：Windows 完整 103 项 unittest 和 33 项 Qt 互动集成检查通过；PyInstaller 6.22.3 构建的便携包与源码包通过 ZIP 完整性和内容边界检查；将 Python 从 PATH 移除后，异地解压的 EXE 在普通与 `--sync-preview` 两种模式下均启动并报告 `2.0.0a7`，后者完成本机 TLS 配对与事件交换；对话界面构造、面板渲染和日志均未发现未处理异常。另将便携 ZIP 解压至 `F:\si_accompany\SiDesktopPet-2.0.0a7-Windows-x64`，其 EXE `--smoke-test` 输出 `ok`。Android 8 个 Java 源文件编译通过，鸿蒙适配器通过 OpenHarmony 公开 API 的 TypeScript 严格检查；两者都未打 APK/HAP，也未在真机运行。报告为 `qa/release-2.0.0a7-verification.json`；详见 [2.0.0a7 说明](docs/CHANGELOG-2.0.0a7.md) 和 [验证记录](docs/VALIDATION.md)。

## 2.0.0a8 国内开源模型自由对话预览

> 2026-10-27，主协作者已独立通过 132 项 Python 测试、六轮真实 DeepSeek 合成对话及源码 Windows Qt 界面的真实获取模型/发送/保存检查，且重新构建了 a8。归档和异地冻结核验由 `tools/verify_windows_release_2a8.py` 独立执行，实际结论记录在 `qa/release-2.0.0a8-verification.json` 和发行核验报告；源码界面通过不等于冻结 EXE 界面已真实发送，长期人设质量仍未定稿。

- 自由聊聊新增“上科大 GenAI 反代”预设：明确点击“获取模型”读取本机 `/v1/models`，再点击“发送”调用 `/v1/chat/completions`。窗口打开时不会自动获取模型、自动发送或发送空测试消息。反代仍在本机 `http://127.0.0.1:8000/v1`。
- 新对话默认进入 GenAI 预设并使用国内开源模型 **DeepSeek-V4.1-Flash**，可手动切换为 **GLM-5.3-Flash**；GenAI 预设只列出这两个已核对官方模型卡的模型，不接受其他型号，也不接受 GPT。两家的官方模型卡已核对为 MIT 许可。普通本机预设仍兼容自部署的 OpenAI-compatible 模型名称，按当前实现只拒绝明显带 GPT/OpenAI/ChatGPT 命名的型号，并不核验其他名称是否确属国产开源模型。
- 反代背后的实际模型权重、路由权重和运行环境由学校提供；桌宠和发行包都不包含模型权重、反代程序或学校账号。它不是本机已下载数百 B 参数权重的本地模型。
- 学校登录由使用者本人完成；登录凭据不进入桌宠、`conversation.json`、日志、同步账本或发行包。`sk-shanghaitech-genai` 只是上游文档公开的本机反代默认访问口令，不是学校登录凭据。
- 新对话默认使用 GenAI 预设与 DeepSeek；旧对话和偏好保留，打开时不改原设置文件。旧 GenAI 的 GPT/未核验型号在窗口内改用 DeepSeek，普通本机的 GPT 型号在窗口内清空；手工填写 GPT 也会在保存和联网前被拒绝。
- 反代访问口令只保留在进程内存，不写入 `conversation.json`、同步账本或日志。
- 当前仍是旧角色图集；新的实机抠图仍在修整，未进入 runtime。`art-source/` 是本机图稿与截图修整候选的工作目录，尚未定稿，保留在本机，不进入源码发行包。语音仍默认关闭，默认声线仍是 A。
- Android 0.1.3 已安装，并完成单手机单 PC 的原生 TLS 部分实测；双 PC 与 Harmony 仍未验收。
- 升级：先退出旧版，把整个旧 `data/` 复制到新版本目录；保留旧目录。不要只复制 EXE。
- 源码中的客户端适配、可选 Bearer 头、`fetch_models`、`provider` 字段和两个国内模型常量存在，只证明功能已装入源码，**不证明真实模型可用、回复质量或时延**。`qa/genai-live-v1`、`qa/genai-live-v2` 与 `qa/genai-ui-live-v1` 是 GPT 时期的历史测试，不能作为新模型的通过证据。
- 新版真实验收读取 `qa/genai-live-v3/report.json`（`kind=genai-live-v3-synthetic-acceptance`）：必须 `technical_roundtrip_verified=true`、`status=ok`、`model=DeepSeek-V4.1-Flash`、`completed_rounds=total_turns=6`，`rounds` 实际为 6 轮且每轮 `persisted=true`、回复非空，`reload_consistent=true`，并且 `production_modules` 记录的 `src/si_pet/conversation.py` 哈希与当前源码一致。
- 另读取 `qa/genai-ui-live-v2/report.json` 作为补充：`status=ok`、模型为 DeepSeek、`qt_send_to_real_model_verified=true`，且 `source_hashes` 中 `conversation.py`、`conversation_ui.py` 的哈希与当前源码一致。它只证明**源码 Qt 界面经真实服务发送**，不是冻结 EXE 的界面真实发送；冻结 UI 的 `conversation_ui_send_exercised` 仍为 false，另以 `source_ui_real_send_verified` 单独记录源码界面证据。
- 两份报告均已由主协作者实际生成：六轮回复和重载一致，源码界面真实发送成功；所记源码哈希与本次构建一致。验证脚本仍会检查报告内容与哈希，任一缺失或不符便拒绝交付，不改写 live 报告。两者均未把长期人设质量自动标为通过。

## 怎么和她相处

| 操作 | 回应 |
| --- | --- |
| 单击身体 / 摸摸头 | 戳一戳、招手与角色风格短句，增加亲密度 |
| 按住拖拽 | 拖到桌面、窗口上方或扩展显示器 |
| 松开鼠标 | 受重力自然下落，保留有限的投掷惯性；落到窗口顶边或桌面下边界 |
| 双击 | 在落脚面上轻轻跳起 |
| 右键 | 散步、坐下、躺下、招手、跳跃、灵光一闪，以及行为开关 |
| 送一份点心 | 专属短句、心情与亲密度增加；有投喂冷却 |
| 和祀说一句 | 五个明确话题：想做点新东西、有点卡住了、想休息一会儿、想听你聊画、先安静陪我；只回应主动点选 |
| 自由聊聊 · 本机模型 | 可选接口，记录最近12轮与填写的管理员偏好；未连接时保留输入并显示错误，不假装回答 |
| 全屏应用时自动隐藏 | 同屏全屏时隐藏桌宠和气泡，退出后恢复；可在设置关闭，保留手动隐藏选择 |
| 先安静陪我 | 靠边、坐下、降低不透明度；关闭“安静陪伴”会释放这次靠边停留 |
| 陪我专注 | 本地倒计时；结束时桌面提示并记录一次专注 |
| 鼠标穿透 | 鼠标操作穿过桌宠；从托盘“找回祀”恢复 |
| 手动模式 | 桌宠获得焦点后，← → 移动，↑ ↓ 切换动作，空格跳跃，Esc 返回自动 |
| 找回桌宠 | 回到主显示器、结束当前专注、取消鼠标穿透，并恢复可见状态 |
| 靠边坐好 | 移到祀所在屏幕右边，保持坐姿直到下次主动操作 |
| 安静陪伴 | 暂停自主散步，降低桌宠不透明度 |
| 专注计时 | 自动靠边并允许鼠标穿过桌宠，结束后恢复 |

系统允许注册时，还可使用 `Ctrl+Alt+S` 找回桌宠。快捷键被占用或受系统限制时，托盘找回始终是主要入口。

短句为本项目原创的角色风格文本，**不是游戏官方台词，也不是联网 AI 对话**。桌宠以“管理员”为称呼，采用较低打扰频率，并避免连续重复同一句。稳定版 1.2.0 不包含语音、后台收集或在线账号。

`release/SiDesktopPet-1.2.1-VoicePreview-Windows-x64.zip` 是单独的语音预览包：需要本机已有 VoiceStudio 环境，语音默认关闭，可在“行为与显示”中连接引擎、启用语音并试听。稳定版 1.2.0 保持原样；预览包仍使用旧动作图集。

1.2.2 互动预览新增“和祀说一句”的五个明确话题。它只在用户点选后回应，不会根据窗口标题、截图或键盘内容推断你在做什么；选择“先安静陪我”后关闭“安静陪伴”会释放靠边停留，不会留下无法解除的停靠状态。1.2.2 仍使用旧动作图集；`art-source/v1.3/si-standing-candidate-v12.png` 是当前站立造型基准，面部尚未定稿，未接入运行时。版本变化与安装说明见 [1.2.2 互动预览说明](docs/CHANGELOG-1.2.2-InteractionPreview.md)。

## 2.0.0a1 Windows 手动协同预览

HarmonyOS 5 原生工程源码预览位于 `mobile/harmony/`，归档为 `release/SiCompanion-Harmony-0.1.0-ProjectPreview.zip`。协议核心与 PC 互测及公开 API 签名对照已通过；本机缺少 HarmonyOS SDK，官方工具下载页要求登录，因此尚未编译 ArkUI 页面或生成 HAP。不能作为可安装客户端交付。详见 [鸿蒙工程边界](mobile/harmony/README.md)。

Android 当前推荐预览为 `release/SiCompanion-Android-0.1.3-preview.apk`，对应源码 `release/SiCompanion-Android-0.1.3-Source.zip`。已安装到 V2564A / Android 16，新增系统分享接收邀请（仍需点击连接），并完成一台手机与一台 PC 同步核心的私网直连验证；报告见 `release/VERIFIED-Android-0.1.3.json`。0.1.2 保留此前的基本界面和悬浮验证；旧 0.1.0/0.1.1 的图片路径在真机失败，不建议安装。当前仍使用旧角色图集，完整多 PC、冻结 Windows 界面与旧存档组合、鸿蒙及断网/后台组合尚未验收。使用方式与边界见 [安卓说明](mobile/android/README.md)。本 APK 不适用于原生鸿蒙 5。

2.0.0a3 新增可选的局域网配对与 TLS 事件交换，入口为协同预览模式下的“相伴手记 → 局域网配对与同步”。它已通过同机两份存档的真实网络与 Qt 联动检查，以及冻结 EXE 的配对与交换检查。尚未验证第二台物理电脑。详见 [局域网协同说明](docs/LAN_SYNC_PREVIEW.md)。

便携预览包解压后使用 `Start-Sync-Preview.cmd` 启动；源码可使用 `main.py --sync-preview`。首次启用会备份旧 `settings.json`，并建立独立的事件账本；摸头、点心、专注完成和相伴手记会写入账本。面板“相伴手记”页可手动导出、导入事件包，合并后更新亲密度、次数和手记，本机缩放与桌宠位置保持独立。迁移前已分叉的旧存档会拒绝自动合并，以免重复累计。详见 [2.0 同步协议与边界](docs/SYNC_PROTOCOL-2.0.md) 和 [预览版说明](docs/CHANGELOG-2.0.0a1-ManualSyncPreview.md)。

该预览尚未完成自动配对、两台物理 PC 验证、Android 客户端或鸿蒙 5 客户端；1.2.2 便携包不包含协同功能。

## 已实现的行为

- 自主行走、随机停留、坐下、躺下休息、特殊小动作。
- 戳一戳、招手、跳跃、十六方向眼神跟随。
- 平面重力、自由落体、拖拽惯性、轻微落地回弹。
- 桌面下边界与普通窗口顶边落脚；支撑窗口移动时跟随，窗口关闭或最小化后继续下落。
- 跨显示器拖拽，负坐标布局，显示器移除后找回。
- 右键菜单与系统托盘、手动模式、鼠标穿透、置顶、缩放、速度、重力、帧率、下边界调节。
- 可选择的 Windows 登录自启，默认关闭。
- 亲密度、心情、投喂、陪伴手记和专注计时。

完整对照见 [Ark-Pets 行为对照](docs/BEHAVIOR_PARITY.md)。

## 显示与边界

默认一帧的逻辑尺寸为 384×416，是原始 192×208 的两倍；角色本身保留透明留白。系统缩放由 Qt 处理。可在“行为与显示”中调为 0.75～3 倍。

普通可见窗口可以成为平台。独占全屏、UAC/锁屏安全桌面、被完全遮挡的顶边及部分特殊系统窗口不会被视为可停留平台。平台只模拟窗口顶边，不模拟复杂三维碰撞或钻入窗口内部。

程序不会修改其他应用。窗口识别只使用本机窗口几何与可见状态，数据不会联网发送。

## 从源码运行与构建

开发环境：Windows x64、Python 3.12。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe tools\prepare_assets.py
.\.venv\Scripts\python.exe main.py
```

1.2.2 使用独立入口，构建输出放在 `dist-1.2.2/` 和 `build-1.2.2/`，发布文件也使用独立名称：

```powershell
.\.venv\Scripts\python.exe tools\build_interaction_preview.py
```

也可以运行 `.\build_interaction_preview.ps1`。该入口依次执行单元测试、Qt 集成检查、高清与居中检查、窗口实机检查、PyInstaller、1.2.2 打包及异地解压校验。构建阶段需要联网安装依赖，发布程序不需要网络。

2.0.0a7 使用独立输出目录，避免覆盖旧包或旧构建缓存：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
.\.venv\Scripts\python.exe tests\integration_check.py
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --distpath dist-2.0a7 --workpath build-2.0a7 SiDesktopPet.spec
.\.venv\Scripts\python.exe tools\make_windows_release_2a7.py
.\.venv\Scripts\python.exe tools\verify_windows_release_2a7.py
```

打包脚本要求源码版本为 `2.0.0a7` 且构建产物位于 `dist-2.0a7/SiDesktopPet/`，并复核已有 ZIP 哈希未变化；源码包会排除 `.venv`、`build-2.0a7`、`dist-2.0a7`、`build-2.0a*`、`dist-2.0a*`、`release` 和 `qa`，因此不会带上试听音频。验证脚本会检查 ZIP 完整性、源码包边界、移除 Python PATH 后的普通/协同启动、对话窗口构造和面板渲染，报告写入 `qa/release-2.0.0a7-verification.json`。

2.0.0a8 的独立工具已按“默认国内开源模型”要求更新，但**本次没有执行构建、打包或验证**。用户模型要求已变化，之前为 a8 构建的临时 `dist-2.0a8` 不能视为合格产物，必须重新构建；以下命令留待主协作者在真实检查时运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
.\.venv\Scripts\python.exe tests\integration_check.py
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --distpath dist-2.0a8 --workpath build-2.0a8 SiDesktopPet.spec
.\.venv\Scripts\python.exe tools\make_windows_release_2a8.py
.\.venv\Scripts\python.exe tools\verify_windows_release_2a8.py
```

`make_windows_release_2a8.py` 要求源码版本为 `2.0.0a8` 且构建产物位于 `dist-2.0a8/SiDesktopPet/`，生成新的便携 ZIP、源码 ZIP 和 `SHA256SUMS-2.0.0a8.json`；同名文件已存在时会拒绝覆盖，并复核 `release/` 下所有既有 ZIP/APK 的哈希未变化。源码包会通用排除所有 `build-*`/`dist-*`、`qa`、`data`、`.venv`、外部工具、试听音频、`.claude`、`token*.txt`、`credentials.txt` 和 `.env`，也会排除本机 `art-source/` 图稿与截图修整候选；它不会删除本机目录，并拒绝符号链接或越界路径。脚本同时要求源码中的默认模型为 `DeepSeek-V4.1-Flash`、可选 `GLM-5.3-Flash`。

`verify_windows_release_2a8.py` 会异地解压新便携包、移除 Python PATH 后启动普通/协同模式、检查本机 TLS 配对与交换，并从冻结包 PYZ 独立读取 `si_pet.conversation` / `si_pet.conversation_ui` 的模块 API：只在内存中执行随包的对话模块，校验可选 Bearer 头、`fetch_models`、`request_reply`、`provider`、预设及两个国内模型常量，不联网、不改应用源码；该检查不通过时验证直接失败。它明确记录是否构造对话 UI，但常规 smoke **只证明启动和对话窗口构造，不等于新 UI 真实发送**。真实结论由主协作者另跑新版六轮 DeepSeek 测试和源码 Qt 界面真实发送测试：验证脚本读取 `qa/genai-live-v3/report.json`（`kind=genai-live-v3-synthetic-acceptance`）的六轮、模型、哈希与 `technical_roundtrip_verified`，并读取 `qa/genai-ui-live-v2/report.json` 的 `qt_send_to_real_model_verified` 及两个源码哈希；旧的 GPT v1/v2 与 UI v1 报告不能替代。源码界面报告只证明源码 Qt 界面真实发送，冻结 EXE 的 `conversation_ui_send_exercised` 仍为 false，另以 `source_ui_real_send_verified` 区分。任一份报告缺失或不符时以非零状态拒绝最终交付，且不会改写 live 报告。验证报告写入新的 `qa/release-2.0.0a8-verification.json`，已存在时拒绝覆盖；如需重跑，请用 `--report qa/release-2.0.0a8-verification-2.json` 指定新的报告路径。验证脚本拒绝把报告写进任何 `qa/genai-live*/` 或 `qa/genai-ui-live*/` 目录，不只保护 v1。

不要用旧的 `build_windows.ps1`、`tools/make_release.py` 或 `tools/finalize_release.py` 来生成 1.2.2、2.0.0a7 或 2.0.0a8：它们仍是 1.2.0 稳定包的入口，会写入稳定包名。需要保留现有 1.2.0/1.2.1 压缩包时，请使用上面的独立入口；打包脚本也会复核旧包哈希未变化。

如 PowerShell 的本机策略不允许运行脚本，可直接按 `tools\build_interaction_preview.py` 中的顺序逐项执行，无需修改系统全局策略。

## 项目结构

```text
main.py                   启动入口
src/si_pet/               界面、动作调度、物理、系统集成与存档
assets/                   发布程序实际读取的动作图集、帧与清单
art-source/               本机图稿与截图修整候选，未定稿，不进入发行包
tools/                    素材准备、各版本打包与便携校验脚本
tests/                    物理、存档与 Qt 交互检查
docs/                     行为对照、版本说明与验证记录
licenses/                 第三方许可证
SiDesktopPet.spec         Windows 可执行程序打包配置
build_interaction_preview.ps1  1.2.2 互动预览独立构建入口
build_windows.ps1         旧版 1.2.0 稳定包构建入口
```

## 署名与许可

行为设计参考 [isHarryh/Ark-Pets](https://github.com/isHarryh/Ark-Pets)。本项目独立实现祀专属行为与界面，不需要安装 Ark-Pets，也不包含其社区模型库。

本项目代码采用 GPL-3.0，详见 `LICENSE`。PySide6、Qt、Python 等组件的说明见 `THIRD_PARTY_NOTICES.md` 和 `licenses/`。Qt 动态库保留在发布包的 `_internal/` 中，可以使用兼容版本替换并重新构建。

“祀”、《明日方舟：终末地》及相关角色设计属于原权利人。本项目为非官方同人作品；代码许可证不授予角色原作权利。
