# 祀 · 鸿蒙平板原生工程预览 0.1.0

这是面向 HarmonyOS 5 / API 12 的 ArkUI/ArkTS 工程源码预览，**尚未通过 HarmonyOS SDK 构建，没有可安装 HAP**。它不替代 Android APK，也不能把下述 Node 协议测试称为鸿蒙运行验收。

## 已写入工程

- 原生 EntryAbility、ArkUI 页面和手机/平板宽度自适应布局。
- 使用现有角色帧的稳定待机、单次招手与安静坐姿；共享桌面版管理员台词，摸头、点心、五个话题和相伴手记。
- 独立设备账本、PC 邀请配对、双向事件交换，以及应用打开时可选的每分钟同步。
- 原生 TLSSocket 的证书指纹核对，核对成功后才发送邀请凭据或记录；文件选择器的导入/导出入口。
- 账本使用应用私有文件，先完整写入、fsync，再以同目录 rename 替换。Preferences 仅保存较小的本机开关与配对信息，避免其字符串长度上限截断不断增长的记录。

## 已验证的范围

1. 生产 TypeScript 协议核心通过 TypeScript 5.6.3 严格检查。
2. 与 Python 使用相同事件样本，核对中文/表情符号摘要、旧累计、亲密度、重复与冲突拒绝、微秒顺序、并发写入、注入存储失败时核心状态不前进，以及保存后重读。
3. 同一核心配合 Node 测试传输适配器，与 Python PC 服务执行真实 TLS 配对、双向交换、重试和错误指纹拒绝；生产 HTTP 分帧器逐字节处理了中文跨帧与不合法响应。
4. 原生网络、私有文件、Preferences 和文件选择适配器，已与公开 OpenHarmony 5.0 / API 12 类型定义做签名对照。固定提交为 `0ebbb2136397fd2b190547bc3c2cdcf6b4ba257a`，来源为 https://github.com/openharmony/interface_sdk-js 。该检查仅使用标准 TypeScript 类型系统，不是 ArkTS 编译器检查。

**未验证**：HarmonyOS 商用 SDK 编译、ArkUI 页面编译与显示、原生 TLSSocket/文件系统的实际运行、HAP 签名、平板安装及生命周期。悬浮窗、后台常驻、语音与专注计时还未接入。当前采用 JavaScript 安全整数范围，超范围异常记录会拒绝，避免静默截断。

## 当前构建条件

本机未发现 DevEco / HarmonyOS SDK。官方工具下载页 https://developer.huawei.com/consumer/cn/download/command-line-tools-for-hmos 会跳转到华为账号登录，尚未取得商用 SDK。另一次 OpenHarmony 公共 SDK 大包下载已主动取消，未把未完成的下载当作可用 SDK。

有 SDK 后，在 DevEco 中打开本目录，核对 API 12 或兼容 SDK、工程 profile 与签名配置，再执行原生构建并修正实际编译诊断。当前 build-profile 和 hvigor 配置是待编译检查的工程配置，不能据此声称能够安装。真实平板的调试签名和部署需要用户自己的设备与开发者配置。

源码包已经包含 rawfile 角色帧与共享台词，不需要再次生成资产。完整工程中可运行 tools/prepare_assets.py 重新从原素材复制。

## 检查入口

- tests/run_core_checks.py：严格类型与 Node/Python 实际协议互测。
- tests/check_public_api.py：将不含 ArkUI DSL 的原生适配器原样暂存为 .ts，与公开 API 声明核对；不会构建页面或 HAP。
- 可用 SI_NODE、SI_TSC、SI_OH_API 指定 Node、TypeScript 编译器和公开 API 路径。独立工程包测试时用 SI_PC_PROJECT 指向完整桌宠工程，需 Python cryptography。

TypeScript 编译器来自 npm 官方包 typescript@5.6.3，下载时核对了 registry 元数据的 SHA-512。公开 API 为上述固定提交的源码声明，没有将其冒充 HarmonyOS 5 SDK。

当前所有角色帧仍是旧版生成素材，面部、服装和手臂墨纹修订尚未接入。角色相关权利归原权利人，代码采用 GPL-3.0-only。
