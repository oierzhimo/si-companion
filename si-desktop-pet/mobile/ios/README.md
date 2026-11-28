# 小祀 · iOS 原生 SwiftUI 伴侣工程 (v2.1.2)

面向 **iPhone / iPad (iOS 16+)** 的原生 SwiftUI 伴侣工程，支持与 Mac mini / Windows 桌面端共享 66 句原创台词、水墨金玉深浅主题、摸头/点心/25 分钟专注计时、局域网/公网同步节点（`/api/*`）以及独立直连 DeepSeek / Ollama / 学校兼容 API（API Key 仅留在内存，绝不写盘）。

> 💡 如果你只想最省事地在 iPhone/iPad 桌面上拥有独立的「小祀」App：**无需编译任何代码**，直接用 iPhone Safari 打开官网 https://oierzhimo.github.io/si-companion/ → 点底部 **分享图标** → **添加到主屏幕**，即可获得带独立图标的全屏 PWA 应用！

## 在 Mac mini 上用 Xcode 构建真机 App

1. 安装 Xcode 15+（Apple Silicon M 系列 Mac mini 可通过 XcodeGen 或 SwiftPM 打开）。
2. 如已安装 Homebrew，可一键生成 `.xcodeproj`：
   ```bash
   brew install xcodegen
   cd si-desktop-pet/mobile/ios
   xcodegen generate
   open SiCompanion.xcodeproj
   ```
3. 在 Xcode 顶部选择你的 Apple ID（`Signing & Capabilities -> Team`），连接 iPhone / iPad 后点击 **Run (⌘R)** 即可安装到真机。
4. 运行核心单元测试（可在任意 macOS / CI 无签名环境运行）：
   ```bash
   cd si-desktop-pet/mobile/ios
   swift test -v
   ```

## 已验证与边界说明

- **已验证范围**：`SiCompanionCore` Swift Package 涵盖 66 句洗牌轮换、`SyncSnapshot` JSON 编解码与接口封装，可在 macOS CI 通过 `swift test` 编译与单测验证。
- **未验证范围**：未随公开仓库附带商业 Apple Developer 签名 IPA，真机签名部署需使用你自己的 Apple ID 在 Mac mini Xcode 中完成；iOS 系统不支持像桌面端那样的系统级透明置顶悬浮窗，因而采用独立应用与实时同步模式。
