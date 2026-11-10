# 本次交接复验

2026 年 11 月 10 日，在交接源码的隔离副本上执行以下检查。Python 依赖复用本机已安装版本，应用数据目录指向临时位置。QQ 冒烟走离屏自检，移动端协议检查走本机回环。

| 检查 | 结果 | 范围 |
|---|---|---|
| Windows a9 源码冒烟 | 通过，报告版本 2.0.0a9 | Qt 启动、加载动画、局域网预览、本机 TLS 配对和交换 |
| Windows a9 unittest | 223 项，221 通过，1 失败，1 跳过 | `test_genai_status_widget` 的 Qt 请求属性断言，实际返回 `LoadControl.Manual` 枚举，旧测试期待整数 `1` |
| Windows a10 叠加候选 unittest | 228 项，227 通过，1 跳过 | 同一份桌宠资源和测试夹具，叠加六个候选文件。原生前台全屏仍需独立验收 |
| QQ 0.2.5 unittest | 444 项通过 | 含路由、附件、分条、能力边界与 GUI 逻辑 |
| QQ 源码冒烟 | 通过 | 生成离屏界面预览 |
| QQ 相对路径 spec 构建及 EXE 冒烟 | 通过 | 从交接副本构建 `SiQQ-Handoff`，生成冻结程序预览 |
| Android 协议 | 通过 | Java/Python 规范 JSON、夹具恢复、真实 TLS 回环。验证时从本机补入已安装的测试 JAR |
| Android 构建 | 通过 | 使用 JDK 17、API 35、build-tools 35 和临时开发密钥构建，并用 `apksigner` 验签 |
| HarmonyOS 协议 | 通过 | TypeScript 核心与 Node/Python TLS 回环 |
| HarmonyOS 公开接口声明 | 通过 | 使用固定提交的实际 `api/` 目录复跑。第一轮传入了 SDK 下载缓存目录，出现模块找不到，修正路径后通过 |

本次证据对应离线、隔离的开发环境。a10 原生窗口、Windows 多模态真实服务、移动端安装与长后台、HarmonyOS 商用 SDK、第二台物理 PC 和跨端媒体仍是后续验收项。Android 历史真机证据仅针对原发布 APK 与单手机配对场景。临时签名用于验证本次构建脚本。

## 交接副本的适配

- `si-qq-bot/SiQQ-Handoff.spec` 用于从解压路径构建，收录知识库、表情资源和必要运行钩子。QQ 业务源码保持发布基线。
- `si-desktop-pet/mobile/android/build-apk.ps1` 接受显式签名文件与别名，从本机环境读取签名密码。Windows 桌宠主源码保持 a9，候选文件留在 `candidates/desktop-a10/`。
- 主文档使用包内相对路径。历史工程说明仍有原机器路径、旧版本号及原始验收报告引用。可运行入口和迁移步骤见 [接手指南](TAKEOVER.md)。

原项目的测试与实机证据可以和本次复验一起参考。已带入 [QQ 发布记录](evidence/release-0.2.5-validation.json)、[图片发送回执](evidence/generated-image-025-qq-send.json) 以及 [a10 运行报告](evidence/desktop-a10/native-visibility-pause.json)。发送回执与接收端实际显示是两项检查。
