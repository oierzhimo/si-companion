# 项目介绍与状态

## 产品目标

小祀是以祀为原型的跨设备桌面伙伴。用户希望她在工作时安静陪伴，在用户召唤或发送内容时自然回应，并将用户视为管理员。角色表达应亲近、克制，避免客服腔、反复自我介绍或凭空声称知道用户屏幕和真实处境。桌宠形象来自实机资料，交互需要兼顾不挡视线、拖拽可靠、自然走动和全屏避让。

目标是 Windows、Android 与 HarmonyOS 客户端共享可接续的会话，支持文字、图片、文件和语音等输入，理解内容后以合适的文字、图像、文件或语音回复。QQ 原型试过附件暂存、后续引用、长文本分条、语音和图片发送、失败提示。这些交互提供参考。实现时把内容处理和模型能力抽出，桌宠与 QQ 分别使用自己的界面和发送适配器。

## 已有工程与关键入口

| 工程 | 入口 | 职责 |
|---|---|---|
| Windows 桌宠 | `si-desktop-pet/main.py`、`src/si_pet/app.py` | PySide6 应用、桌宠和设置面板、托盘、恢复入口 |
| 桌宠行为 | `controller.py`、`pet_window.py`、`physics.py`、`native.py`、`sprites.py` | 动作状态、拖拽、显示区域、全屏抑制、动画资源 |
| 桌宠自由聊聊 | `conversation_ui.py`、`conversation.py` | 会话界面、文本历史和模型调用。现有桌宠语音调用还可参照 `voice.py` 和 `voice_client.py` |
| 现有同步 | `storage.py`、`sync_v1.py`、`sync_transport.py`、`sync_lan_ui.py` | 本地保存、事件账本、配对、TLS 指纹校验与事件交换 |
| Android | `mobile/android/app/src/main/java/cn/si/companion/` | `MainActivity` 主界面，`CompanionView` 动画，`PetOverlayService` 悬浮，`EventLedger` 与 `PinnedPeer` 同步 |
| HarmonyOS | `mobile/harmony/entry/src/main/ets/` | ArkUI 页面、能力入口、NativeTls、账本文件和 TS 协议核心 |
| QQ 原型 | `si-qq-bot/main.py`、`core.py`、`group_core.py`、`onebot_adapter.py` | QQ 登录连接、管理员绑定、私聊与群聊、消息段和发送确认 |
| 多模态实现参考 | `model_connection.py`、`model_router.py`、`image_prepare.py`、`media_support.py`、`service_reply.py` | 模型连接、识图、附件准备、媒体指令分流 |
| 本地媒体能力 | `voice_reply.py`、`outbound_images.py`、`image_prompt.py`、`image_review.py`、`local_resources.py` | VoiceStudio 合成、ComfyUI 生图、描述转换、画面核对和模型资源切换 |
| 人设与资料 | `companion_style.py`、`knowledge_rag.py`、`knowledge/`、`si-language-lab/` | 表达约束、带来源的词项检索、语言实验脚本和样本 |

表中桌宠 Python 模块名相对 `si-desktop-pet/src/si_pet/`，QQ Python 模块名相对 `si-qq-bot/`。两个工程各有 `si_pet` 命名空间和独立数据位置。开发共享逻辑时使用明确的模块边界，防止从错误的 `si_pet` 导入，也避免把 OneBot 消息段直接变成桌宠的存储格式。

## 版本状态与边界

| 模块 | 当前源码与已知证据 | 待完成 |
|---|---|---|
| Windows 2.0.0a9 | 有常规窗口行为、固定情境台词、自由对话和局域网预览 | 把 QQ 验证过的多模态能力接进桌宠交互界面，补实际收发验收 |
| a10 候选 | 修复隐藏期间继续移动、恢复后突然移动、拖拽状态残留；已有隔离源码和自动回归记录 | 前台门控的全屏及恢复实测、独立打包与发布验收 |
| Android 0.1.3-preview / 1007 | 原生 Java，一台手机与一台 PC 的原生 TLS 配对和事件交换有部分实机证据 | 多模态会话、新版完整悬浮、长后台、断网恢复与多设备实测 |
| HarmonyOS 0.1.0 | ArkTS 工程预览，公开 API 检查与 Node/Python 回环有记录 | 商用 SDK 构建、签名、HAP、平板验证 |
| QQ 0.2.5 | 已试过图文理解、附件、语音回复和图片发送。较早的媒体测试中，用户确认图片可见、语音可播放。0.2.5 简化绘图样例有 QQ 发送回执 | 0.2.5 生成图在接收端的查看、原始严格绘图需求稳定性、冷启动与故障可见性。语音输入 ASR 仍是 `experimental/voice_reply_v1/` 中的原型 |
| 人设和知识 | 53 段词项检索，来源分为官网、社区和用户角色设定 | 人设仍需真实体验验收，向量索引待评估。语言实验有 30 条待审样本与 24 条留出题，未训练语言 LoRA |
| 新形象 | 072 局部合成抠图草稿在 `art-source/v1.3/si-game-regional-fusion-v2/cutout.png`，路径相对桌宠 | 边缘、发丝、尾部和完整动作仍待验收。运行帧仍用旧图集。原始游戏截图和需要它们的重建步骤留在原机器 |

同步账本目前交换互动事件、手记和有限偏好等数据。聊天消息、文件、图片、语音的跨端传输属于新开发。接手时从现有配对及 TLS 机制出发，先定义消息和附件在设备间的身份、完整性、持久化和恢复规则。旧协议说明见 [同步协议](../si-desktop-pet/docs/SYNC_PROTOCOL-2.0.md)。

绘图关键回归样例为“一个红色陶瓷杯子放在蓝色桌面中央，白色背景，没有人物”。0.2.5 简化题“一个红色杯子、没有人物”有成功回执，完整约束仍有失败。记录提示词转换、生成图核对、发送接受和接收端查看四阶段结果。有关已保存证据，见 [QQ 发布验证](evidence/release-0.2.5-validation.json) 与 [a10 原生测试记录](evidence/desktop-a10/native-visibility-pause.json)。
