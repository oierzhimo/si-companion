# 小祀 · Si Companion（跨设备桌面 & 随身伙伴 v2.1.2）

> 官网与 iOS 独立 PWA 入口：**https://oierzhimo.github.io/si-companion/**
> 下载发布页（Windows / Mac mini / iOS / Android）：**https://github.com/oierzhimo/si-companion/releases/tag/v2.1.2**

小祀是以《明日方舟：终末地》角色「祀」为形象的非官方开源同人伙伴项目，支持 **Windows**、**Mac mini（Apple Silicon / Intel）**、**iOS（iPhone / iPad 独立 PWA 与 SwiftUI 原生工程）**、**Android 预览版** 及 **HarmonyOS 5 工程预览**，并内置 **Soul Engine V1.2 数字人格**、**耳机/游戏感知智能语音与拟声词/动作音效** 以及跨端事件账本同步（`sync-v1`）。

---

## 全平台支持一览（v2.1.2）

| 平台 | 运行形式 | 核心特性与入口 |
|---|---|---|
| **Windows 10/11 (x64)** | 绿色便携压缩包 (`SiDesktopPet-2.1.2-Windows-x64.zip`) | 解压即用；3D 防穿模与对立平衡微动态；窗口顶边落脚；Core Audio 耳机检测 + Apex/彩六等游戏自动静音 TTS & 拟声/动作音效；`data/` 便携存档 |
| **Mac mini / macOS 13+** | [`SiDesktopPet-2.1.2-macOS-Source.zip`](https://github.com/oierzhimo/si-companion/releases/download/v2.1.2/SiDesktopPet-2.1.2-macOS-Source.zip)（双击 [`start_macos.command`](si-desktop-pet/start_macos.command) 或用 `tools/build_macos.sh` 生成 `.app`/`.dmg`） | Cocoa 置顶、PingFang SC 字体、`AppleInterfaceStyle` 深浅色自适应、`system_profiler` 耳机检测、`afplay` 语音播放、`LaunchAgents` 登录自启；存档于 `~/Library/Application Support/SiDesktopPet/data` |
| **iOS (iPhone / iPad)** | ① Safari 添加到主屏幕独立 PWA（[`si-web/`](si-web/)）<br>② SwiftUI 原生工程（[`mobile/ios/`](si-desktop-pet/mobile/ios/)） | 刘海屏/灵动岛全屏适配、手指拖动 16 向视线跟随、绑定 Mac mini / Windows 实时节点双向聊天，或开启内存级 Key 直连 DeepSeek / Ollama API |
| **Android 预览版** | `0.1.3-preview` 原生 Java APK | 应用内陪伴、系统悬浮窗、系统分享接收邀请与局域网 TLS 配对同步 |
| **HarmonyOS 5 工程预览** | `0.1.0` ArkTS/ArkUI 工程 ([`mobile/harmony/`](si-desktop-pet/mobile/harmony/)) | 原生页面、设备账本与 TypeScript `sync-v1` 协议实现 |
| **QQ 多模态原型** | [`si-qq-bot/`](si-qq-bot/) | OneBot 适配、多模态图文/媒体收发参考实现 |

---

## Mac mini & iOS 快速开始

1. **Mac mini / macOS**：
   - 下载 Release 中的 `SiDesktopPet-2.1.2-macOS-Source.zip` 解压后，双击 `start_macos.command` 即可自动配置虚拟环境并运行；或执行 `bash tools/build_macos.sh` 打包为独立 `SiDesktopPet.app` 与 `.dmg`（另附 CI 模板 [`si-desktop-pet/docs/macos-release-workflow.yml`](si-desktop-pet/docs/macos-release-workflow.yml)）。
   - 若打包 `.app` 首次打开遇 Gatekeeper 提示「无法验证开发者」，在终端执行 `xattr -cr /Applications/SiDesktopPet.app` 或在「系统设置 → 隐私与安全性」点击「仍要打开」。
2. **iPhone / iPad (iOS)**：
   - **零编译方式（推荐）**：用 Safari 打开 **https://oierzhimo.github.io/si-companion/** → 点击底部「分享」→「添加到主屏幕」，即可离线/全屏运行。
   - **Xcode 真机编译**：用 Mac mini 打开 `si-desktop-pet/mobile/ios/`（支持 `xcodegen generate` 与 `swift test`），选择个人 Apple ID Team 后即可运行到真机。

---

## 开发与测试命令

```bash
# Windows / macOS 运行全量 Python 单元测试（offscreen 无头模式）
cd si-desktop-pet
QT_QPA_PLATFORM=offscreen PYTHONPATH=src python3 -m unittest discover -s tests -p "test_*.py"

# macOS 本地构建 .app / .dmg（Apple Silicon M 系列 / Intel 均适用）
bash tools/build_macos.sh

# Swift Core 单元测试（macOS / Xcode 15+）
cd mobile/ios && swift test -v
```

---

## 署名与许可

- 本项目代码采用 **GPL-3.0** 协议，行为设计参考 [isHarryh/Ark-Pets](https://github.com/isHarryh/Ark-Pets)。
- 角色「祀」及《明日方舟：终末地》相关权利归原权利人所有；素材署名与第三方许可见 [si-desktop-pet/THIRD_PARTY_NOTICES.md](si-desktop-pet/THIRD_PARTY_NOTICES.md)。
