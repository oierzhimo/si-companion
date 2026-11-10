# QQ 语音隔离原型

这是一组可导入的本地原型，尚未接入主程序、打包或连接真实 QQ。未自动发送任何消息。保留原聊天账本、资料、声线档案和既有发布包。

`voice_reply.prepare_reply` 使用本机 `127.0.0.1:3900` 的 VoiceStudio `audiocpp` 和管理员选择的声线 A（`06332768`）。它先将回复按句子、停顿切为每段最多 120 字，最多 5 段；所有段均合成成功后，校验声道、采样宽度、采样率及压缩类型一致，在内存中拼接 PCM 为一个 WAV 和一条 OneBot `record` 消息。合并后的音频最多 45 秒、2 MB；超限、格式不一致、过长、服务异常或无效 WAV 时返回完整文字段，并保留原文与状态。默认行为是否启用由上层设置明确控制，`reply_mode_command` 仅识别独立的 `/语音`、`/文字`，调用方必须先验证发送者为已绑定管理员，并自行持久化模式。

`delivery.Delivery` 为早期多段设计留下的纯状态机，不读写文件，也不调用 QQ；当前方案只需一次发送，不要求把多段流程用于主程序。主程序仍需在发出单条语音前持久化待发送记录，收到明确回执后才记为已送达；超时、断线或重启时结果可能不明，不能自动重发语音或补发全文，须在本机保留原文并提示用户处理。实际 QQ 发送、状态持久化与恢复 UI **尚未实现**。

`asr_client.transcribe_wav` 只接受经过大小、时长和 PCM 检查的 WAV 字节，并将其送往同一 VoiceStudio 的 `/transcribe`；未绑定或非管理员不应触发该流程。VoiceStudio 源码提供此接口，但模型及实际识别尚未验证。官方 [NapCat 文件文档](https://napneko.github.io/develop/file) 说明 `get_record` 可将收到的语音转换为 WAV；上层须仅对已授权管理员收到的 record 调用该接口，校验返回的本机文件路径与大小，不能直接信任消息里任意路径或 URL。不能将文本或语音写进普通日志；识别文本进入学校 GenAI 反代前需在界面说明。录音文件清理由其所有者负责，不删除已有资料。

官方 [OneBot 消息段](https://napneko.github.io/onebot/segment) 指定 `record.file` 可用 Base64；[实现](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-onebot/api/msg.ts) 将 record 转为 PTT。文件文档描述 NapCat 的转码流程，但不能据此断言本机 QQ 已成功播放。必须在用户授权后，以专用账号、已绑定管理员和本人手机验收短/多段语音、文本失败回退及断线未知状态。

离线验证：`python -m unittest -v test_voice_reply test_delivery test_asr_client`，17 项通过，覆盖中途失败、参数不一致和合并后时长及大小超限，均回退完整文字。将已有 A 声线样本在内存中复制拼接为 34.56 秒、24 kHz 单声道 WAV，大小 1,658,924 字节，未发送。合成数据与既有声线样本只证明构造和状态分支；尚未验证 VoiceStudio 当前服务状态、ASR 模型、真实 QQ 语音发送或收听。不可将此原型视为已发布功能。
