# 火山引擎大模型 ASR 配置指南

本指南介绍如何开通火山引擎（Volcengine）大模型 ASR 服务，获取 API key，并配置到 podcast-summary 流水线。

火山云 ASR 的优势：**不需要本地 GPU**，直接把音频公网 URL 提交给火山云异步转录，输出带 utterance 时间和 speaker 标签的 Markdown，可直接进入 `podcast-summary`。

本仓库的最小火山云通信层只使用 Bash + curl。Python 标准库客户端是可选便利层；不要安装 uv、httpx、requests、jq 或 ffmpeg。

## 前置条件

- 火山引擎账号（支持支付宝/微信实名认证）
- 需要开通 Agent Plan 套餐（按需付费）

## 步骤 1：购买 Agent Plan 套餐

打开 [火山引擎 Agent Plan 订阅页](https://console.volcengine.com/ark/region:cn-beijing/subscription/agent-plan)，按需购买套餐。

> Agent Plan 是火山方舟（Ark）的按需套餐，包含大模型调用额度。录音文件识别 2.0 会消耗其中的额度。

## 步骤 2：开通录音文件识别 2.0

### 2.1 进入开通管理页面

打开 [豆包语音开通页](https://console.volcengine.com/speech/new/setting/activate?_vtm_=a86845.b103859.0_0.0_0.0.15_7668884661937997312&projectName=default)。

### 2.2 开通服务

在 **豆包语音 → 系统管理 → 开通管理 → 服务管理 → 大模型** 里，找到 **录音文件识别 2.0**，点击开通。

> 录音文件识别 2.0 是异步服务：提交 audio URL → 轮询查询 → 拿到文本和可用的 utterance/speaker 明细。支持中英混杂、长音频（实测 2h+ 正常）。

## 步骤 3：设置 API Key

### 3.1 进入 API 调用页

在 **豆包语音 → 语音识别** 页面右上角，点击 **API 调用**。

### 3.2 创建 API Key

在 API 调用页面创建一个新的 API key（形如 `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`）。

### 3.3 配置环境变量

把 API key 通过 agent 的 secret 管理器或当前进程环境提供给脚本：

```bash
export VOLC_ASR_API_KEY="<your-api-key>"
```

> **安全提示**：不要把 API key 放在命令行参数、README、仓库文件或聊天示例里。若 key 曾被公开粘贴，应立即在控制台撤销并轮换。

## 步骤 4：验证

设置好环境变量后，AI agent 会自动在 `podcast-asr-scheduler` 的 Priority 1 选用火山云路径。

手动验证：

```bash
# 只检查是否设置，不打印密钥
test -n "$VOLC_ASR_API_KEY" && echo 'VOLC_ASR_API_KEY: configured'

# 最小路径：curl-only，直接提交公网音频 URL
bash scripts/volc_asr.sh run \
  'https://example.com/episode.mp3' \
  'audios/cloud/example'

# 可选 Python 便利客户端：自动建目录/解析 JSON
python3 scripts/volc_asr.py --audio-url 'https://example.com/episode.mp3'

# 或复用 metadata-only handler 写出的 README（Python 便利客户端）
python3 scripts/volc_asr.py --episode-dir "audios/xiaoyuzhou/<podcast>/<episode>"
```

## 识别参数策略

| 参数 | 默认值 | 播客场景的理由 |
|---|---:|---|
| `enable_itn` | `true` | 把日期、数字、单位等转成更适合阅读和检索的形式 |
| `enable_punc` | `true` | 恢复标点，显著改善总结和长文本阅读 |
| `enable_ddc` | `true` | 语义顺滑更适合总结；严格逐字场景可关闭 |
| `enable_speaker_info` | `true` | 请求说话人区分，便于访谈角色归因 |
| `show_utterances` | `true` | 输出分句、时间等结构化明细；固定开启 |
| `enable_channel_split` | `false` | 仅双声道隔离录音适用，普通立体声不要开启 |
| `vad_segment` | `false` | 默认让模型保持语义分句；特殊长停顿素材再试开启 |
| `sensitive_words_filter` | `""` | 默认不静默删改原始内容 |

可用环境变量覆盖布尔项，例如：

```bash
# 真正的双声道隔离访谈
VOLC_ASR_CHANNEL_SPLIT=true bash scripts/volc_asr.sh run "$AUDIO_URL" "$EPISODE_DIR"

# 更接近逐字记录，关闭语义顺滑
VOLC_ASR_ENABLE_DDC=false bash scripts/volc_asr.sh run "$AUDIO_URL" "$EPISODE_DIR"
```

支持的覆盖项：`VOLC_ASR_ENABLE_ITN`、`VOLC_ASR_ENABLE_PUNC`、`VOLC_ASR_ENABLE_DDC`、`VOLC_ASR_SPEAKER_INFO`、`VOLC_ASR_CHANNEL_SPLIT`、`VOLC_ASR_VAD_SEGMENT`。

成功输出：

```
[volc-asr] submitting audio_url=https://... request_id=...
[volc-asr] submitted, polling every 10s (max 1800s) ...
[volc-asr] running (code=20000001), waiting 10s ...
[volc-asr] transcript written: audios/.../transcript.md (12345 chars)
TRANSCRIPT=audios/.../transcript.md
```

## 成本参考

- 录音文件识别 2.0 按音频时长计费（具体价格见火山引擎控制台）
- 价格和免费额度可能变化，以火山引擎控制台当前信息为准
- Agent Plan 套餐有月度额度，超出后按量付费

## 与本地 GPU ASR 的对比

| 维度 | 火山云 ASR | 本地 GPU ASR (vibevoice) |
|---|---|---|
| GPU | 不需要 | 需要 4× RTX 4090 |
| 部署 | 5 分钟（开通服务 + API key） | 1-2 小时（下载模型 + Docker） |
| speaker 标签 | 有（云端 diarization） | 有（本地 diarization） |
| 耗时 | 2-10 分钟/集（含排队） | 5-30 分钟/集（取决于音频长度） |
| 成本 | 按控制台当前计费 | 电费 + GPU 折旧 |
| 隐私 | 音频 URL 传给火山云 | 完全本地 |

## 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `VOLC_ASR_API_KEY is not set` | 环境变量未配置 | 重新 `export VOLC_ASR_API_KEY=...` |
| `submit failed: code=...` | API key 无效 / 服务未开通 / 配额耗尽 | 检查控制台开通状态和余额 |
| `polling timed out` | 音频过长或服务排队 | `--max-wait 3600` 重试 |
| `audio is silent` | 音频损坏或全静音 | 检查 audio_url 是否有效 |
| `no --audio-url given` | README 里没有 `> Audio URL:` 行 | 手动传 `--audio-url` 或检查 fetch 是否写了该行 |
