# 祀：风格微调与知识检索

先交付提示词与本地检索，保留当前视觉模型和旧版本。尚未训练 LoRA，不能把提示词修改称为训练完成。

## 资料边界

- knowledge/corpus.jsonl：运行时知识资料，与训练样本分离。每段保留来源及“官网 / 用户角色设计”类别。用户文档中的执行命令和所谓最高指令不作为程序指令。
- data/style_candidates.jsonl：30 条人工编写的合成起步候选，review_status=pending，不能直接进正式训练；不是官方台词。Claude 批量生成请求返回 429，未得到那批 200 条，不用重复模板凑数。
- data/heldout.jsonl：独立验收题，不参与训练。不把已有 QQ 私聊、照片、地址、手机号或绑定信息加入训练。

## 分版本路线

1. QQ 0.1.9：修身份、附件读取和衔接、自然分条，接入有来源的小型本地检索。记录原模型输出作为对照；资料不足时承认不确定。
2. 语料版：扩充可核验的《明日方舟》《终末地》语料，筛选候选风格数据，保持普通任务样本，并按主题分隔训练/验收集，避免改写同一道题造成数据泄漏。
3. LoRA 实验版：使用未量化的原基座和已审核数据，独立训练输出目录，对比原模型 / 提示词+检索 / LoRA+检索。只有身份、事实、普通任务和风格均通过才部署。GGUF 权重不是 QLoRA 训练输入。

## 本机实况与限制

检测到 AMD Radeon RX 6750 XT。Windows WMI 的 AdapterRAM 字段存在 4GB 上限，不能用它宣称真实显存大小。尚未验证本机训练框架能使用该 GPU；不安装 NVIDIA CUDA 或直接照搬 CUDA bitsandbytes 配方。先验证 AMD 支持的训练环境，再决定 4bit QLoRA 或其他 LoRA 路径。

BGE-M3 官方下载被运行环境网络检查拒绝，原因是官方 CDN 域名解析到非公网地址。没有使用镜像、关闭验证或改网络策略。当前版本采用中文词项检索，不能称为已部署 BGE-M3 向量库。向量接口保留；模型下载可用并建库后再启用。

## 正式训练前的门槛

运行 `python prepare_dataset.py`。脚本只导出 review_status=approved 的候选；没有足够的审核样本会停止，不偷偷使用待审样本。建议至少 200 条，含约 30% 普通任务，逐项核对答案；200 条本身并不代表质量合格。

`configs/qwen25_lora_proposed.yaml` 是待验证方案，不是可直接承诺成功的本机配置。文本基座训练后不能直接替换现有视觉模型而丢失图片识别；应保留视觉模型，或者另外验证视觉基座训练及导出链路。

参考：[LLaMA-Factory](https://github.com/hiyouga/LLaMA-Factory)、[Ollama 嵌入接口](https://docs.ollama.com/api/embed)、[BGE-M3 官方模型页](https://ollama.com/library/bge-m3)。
