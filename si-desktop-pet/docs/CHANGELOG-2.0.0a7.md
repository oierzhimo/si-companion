# 2.0.0a7 自然陪伴台词与跨端一致

- Windows 本地陪伴短句覆盖摸头、点心、专注开始、专注结束、找回、日常问候、创作、卡住、疲惫、聊画和安静陪伴 11 类情境，每类 6 句，共 66 句。句子为本项目原创角色文本，控制朗读长度，不声称看见屏幕、读取键盘或知道管理员的实际工作状态。
- `Companion.choose()` 为每个情境维护独立洗牌袋：一轮用尽前不重复，跨轮不立即重复，不同情境的轮换互不干扰；保留可注入随机源的测试接口。
- Android 与鸿蒙工程的 `lines.json` 已和 Windows 台词表逐字一致，两端 `line(kind)` 也改为同样的按情境洗牌轮换，并处理空数组与单句情况。两端只同步了台词源数据和轮换逻辑：Android APK 尚未重新安装，鸿蒙工程未取得 HAP，不能视为移动端真机验收完成。
- 语音功能仍默认关闭；启用时保持当前 VoiceStudio 声线 A。另一条 B 只是本机实验试听；其原档案引用 MP3，当前音频引擎会返回 HTTP 500，因此没有加入桌宠的可选声线，不应宣称 B 可选。试听音频仅留在开发机 `qa/` 目录，不进入便携包或源码包。
- 1.3 立绘 v18 尚未获得管理员确认，运行时继续使用旧动作图集。本版没有接入 v18，也没有修复角色形象；站立微动作仍是待审预览。
- 局域网协议、事件账本与存档格式未在本版改动。a3/a4 已生成的零值账本仍不会自动改写，升级限制见 [局域网协同说明](LAN_SYNC_PREVIEW.md)。
- 发布脚本 `tools/make_windows_release_2a7.py` 使用 `dist-2.0a7/` 构建产物，生成独立的 `SiDesktopPet-2.0.0a7-Windows-x64.zip`、`SiDesktopPet-2.0.0a7-Source.zip` 与 `release/SHA256SUMS-2.0.0a7.json`，并复核已有 ZIP 哈希未变化。源码包排除 `build-2.0a7`、`dist-2.0a7` 及其他构建目录、`release` 和 `qa`。
- 验证脚本 `tools/verify_windows_release_2a7.py` 检查便携包完整性、源码包边界、移除 Python PATH 后的普通与协同启动、对话窗口构造和面板渲染，报告写入 `qa/release-2.0.0a7-verification.json`。
- 发布验证（2026-10-26，主协作者独立执行）：Windows 完整 unittest 共 103 项全部通过，33 项 Qt 互动集成检查通过。PyInstaller 6.22.3 构建了本版便携程序；新便携包与源码包均通过 ZIP 完整性检查，源码包内容边界复核通过，排除 `build-2.0a7`、`dist-2.0a7`、`release`、`qa` 与试听音频。
- 将 Python 从 PATH 移除后，异地解压的 `Si-DesktopPet.exe` 在普通模式和 `--sync-preview` 模式下均启动并报告版本 `2.0.0a7`；协同模式完成本机 TLS 配对与事件交换。对话界面构造、面板渲染和运行日志均未发现未处理异常。另将便携 ZIP 解压至 `F:\si_accompany\SiDesktopPet-2.0.0a7-Windows-x64`，其 `Si-DesktopPet.exe --smoke-test` 输出 `ok`。
- 移动端源码检查（2026-10-26）：Android 工程的 8 个 Java 源文件编译通过；本版未重新打 APK，也未安装到设备。鸿蒙原生适配器通过 OpenHarmony 公开 API 的 TypeScript 严格检查（TypeScript 5.6.3）；这不是 HarmonyOS SDK / ArkTS 编译，未生成 HAP。两项都不构成 Android 真机或 Harmony 平板证据。
- 验证边界：实际运行仍只在一台 Windows 电脑上完成，未在两台物理 PC、Android 真机或 Harmony 平板上运行或验收。默认声线 A 保持不变，B 未加入可选；1.3 立绘 v18 未采用。报告见 `qa/release-2.0.0a7-verification.json` 和 `qa/ready-2.0.0a7-smoke.json`，归档哈希见 `release/SHA256SUMS-2.0.0a7.json`。
