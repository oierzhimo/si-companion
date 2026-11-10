# 小祀项目接手说明

请在当前仓库接手小祀桌宠。先读 [README](README.md)、[项目介绍](docs/PROJECT.md)、[接手指南](docs/TAKEOVER.md)、[首轮开发任务](docs/FIRST_TASK.md) 与 [复验记录](docs/VERIFICATION.md)。

QQ 是交互蓝本和测试入口。第一项交付是 Windows 桌宠的「自由聊聊」可以理解文字、图片、文件和语音，并展示或播放相应的回复。先在隔离环境复现 a9 的启动和测试基线，再沿正常发送入口实现最小链路。可以从 QQ 原型复用模型、附件及媒体处理逻辑。OneBot 消息格式、账号与群聊规则放在 QQ 适配层。

使用合成消息和临时数据验证成功、失败、重试和旧纯文本历史。a10 是独立候选，按 [候选说明](candidates/desktop-a10/README.md) 复现。完成 Windows 的交互验证后，再做 Windows 与 Android 的跨端续聊。交付时记录实际运行的测试和真实服务或设备仍需验证的场景。
