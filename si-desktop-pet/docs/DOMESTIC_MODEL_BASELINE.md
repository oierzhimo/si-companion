# 国内开源模型选择

2026-10-27，管理员明确要求不使用 GPT，改用国内开源大模型。本机上科大 GenAI 反代实际返回了 `DeepSeek-V4.1-Flash` 和 `GLM-5.3-Flash`，均标记聊天能力可用。

默认选择 **DeepSeek-V4.1-Flash**，另提供 GLM-5.3-Flash 作为用户主动选择的替代。已查看发布方的模型卡：[DeepSeek 官方模型卡](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash)、[智谱官方模型卡](https://huggingface.co/zai-org/GLM-5.3-Flash)，两者均标记 MIT 许可。这里核对的是对应公开型号；学校反代路由的具体权重版本及部署环境由学校服务提供，客户端不冒称已经在这台 PC 上下载或自托管这些权重。

客户端仍连接 `http://127.0.0.1:8000/v1`，由已登录的本机反代访问学校服务；学校凭据仅由反代自行使用，不进入桌宠设置或发行包。GenAI 预设严格限制为上述两款，其他型号和 GPT 都会被拒绝；普通本机模型保留自部署模型名称的兼容性，按当前实现只拒绝名称中带 GPT/OpenAI/ChatGPT 的型号，并不核验其他任意名称是否确属国产开源模型。

旧 `qa/genai-live-v1`、`qa/genai-live-v2`、`qa/genai-ui-live-v1` 的 GPT 请求已在本次要求之前完成，结果保留为历史记录，不算当前模型验收，旧工具真实联网入口已停用。DeepSeek 的 `qa/genai-live-v3` 已实际完成六轮、保存和重载；`qa/genai-ui-live-v2` 已从源码 Windows Qt 界面真实发送，模型列表恰为 DeepSeek/GLM，所记源码哈希与本次构建一致。132 项测试通过。技术通路通过，长期角色质量仍待验收；冻结 EXE 界面真实发送尚未验证。

角色资料仍来自管理员确认的 111 框架 V1.2 文档；模型选择不改变祀与管理员已经相熟、信任并结为伙伴的关系基线。软件代码继续由 Claude Code 协作实现，未改其现有配置。
