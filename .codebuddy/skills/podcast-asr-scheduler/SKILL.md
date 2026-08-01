---
name: podcast-asr-scheduler
description: 播客/视频转录调度器。按优先级依次尝试官方文稿/字幕 → 火山云 ASR → 本地 GPU ASR，以最低成本拿到 transcript.md。当 podcast-pipeline 需要决定走哪条转录路径时，或用户说"调度转录""选 ASR 路径""自动选转录方式"时使用。本 skill 是编排层，不引入新脚本，只描述决策树和降级规则。
version: 1.0.0
---

# podcast-asr-scheduler

转录调度器。给定一个内容 URL，按**成本从低到高**的优先级尝试各种转录方式，直到拿到 `transcript.md`。

## 设计原则

1. **能不 ASR 就不 ASR**：官方文稿/字幕 > 任何 ASR
2. **能不用 GPU 就不用 GPU**：火山云 ASR > 本地 GPU ASR
3. **降级不报错**：上一级失败/未命中时，自动尝试下一级，只有最后一级失败才报错

## 优先级决策树

```text
输入: URL

┌─────────────────────────────────────────────────────────────┐
│  Priority 0: 官方文稿 / 平台字幕 (零成本, 无 ASR)            │
├─────────────────────────────────────────────────────────────┤
│  0a. podcasttranscript.ai/library/ URL                       │
│      → podcasttranscript-fetch                               │
│      → exit 0: 完成 (→ transcript-fix → summary)            │
│                                                              │
│  0b. youtube / youtu.be / bilibili / b23.tv URL             │
│      → subtitle-fetch                                        │
│      → exit 0: 完成 (AI字幕→transcript-fix→summary)         │
│      → exit 2: 需 ASR → 进入 Priority 1                     │
│      → exit 3/4/5: 报错, 不降级 (cookie/runtime 问题)       │
│                                                              │
│  0c. RSS / 小宇宙 / Apple / Spotify URL                     │
│      → podcast-fetch (内部 probe 官方 transcript)           │
│      → 命中官方 transcript: 完成 (→ transcript-fix → summary)│
│      → 未命中: 进入 Priority 1                               │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼ (需要 ASR)
┌─────────────────────────────────────────────────────────────┐
│  Priority 1: 火山云 ASR (需下载音频, 不需 GPU)              │
├─────────────────────────────────────────────────────────────┤
│  条件: 环境变量 VOLC_ASR_API_KEY 已设置                      │
│                                                              │
│  Step 1a: podcast-fetch 下载音频                             │
│    (README 会写入 "> Audio URL: <url>" 行)                  │
│    → 拿到 episode_dir                                        │
│                                                              │
│  Step 1b: volcengine-asr 云端转录                            │
│    python3 scripts/volc_asr.py --episode-dir <dir>           │
│    (从 README 解析 audio_url, 传给火山云)                    │
│                                                              │
│  → exit 0: 完成 (→ summary, 跳过 transcript-fix)             │
│  → exit 1: API key 缺失 → 降级到 Priority 2                 │
│  → exit 2: 调用失败 → 降级到 Priority 2 (音频已下载)        │
│  → exit 3: 超时 → 降级到 Priority 2 (音频已下载)            │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼ (火山云不可用或失败)
┌─────────────────────────────────────────────────────────────┐
│  Priority 2: 本地 GPU ASR (兜底, 需 GPU)                    │
├─────────────────────────────────────────────────────────────┤
│  条件: 本地有 GPU (nvidia-smi 可用)                         │
│                                                              │
│  ⚠️ LAZY INIT (首次使用时, 见下方"Level 2 懒加载流程"):      │
│    如果 Docker 镜像或模型未就绪, 不自动拉取,                │
│    而是评估磁盘 + 告知用户大小和收益 + 请求确认。            │
│                                                              │
│  前置:                                                       │
│    - 如果 Priority 1 已下载音频 → 直接转录                   │
│    - 如果跳过了 Priority 1 (无 API key) → 先 podcast-fetch   │
│                                                              │
│  调用: podcast-transcribe (transcribe.sh)                    │
│    bash scripts/transcribe.sh <episode_dir>                  │
│                                                              │
│  → exit 0: 完成 (→ transcript-fix → summary)                 │
│  → exit 1: 失败 → 报错 (无更多降级)                          │
└─────────────────────────────────────────────────────────────┘
```

## Level 2 懒加载流程（关键）

当调度器降级到 Priority 2（本地 GPU ASR）时，**不要自动拉取模型和镜像**。按以下流程操作：

### 步骤 1: 检查 GPU

```bash
nvidia-smi >/dev/null 2>&1 && echo "GPU available" || echo "no GPU"
```

无 GPU → 报错，提示用户设置 `VOLC_ASR_API_KEY`（见 `docs/volcengine-asr-setup.md`）。

### 步骤 2: 检查 ASR 资产是否已就绪

```bash
# Docker 镜像
docker image inspect hxer7963/vibevoice-asr-vllm:latest >/dev/null 2>&1 && echo "image: ready" || echo "image: missing"

# 模型权重 (路径可通过 VV_MODEL_PATH 环境变量覆盖, 默认 /workspace/models/VibeVoice-ASR-vllm)
MODEL_DIR="${VV_MODEL_PATH:-/workspace/models/VibeVoice-ASR-vllm}"
[[ -f "$MODEL_DIR/config.json" ]] && echo "model: ready" || echo "model: missing"
```

如果两者都已就绪 → 跳到步骤 5 直接启动服务。

### 步骤 3: 预评估磁盘需求

需要下载的资产（如果缺失）：

| 资产 | 大小 | 说明 |
|---|---|---|
| Docker 镜像 `hxer7963/vibevoice-asr-vllm:latest` | ~5GB | vLLM + VibeVoice plugin + ffmpeg |
| 模型权重 `VibeVoice-ASR-vllm` | ~15GB | 8B 参数 vLLM 格式 checkpoint |
| **合计** | **~20GB** | |

检查可用磁盘空间：

```bash
df -h "${VV_MODEL_PATH:-/workspace/models}" | awk 'NR==2 {print $4 " available"}'
```

### 步骤 4: 告知用户并请求确认

向用户输出类似以下内容（**不要跳过确认直接下载**）：

```
本地 GPU ASR 需要下载约 20GB 资产：

  • Docker 镜像:  ~5GB  (hxer7963/vibevoice-asr-vllm:latest)
  • 模型权重:    ~15GB  (VibeVoice-ASR-vllm, 8B 参数)

下载是一次性的，后续转录不再重复下载。

性能预期（4× RTX 4090, tp=4）：
  • 60 分钟音频 → ~8 分钟转录（7.5x 实时）
  • 带 speaker 分离（区分不同说话人）
  • 无 API 费用（完全本地）

当前可用磁盘空间: <XX> GB

是否现在下载并启动本地 GPU ASR？
```

- 用户**确认** → 执行步骤 5
- 用户**拒绝** → 报错，提示改用火山云 ASR（设置 `VOLC_ASR_API_KEY`）

### 步骤 5: 下载并启动（用户确认后）

```bash
# 5a. 拉取 Docker 镜像 (如果缺失)
docker pull hxer7963/vibevoice-asr-vllm:latest

# 5b. 下载并转换模型 (如果缺失)
bash setup/download_vibevoice_model.sh

# 5c. 启动 vLLM 服务 (首次加载约 2-3 分钟)
bash vibevoice-asr/serve_vllm.sh start
```

### 步骤 6: 服务就绪后转录

```bash
bash scripts/transcribe.sh <episode_dir>
```

> **为什么懒加载？** 大多数用户首次使用时，官方字幕/文稿或火山云 ASR 已能解决问题，不需要下载 20GB 本地资产。懒加载避免了不必要的下载，只在真正需要时才请求确认。

> **扩展点**：Priority 0 和 Priority 1 之间可以插入一个"中央转录复用"层（例如自建的 transcript 缓存服务）。本仓库不内置此类私有源；如果你有自己的中央 transcript 仓库，可以在调度器里加一个 Priority 0.5 节点，命中即返回。

## 各级成本对比

| 优先级 | 方式 | 下载音频 | GPU | API 费用 | 耗时 | 输出 |
|---|---|---|---|---|---|---|
| 0a | podcasttranscript-fetch | 否 | 否 | 否 | <10s | 纯文本 |
| 0b | subtitle-fetch | 否 | 否 | 否 | <30s | 纯文本 |
| 0c | RSS 官方 transcript | 否 | 否 | 否 | <10s | 纯文本 |
| 1 | 火山云 ASR | 是 | 否 | 是 | 2-10min | 纯文本 |
| 2 | 本地 GPU ASR | 是 | 是 | 否 | 5-30min | 有 speaker 标签 |

## 各级 transcript 后处理差异

| 优先级 | 需要 transcript-fix? | 原因 |
|---|---|---|
| 0a (podcasttranscript) | 是 | AI 转录，可能有专名错误 |
| 0b (subtitle 人工) | 否 | 人工字幕质量高 |
| 0b (subtitle AI/自动) | 是 | 平台 AI 字幕可能有专名错误 |
| 0c (RSS 官方 transcript) | 是 | AI 转录 |
| 1 (火山云 ASR) | 否 | 火山大模型 ASR，质量较好 |
| 2 (本地 GPU ASR) | 是 | vibevoice 可能有专名错误 |

## 执行规则

### 规则 1: 逐级尝试，不跳级
必须从 Priority 0 开始，按顺序尝试。只有当前级明确未命中（返回降级退出码）才进入下一级。**禁止直接跳到 Priority 2**，除非 0/1 都明确不可用。

### 规则 2: 错误不降级
- subtitle-fetch 的 exit 3（cookie）/4（runtime）/5（不完整）是**错误**，不是"无字幕"，不得降级到 ASR
- 必须先解决 cookie/runtime 问题，或用 `--allow-partial` 处理不完整字幕

### 规则 3: 火山云前置条件
- 必须先 `podcast-fetch` 下载音频（README 写入 audio_url）
- 即使火山云只读 URL 不读本地音频，fetch 仍需执行以创建 episode_dir 结构
- 如果火山云失败，音频已下载，Priority 2 可直接用

### 规则 4: GPU 可用性检查
进入 Priority 2 前必须确认 GPU 可用：
```bash
nvidia-smi >/dev/null 2>&1 && echo "GPU available" || echo "no GPU"
```
无 GPU 时，如果 Priority 1 也不可用，报错并提示用户设置 `VOLC_ASR_API_KEY`（见 `docs/volcengine-asr-setup.md`）。

### 规则 5: 幂等
每一级都检查 `episode_dir/transcript.md` 是否已存在且 ≥100 字节。如果已有，跳过所有优先级直接返回。

## 快速判定流程

收到 URL 后，AI agent 按以下顺序判定：

```text
1. URL 包含 podcasttranscript.ai/library/?
   → 是: 调 podcasttranscript-fetch, 结束
   → 否: 继续

2. URL 包含 youtube.com / youtu.be / bilibili.com / b23.tv?
   → 是: 调 subtitle-fetch
     - exit 0: 结束 (有 transcript)
     - exit 2: 跳到 step 4 (视频需 ASR)
     - exit 3/4/5: 报错, 不降级
   → 否: 继续

3. 播客 URL (小宇宙/RSS/Apple/Spotify)
   → 调 podcast-fetch (内部 probe 官方 transcript)
     - 命中官方 transcript: 结束
     - 未命中: 继续

4. 需要 ASR — 检查 VOLC_ASR_API_KEY 环境变量
   → 已设置:
     - 如果 step 3 已 fetch (有 episode_dir): 调 volc_asr.py
     - 如果跳过了 step 3 (视频 ASR): 先 video_fetch.py 下载音频, 再 volc_asr.py
     - exit 0: 结束
     - exit 1/2/3: 降级
   → 未设置: 继续

5. 检查 GPU (nvidia-smi)
   → 有 GPU:
     - 如果已有 episode_dir + 音频: 进入 Level 2 懒加载流程
     - 否则: 先 podcast-fetch / video_fetch 下载, 再进入 Level 2 懒加载流程
     - (见上方"Level 2 懒加载流程", 不要自动拉取模型/镜像, 先告知用户大小和收益并请求确认)
   → 无 GPU:
     - 报错: "无可用转录路径。请设置 VOLC_ASR_API_KEY 或在 GPU 主机上运行"
```

## 与 podcast-pipeline 的关系

本 skill 是 `podcast-pipeline` 中 Stage 0（URL 路由）+ Stage 1（fetch + transcribe）的**决策大脑**。pipeline 调用本 skill 后，本 skill 决定调用哪些子 skill，最终产出 `episode_dir + transcript.md`。

后续阶段（transcript-fix / summary）由 pipeline 按标准链路执行，不受本 skill 影响。

## 子 skill 索引

| 优先级 | 子 skill | 脚本 | 退出码 |
|---|---|---|---|
| 0a | podcasttranscript-fetch | `podcasttranscript_fetch.py` | 0=成功 |
| 0b | subtitle-fetch | `subtitle_fetch.py` | 0=有字幕, 2=需ASR, 3/4/5=错误 |
| 0c | podcast-fetch (官方transcript probe) | `rss_download.py` 等 | 命中=有transcript |
| 1 | volcengine-asr | `volc_asr.py` | 0=成功, 1=无key, 2=失败, 3=超时 |
| 2 | podcast-transcribe | `transcribe.sh` | 0=成功, 1=失败 |
