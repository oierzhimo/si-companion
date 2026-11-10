# 祀 · 手机陪伴 0.1.3 预览（已构建，完成部分真机与单 PC 私网验证）

Android 原生 Java 应用，包名 `cn.si.companion`，最低 Android 8.0（API 26），目标 API 35。当前版本为 `0.1.3-preview`（versionCode 1007），新增接收系统分享的配对邀请。0.1.3 已构建，并在一台 vivo V2564A / Android 16 物理手机与一台 PC 的私网 IPv4 直连中完成部分真机验证：原生 Android TLS 配对与事件交换通过。**这不是完整的手机版或多端验收**，未验证项见下文。0.1.2（1006）及更早版本的 build/dist 与发布文件全部保留，不会被 0.1.3 的源码或发布改动覆盖。

`0.1.2-preview` 的部分真机 UI/悬浮验证作为历史证据保留：角色可见、摸头与计数、强停重启后的记录保留、安静模式偏好保留，以及悬浮服务的启动、拖拽靠边和停止。**那些结论只属于 0.1.2；0.1.3 没有重做这些界面截图和悬浮复测**，不能把 0.1.2 的 11 项检查写成 0.1.3 的结果。

`0.1.0-preview` 与 `0.1.1-preview`（1002）的 APK 由 Windows AAPT2 把 14 帧角色资源以字面反斜杠写入 entry，例如 `assets/hd\idle\00.png`；真机 `AssetManager.list("hd")` 为空，角色区域显示空白，因此不推荐安装这两个版本。它们的发布文件与检查报告继续保留，不会被本次修改覆盖或删除。源码和 `srcJava` 本身正常，问题只在 APK 打包路径；0.1.2 起在签名前用 JDK 重写 ZIP entry 名修复，0.1.3 沿用同一修复。

## 0.1.3 新增：接收系统分享的配对邀请（已构建并完成真机填入验证）

- `MainActivity` 新增 `Intent.ACTION_SEND` + `text/plain` + `EXTRA_TEXT` 接收。应用冷启动收到分享，或主界面已在顶层时通过 `onNewIntent` 收到分享，都会把文本填入“连接我的电脑”上方的邀请输入框；`onNewIntent` 先调用 `setIntent`，再走同一处理。普通桌面图标启动没有 `ACTION_SEND`，不会覆盖用户已输入的内容。
- 分享文本先去除首尾空白，只接受 1..4096 个字符。应用只做本地检查：JSON 可解析、`transport=1`、邀请必要字段格式，以及 `PinnedPeer.validate` 的局域网 IPv4、端口和证书指纹检查。**不调用配对、不发起网络请求**，不改动已有配对、自动同步开关、账本或互动数据。
- 检查通过后仅用 `setText` 填入邀请并自动滚动到连接栏，状态只提示确认电脑名称后主动点“连接我的电脑”，不把邀请 token 显示在普通状态文本中。空白、过长或不合格的分享只显示“这份邀请暂时无法使用”等简短提示，不执行分享文本，也不打开其中的地址或链接。**分享只预填，仍须用户自己点击连接，不会自动配对。**
- 邀请输入框使用 `NO_SUGGESTIONS`，避免中文输入法把分享/粘贴的 JSON 改写；分享文本通过 `setText` 保真填入。真机验收已确认分享文本精确保真，且收到分享本身不会触发联网或配对。
- `AndroidManifest.xml` 为同一个 `MainActivity` 增加独立的 `ACTION_SEND` / `CATEGORY_DEFAULT` / `text/plain` intent-filter，并设为 `singleTop`，让重复分享复用顶层实例。原 `LAUNCHER` filter、`exported=true` 和现有权限保持不变；未增加 Receiver、URL scheme 或网络权限。

## 0.1.3 构建、发布与部分真机/单 PC 验证

0.1.3 的工程内构建输出为 `mobile/android/dist/SiCompanion-Android-0.1.3-preview.apk`，构建目录为 `mobile/android/build/0.1.3-preview`。构建沿用 0.1.2 的 ZIP 路径规范化步骤（`tools/NormalizeApkPaths.java`，在更新 `classes.dex` 之后、`zipalign` 之前运行）和同一预览签名密钥，因此可从 0.1.2 直接覆盖升级。此前 README 中“0.1.3 尚未构建、尚未验收”的记录只是当时状态，现已由本次构建与部分真机验证取代。首次 0.1.3 安装尝试曾被手机系统安装器拒绝（`INSTALL_FAILED_ABORTED: User rejected permissions`），这也是历史记录；用户明确授权重试后安装成功，原应用数据未清除。

发布入口为工程根的：

```text
python tools/package_android_013_preview.py
```

该脚本只在全部校验通过后写入 `release/SiCompanion-Android-0.1.3-preview.apk`、`release/SiCompanion-Android-0.1.3-Source.zip` 和 `release/VERIFIED-Android-0.1.3.json`，默认拒绝覆盖同名产物，并等待主协作者独立执行。它读取 `qa/android-013-device-review.json`，要求 schema 为 `si-android-device-review/v2`、版本 `0.1.3-preview`/1007、包名、APK SHA-256 与待发布 APK 一致，顶层安装、拉回哈希、设备测试均为 true，17 项 checks 全部为 true；同时要求 `transport` 中 `direct_private_ipv4`、`native_android_tls_verified`、`physical_pc_android_lan_verified` 为 true，`adb_reverse_used`、`physical_multi_pc_lan_verified`、`harmony_verified` 为 false。报告保留新的 `not_verified` 列表，标记 `partial_android_device_verification=true`、`runtime_art=legacy`，不会把 0.1.2 的 11 项界面检查套到 0.1.3，也不会把单手机 + 单 PC 写成两 PC 或 HarmonyOS 验收。

已完成的部分真机/单 PC 私网验证如下，证据见 `qa/android-013-device-review.json` 与 `qa/android-native-sync-v2/`：

- 系统分享的邀请文本精确保真；收到分享不自动配对、不自动联网。由用户在原生 Android 界面明确点击“连接我的电脑”后，经私网 IPv4 直连配对成功，未使用 `adb reverse`。
- 共 4 次原生 Android TLS 交换；PC 端导入计数为 `[44,0,0,0]`。PC 端两条手记在手机各显示一次；重复摘要与事件数不变，重复交换导入 0。
- 强停并重开后，连接与两条手记仍在，计数保持亲密度 31 / 摸头 16 / 点心 5；重连导入 0。
- 错误 fingerprint 在 Android 原生握手被拒绝并提示“对方证书已变化”，未替换已有 peer。
- 测试后已从手机移除本机连接、撤销电脑端授权、关闭测试服务与端口；自动同步开关始终为 false，现有记录未被清除。手机安装包拉回后的 SHA-256 为 `e9fcf1c50f1a33a7e1279f50d655f5ef89f4d987aa4e1f692614349552278d40`，与本地待发布 APK 一致。

**边界：** 本次只有一台物理手机和一台 PC。未验证两台物理 PC 的协同、HarmonyOS 5 平板、断网恢复与多设备冲突、后台长期耗电、每分钟自动同步的长期生命周期、异常或超长分享输入，也未接入新实机立绘或完整动画。0.1.3 没有重新做 0.1.2 的界面截图与悬浮复测，不能把本次单 PC 私网 TLS 结果当作完整多端验收。

## 0.1.2 已构建内容（历史版本，继续保留）

- 新增 `tools/NormalizeApkPaths.java`：以 JDK 17 单文件源码方式运行，仅用标准库，入参为 `unsigned.apk` 和 `normalized.apk`。它逐个 entry 读取，用 `getName().replace('\\','/')` 生成新名称；拒绝绝对路径、`..` 段以及规范化后的重复名称。新 ZIP 保留原压缩方法，STORED entry 显式写入 size/CRC，其余内容按解压后字节原样复制，因此全部 entry 的解压内容不变。原 `unsigned.apk` 只读；新文件先写同目录临时文件再原子移动，不递归删除任何目录，并输出规范化 entry 计数。
- `build-apk.ps1` 在 `jar` 更新 `classes.dex` 之后、`zipalign` 之前调用上述工具，把 `unsigned.apk` 转为 `normalized.apk`；`zipalign` 读取 `normalized.apk`，随后仍用现有密钥签名。0.1.2 构建时的版本为 `0.1.2-preview` / `1006`；当前脚本默认值已是 `0.1.3-preview` / `1007`，0.1.3 也按同一顺序构建。真机已在保留应用数据的情况下从 1006 升级到 1007。
- 角色素材仍为原有生成立绘：idle 6 帧、waving 4 帧、sitting 4 帧，共 14 帧，未替换、未修改帧内容；66 句台词、包名、权限、minSDK/targetSDK 和模型配置均未因 0.1.2 的修复或 0.1.3 的分享接收改变。0.1.3 的代码改动仅限 `MainActivity` 的分享接收处理与 `AndroidManifest.xml` 中同一 Activity 的 intent-filter/singleTop，未改角色加载、账本、配对协议或悬浮逻辑。
- `CompanionView` 加载失败时，无障碍描述恢复为短句“角色图像暂时不可用”，不再拼接 assets 目录和 `sourceDir` 临时诊断信息；`Log.e` 仍记录完整堆栈，异常消息保留 `Missing frame` 的文件名。其他加载与动画逻辑不变。

## 0.1.2 构建、打包与部分真机验收（历史）

`build-apk.ps1` 已构建出 `mobile/android/dist/SiCompanion-Android-0.1.2-preview.apk`。在工程根目录运行 `python tools/package_android_012_preview.py` 后，发布目标为 `release/SiCompanion-Android-0.1.2-preview.apk`、`release/SiCompanion-Android-0.1.2-Source.zip` 和 `release/VERIFIED-Android-0.1.2.json`；0.1.2 的发布文件已经存在，构建与打包脚本都会拒绝覆盖同名产物。

0.1.2 发布脚本必须读取 `qa/android-012-device-review.json`，校验 schema、版本、versionCode、包名、`android_device_tested`、`apk_installed_on_device`、`installed_apk_pulled_and_hash_matches`、APK SHA-256，以及 11 项真机 checks 全部为 true，才会把设备测试记为 true，并同时写入 `partial_android_device_verification=true`、验证范围和未验证项。缺少或篡改该 QA 记录时发布会停止。这些 11 项 UI/悬浮结论仅针对 0.1.2。

0.1.2 已完成的部分真机验证如下，证据见 `qa/android-012-device-review.json`：

- 冷启动后主界面角色可见，不再出现资源缺失描述。
- 摸头后亲密度 14 → 15、摸头 8 → 9，点心保持 2；强停并重新打开后仍为 15 / 9 / 2，原安静模式开关 `true` 保留。
- 悬浮前台服务与窗口可启动；拖拽后从 x=1000 靠边到 x=0；停止后 service 与 window 均消失。测试结束已停止悬浮，未清除应用数据，也未卸载。
- 手机安装包拉回后的 SHA-256 为 `81972f08dc37f977684d38a47934b6c06ce1170ba2452ef9fa6f618c69654cf4`，与本地待发布 0.1.2 APK 一致。

0.1.2 未验证：native Android TLS 与 PC 配对交换、物理局域网或多 PC 协同、悬浮长按/锁屏/旋转/长时间耗电、关闭安静模式后的手势动画、完整手记保留与导入导出、HarmonyOS 平板安装运行，以及新实机立绘和完整动画的接入。JVM/PC 协议回环验证通过，但不等于 Android 原生 TLS 实机验收。0.1.3 已另行完成单手机 + 单 PC 的原生 TLS 验证，但 0.1.2 的这些历史限制不因此改写。

## 安装与升级

0.1.3 已构建并通过上述部分真机验证。工程内安装文件为 `mobile/android/dist/SiCompanion-Android-0.1.3-preview.apk`；主协作者执行 `python tools/package_android_013_preview.py` 后，发布安装文件为 `release/SiCompanion-Android-0.1.3-preview.apk`。升级安装必须保持同一包名和同一签名证书；满足这两点时可直接覆盖安装 1007，应用数据（账本、手记、配对身份等）按 Android 规则保留，请勿为升级先卸载。0.1.3 的真机安装曾在首次尝试时被系统安装器拒绝，用户明确授权重试后成功；现有记录未被清除。旧 0.1.0、0.1.1、0.1.2 及诊断包的 build/dist 和发布文件均保留。

旧 0.1.0/0.1.1 不建议安装或推荐给他人使用，原因是角色资源路径在真机失败、角色空白；它们作为历史版本和证书对照继续保留。

安装后打开应用即可摸头、送点心、选择五个话题、安静陪伴和查看手记，不要求悬浮权限。若要桌面陪伴，主动点击“开启桌面悬浮”，按系统提示允许悬浮和通知。小祀可拖动，松开后靠近屏幕边缘，点击摸头，长按回到应用。通知与应用内都可以停止。拒绝权限时应用内陪伴仍可使用。没有开机自启、无障碍或屏幕内容读取。

## 与 Windows 配对

1. PC 推荐使用当前 `2.0.0a7` 的 `Start-Sync-Preview.cmd` 启动。手机与 PC 连接同一局域网；0.1.3 的已验证路径是一台手机与一台 PC 的私网 IPv4 直连。
2. PC 在“相伴手记 → 局域网配对与同步”生成邀请并复制到手机，粘贴后点击“连接我的电脑”。也可以把邀请文本通过 Android 系统“分享”发给本应用：0.1.3 会校验后把邀请填入输入框并滚动到连接栏，但**不会自动配对或联网**。无论粘贴还是分享，仍须用户自己确认电脑名称并主动点击“连接我的电脑”。邀请仅一次、3 分钟有效。
3. 点击“现在同步”。需要时勾选“应用打开或悬浮期间，每分钟同步”。应用关闭且悬浮停止后，不承诺继续后台同步；长期自动同步生命周期尚未在真机上验证。
4. 手机首次建立空账本，不创建旧累计快照；可以接收 PC 的已有迁移基数。首次配对前手机发生的互动会作为独立事件合并。
5. 手机可移除本机连接，PC 可撤销手机授权。导出记录包包含互动与手记，不含配对密钥。保留应用数据即可保留本机设备身份。

## 验证与限制

- 0.1.2 的打包脚本检查 aapt2 badging 中的实际包名、版本 `0.1.2-preview`/1006、minSDK 26 和 targetSDK 35；检查 v2/v3 签名及证书 SHA-256 与更早发布 APK 一致；检查 APK 台词为当前 Windows 的 11 类各 6 句共 66 句；检查 14 帧与现有原素材逐字节相同；检查 APK 和源码 ZIP 完整、源码包内容与文件一致。
- 0.1.3 的发布脚本复用同一组构建检查，版本固定为 `0.1.3-preview`/1007，证书要求与 `release/SiCompanion-Android-0.1.2-preview.apk` 一致，并确认 `build-apk.ps1` 仍在 zipalign 前运行路径规范化工具。
- 最关键的新增检查直接读取 APK 与源码 ZIP 的 `ZipInfo.orig_filename`，拒绝反斜杠、绝对路径、相对路径段和重复 entry，并在报告中标记 `raw_entry_paths_are_posix=true`。此前 Windows 上 `ZipInfo.filename` 会自动把反斜杠规范成斜杠，掩盖了真实 entry 名，旧检查因此通过但真机角色空白。
- 0.1.3 发布脚本要求 `qa/android-013-device-review.json` 的 schema 为 `si-android-device-review/v2`，版本/versionCode/包名匹配，安装、拉回哈希和设备测试为 true，APK SHA-256 与实际待发布 APK 一致，17 项 checks 全为 true；`transport` 中私网 IPv4、原生 Android TLS 和单 PC LAN 为 true，`adb reverse`、物理多 PC 和 Harmony 为 false。报告会带出验证范围、新的 `not_verified` 和记录 SHA-256；QA 中的 IP、token、密钥、serial 等私密字段不会进入报告。缺少或篡改该记录时发布会停止。
- 0.1.2 的历史 11 项 UI/悬浮检查继续作为 0.1.2 的证据，不套用到 0.1.3。0.1.3 没有重新采集界面截图或复测悬浮，不得声称已完成全部 overlay/visual 复测。
- JVM/PC 协议检查继续引用 `qa/android-protocol-checks.json`，发布脚本要求其中 `ok=true`。该检查使用相同生产协议代码在 JVM 与 Python 服务间验证配对、双向 TLS 和事件交换，只证明 JVM/PC 协议回环，不代表 Android 实机网络结果。
- 0.1.3 已在 V2564A / Android 16 上完成一台物理手机与一台 PC 的私网直连验证：分享保真且不自动联网、明确点击后配对、4 次原生 TLS 交换、`[44,0,0,0]` 导入、两条 PC 手记各显示一次、重复导入 0、强停重开与重连保持、错误 fingerprint 被拒绝，以及测试后清理。发布报告标记 `runtime_art=legacy`、`partial_android_device_verification=true`、`native_android_tls_verified=true`、`physical_pc_android_lan_verified=true`、`physical_multi_pc_lan_verified=false`、`harmony_verified=false`。
- 仍使用旧版生成角色素材。仅在触摸时播放一次招手，平时稳定站立并间隔播放待机小动作；安静模式保持坐姿。0.1.3 未接入新实机立绘或完整动画。
- 当前移动版不包含 VoiceStudio 声音、专注计时或鸿蒙 ArkTS 客户端。协议整数使用 Java 有符号 64 位范围；超出范围的异常记录会拒绝导入。

悬浮实现使用 [TYPE_APPLICATION_OVERLAY](https://developer.android.com/reference/android/view/WindowManager.LayoutParams#TYPE_APPLICATION_OVERLAY)；用户主动开启的可见陪伴服务声明 [specialUse 前台服务类型](https://developer.android.com/develop/background-work/services/fgs/service-types#special-use)。系统与厂商仍可能限制悬浮或后台存活，须实测。

## 构建

本机可直接运行 `build-apk.ps1`。参数 `VersionName` 默认 `0.1.3-preview`，`VersionCode` 默认 `1007`；版本字符串只允许字母、数字、点和横线，版本号必须为正整数。默认拒绝覆盖已存在的目标 APK，也不删除旧版本构建目录；确需重建同一目标时可显式传入 `-Force`，该参数只替换目标 APK。0.1.3 已按该默认值构建；重建时应继续使用同一 ZIP 路径规范化步骤和同一预览签名密钥。

脚本默认沿用现有 FocusGuard 工具目录中的 JDK 17、Android build-tools 35 和 platform android.jar，以及工程外的现有预览签名密钥路径；也可传入 `JavaDirectory`、`BuildToolsDirectory`、`AndroidJar` 和 `SigningKey`。密钥不存在时脚本会停止，不会自行生成新密钥。构建顺序为：aapt2 链接资源、编译 Java、d8 生成 dex、jar 更新 `classes.dex`、运行 `NormalizeApkPaths.java` 生成 `normalized.apk`、对 `normalized.apk` 执行 zipalign、用现有密钥签名并验签。规范化工具缺少时构建会直接停止。

0.1.2 的发布脚本 `tools/package_android_012_preview.py` 只用于 0.1.2，本次没有修改；它的产物与 0.1.2 发布文件保持不变。0.1.3 的发布入口是工程根的：

```text
python tools/package_android_013_preview.py
```

该脚本只在全部验证通过后写入 `release/SiCompanion-Android-0.1.3-preview.apk`、`release/SiCompanion-Android-0.1.3-Source.zip` 和 `release/VERIFIED-Android-0.1.3.json`，默认拒绝覆盖现有产物；它不会改动旧 Android 0.1.0/0.1.1/0.1.2 或诊断包文件，旧的 011/012 打包脚本也保持不变。发布前还必须存在通过校验的 `qa/android-013-device-review.json`；脚本会把其中的非私密真机事实、记录 SHA-256、部分验证范围和未验证项写入报告，不会把单手机 + 单 PC 写成多端验收。源码包包含 Java、资产、构建/测试文件、`tools/NormalizeApkPaths.java`、分享接收改动、`AndroidManifest.xml`、`mobile/android/README.md`、根 README.txt、LICENSE、THIRD_PARTY_NOTICES、licenses、requirements 与测试依赖说明，排除 build/dist、密钥/签名材料、QA、测试账本/邀请文件、视频和模型；`src/si_pet` 只含供 PC 协议互测的三个协议文件。源码包等待主协作者独立执行发布脚本生成。

Android 应用没有第三方运行库；使用系统 org.json。`tools/json-jvm-tests.jar` 仅用于电脑上的协议测试，不进入 APK 或源码包。测试入口为 `tests/run_protocol_checks.py`；完整工程中可重新运行 `tools/prepare_assets.py`，将原帧和桌面版 LINES 原样复制为安卓资产。源码包已带生成资产，构建 APK 时无需再次准备。

本地预览签名密钥位于工程外，不随源码包分发。角色和图像为非官方同人素材，权利说明见工程根目录 `THIRD_PARTY_NOTICES.md`。
