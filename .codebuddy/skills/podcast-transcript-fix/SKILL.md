---
name: podcast-transcript-fix
description: 校验和修正 vibevoice-asr 输出的 transcript.md，重点处理英文专有名词、技术术语、中英混杂被识别成相近发音中文等 ASR 常见错误。原地写回 transcript.md。当用户说"修正转录"、"校验 transcript"、"修 ASR 错误"、"fix transcript"、"transcript 校对"，或在生成纪要前先要把转录文本提质时使用。
---

# podcast-transcript-fix

ASR 校验阶段。**只做转录文本的事实纠错**，不重写、不精简、不改风格。

## 输入 → 输出

- **输入**：`<episode_dir>/transcript.md`（由 [`podcast-transcribe`](../podcast-transcribe/SKILL.md) 产出）
  - 同目录下的 `README.md`（shownotes）作为修正参考来源（嘉宾名、公司名、产品名通常都在 shownotes 里）
- **输出**：原地写回 `<episode_dir>/transcript.md`（修正版）

## 何时跳过本阶段

| transcript 来源 | 需要 transcript-fix? | 原因 |
|---|---|---|
| podcast-transcribe（本地 GPU ASR） | **是** | vibevoice 可能有专名错误 |
| podcasttranscript-fetch（AI 转录） | **是** | 平台 AI 转录，可能有专名错误 |
| subtitle-fetch（人工字幕） | 否 | 人工字幕质量高，直接进 summary |
| volcengine-asr（火山云） | 否 | 火山大模型 ASR 质量较好 |

## 核心校验清单（按出错频率排序）

1. **英文专有名词**
   - 公司名：Anthropic、OpenAI、NVIDIA、TSMC、Google DeepMind、Meta、Microsoft、Amazon、Tesla、Apple…
   - 产品名：Codex、GPT、ChatGPT、Gemini、Llama、CoWoS、HBM、CUDA、TPU、MoE…
   - 人名：Sam Altman、Jensen Huang、Dario Amodei、Demis Hassabis…

2. **中英混杂被吃成相近发音的中文**
   - "Anthropic" → 容易被识别成"安瑟若皮克"或被丢音节
   - "CoWoS" → 容易变成"扣沃斯"
   - "transformer" → 容易变成"传送门"或"特兰斯佛默"
   - "scaling law" → 容易变成"斯凯林"

3. **技术 / 行业术语**
   - 半导体：先进制程、3nm、2nm、HBM3E、CXL、NVLink…
   - AI：scaling law、emergent capability、RLHF、test-time compute、推理时算力…
   - 投资：DCF、EV/EBITDA、free cash flow、自由现金流…

4. **数字 / 单位 / 时间节点**
   - "10 亿美金" 不要被识别成"100 亿"或"1 亿"
   - "Q4 2024" 不要被吃成 "去年 Q4"
   - 百分比、比例、时间点都要再核一遍上下文是否合理

5. **上下文校验**
   - 词语在语境中是否说得通？
   - 同一个嘉宾前后用词是否一致？
   - 数字前后呼应（嘉宾说 "10 倍" 后面又说 "10x" 应该一致）

6. **切片重叠区**（仅长播客）
   - >100 min 的播客在 70 min 边界 ±30s 会有重复说话
   - 如果发现两段几乎相同的连续句子，删除重复，保留语义完整的那份

## 操作流程

1. 通读 `transcript.md`（不要跳读）
2. 同时打开 `README.md`，把里面提到的人名、公司名、产品名、关键术语记成"白名单"
3. 用白名单回头扫一遍 transcript，逐个修正错误识别
4. 检查切片合并的重叠区
5. 用 `Edit` / `Write` 工具原地写回（**不创建 transcript.fixed.md**，下游 skill 只看 transcript.md）

## 不做的事

- ❌ **不要重写表达** — ASR 风格是"对话流"，不是书面语，保持原貌
- ❌ **不要补全省略号 / 修辞** — 嘉宾"嗯""啊""那个"原样保留
- ❌ **不要合并段落** — speaker 标签和换行结构不动
- ❌ **不要总结 / 精简** — 这是逐字稿，不是纪要
- ❌ **不要改 speaker 标签** — `[Speaker 1]` / `[Speaker 2]` 谁是谁的判断留给 [`podcast-summary`](../podcast-summary/SKILL.md)

## 典型修正示例

| 修正前（ASR 原文） | 修正后 | 错误类型 |
|---|---|---|
| "安瑟若皮克的克劳德三点五" | "Anthropic 的 Codex 3.5" | 公司+产品名 |
| "黄仁勋说苦窝丝技术…" | "黄仁勋说 CoWoS 技术…" | 技术术语 |
| "他们用了一个传送门架构" | "他们用了一个 transformer 架构" | 中英混杂 |
| "这家公司估值十亿美金" | "这家公司估值 100 亿美金"（如果上下文是大公司） | 数量级 |
| "2014年发布"（实际谈论 GPT-4） | "2024年发布" | 时间节点 |

## 下一步

修正完毕后调用 [`podcast-summary`](../podcast-summary/SKILL.md) 基于干净的 transcript 生成详尽纪要。

完整流水线参考 [`podcast-pipeline`](../podcast-pipeline/SKILL.md)。
