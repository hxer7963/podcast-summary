# 架构：信源抓取多级结构与流水线

本文档说明 podcast-summary 的整体架构，重点是**信源抓取的多级结构**和**转录调度的优先级决策树**。

## 整体流水线

```
URL ──▶ podcast-asr-scheduler (调度大脑) ──▶ episode_dir + transcript.md
                                                  │
                                                  ▼
                                          podcast-transcript-fix (仅本地 GPU 路径)
                                                  │
                                                  ▼
                                          podcast-summary
                                                  │
                                                  ▼
                                          <episode_dir>/{basename}.md  (中文深度纪要)
```

本仓库到 summary 为止。后续的归档、标签、推送等阶段由下游项目自行实现。

## 信源抓取的多级结构

podcast-summary 支持多级信源抓取，按**成本从低到高**的优先级尝试，命中即返回：

### Level 0：零成本（无 ASR，无音频下载）

#### 0a. PodcastTranscript.ai 公共库

- **URL 形式**：`podcasttranscript.ai/library/<slug>`
- **子 skill**：`podcasttranscript-fetch`
- **脚本**：`scripts/podcasttranscript_fetch.py`
- **成本**：无（公开只读 API，每 IP 每分钟 60 次）
- **输出**：`transcript.md`（纯文本，无 speaker 标签）
- **适用**：已知 PodcastTranscript.ai 上有该集文字稿

#### 0b. YouTube / Bilibili 官方字幕

- **URL 形式**：`youtube.com/watch?v=`, `youtu.be/`, `bilibili.com/video/`, `b23.tv/`
- **子 skill**：`subtitle-fetch`
- **脚本**：`scripts/subtitle_fetch.py`
- **成本**：无（仅下载字幕文件）
- **输出**：`transcript.md`（纯文本）
- **适用**：视频平台有字幕（人工或 AI 自动）
- **退出码**：
  - `0` = 有完整字幕
  - `2` = 无字幕，需 ASR（进入 Level 1）
  - `3/4/5` = 错误（cookie/runtime/不完整），不降级

#### 0c. RSS / 小宇宙 / Apple / Spotify 官方 transcript

- **URL 形式**：RSS feed, `xiaoyuzhoufm.com/`, `podcasts.apple.com/`, `open.spotify.com/`
- **子 skill**：`podcast-fetch`（内部 probe 官方 transcript）
- **脚本**：`scripts/podcast_transcript_probe.py` + `rss_download.py`
- **成本**：无（探测 RSS `<podcast:transcript>` 标签或 description 里的 transcript 链接）
- **输出**：`transcript.md`（纯文本）
- **适用**：播客在 RSS feed 里附带了官方 transcript（如 Lex Fridman）
- **未命中**：进入 Level 1

### Level 1：火山云 ASR（只传公网 URL，不下载音频，不需 GPU）

- **条件**：环境变量 `VOLC_ASR_API_KEY` 已设置
- **子 skill**：`volcengine-asr`
- **脚本**：`scripts/volc_asr.sh`（curl-only）或可选 `scripts/volc_asr.py`
- **成本**：按火山引擎控制台当前套餐与音频时长计费
- **输入**：音频公网 URL（从 `episode_dir/README.md` 的 `> Audio URL:` 行解析）
- **本地环境**：最小路径只需 Bash + curl；不需要 Python、uv、httpx、ffmpeg
- **输出**：`transcript.md`（speaker 标签，不带 timestamp）和原始 `volc-response.json`（保留 utterance 时间）
- **适用**：不想占用本地 GPU，或没有 GPU
- **配置**：见 `docs/volcengine-asr-setup.md`
- **降级**：API key 缺失 / 调用失败 / 超时 → 进入 Level 2

### Level 2：本地 GPU ASR（兜底，需 GPU）

- **条件**：本地有 GPU（`nvidia-smi` 可用）
- **子 skill**：`podcast-transcribe`
- **脚本**：`scripts/transcribe.sh` → `vibevoice-asr/transcribe_vllm.py`
- **成本**：电费 + GPU 折旧（无 API 费用）
- **输入**：本地音频文件（`*.m4a` 或 `*.mp3`）
- **输出**：`transcript.md`（带 speaker 标签）
- **适用**：有 GPU 主机，想要 speaker 分离，或不想付费
- **配置**：见 `docs/vibevoice-local-setup.md`
- **失败**：报错（无更多降级）

## 各级对比

| 级别 | 方式 | 下载音频 | GPU | API 费用 | 耗时 | speaker 标签 | 需要 transcript-fix |
|---|---|---|---|---|---|---|---|
| 0a | PodcastTranscript | 否 | 否 | 否 | <10s | 无 | 是 |
| 0b | subtitle-fetch | 否 | 否 | 否 | <30s | 无 | 人工否，AI 是 |
| 0c | RSS 官方 transcript | 否 | 否 | 否 | <10s | 无 | 是 |
| 1 | 火山云 ASR | 否 | 否 | 是 | 2-10min | 无 | 否 |
| 2 | 本地 GPU ASR | 是 | 是 | 否 | 5-30min | 有 | 是 |

## URL 自动路由

收到 URL 后，`podcast-pipeline` 自动分类并路由：

| URL 模式 | 自动路由 |
|---|---|
| `podcasttranscript.ai/library/` | → `podcasttranscript-fetch` |
| `youtube.com` / `youtu.be` | → `subtitle-fetch` |
| `bilibili.com/video` / `b23.tv` | → `subtitle-fetch` |
| `xiaoyuzhoufm.com/episode` | → `podcast-fetch`（小宇宙下载） |
| `xiaoyuzhoufm.com/podcast` | → `podcast-fetch`（整档播客） |
| RSS feed URL | → `podcast-fetch`（RSS 下载） |
| `podcasts.apple.com/.../id<digits>` | → `podcast-fetch`（Apple → iTunes Lookup → RSS） |
| `open.spotify.com/{episode,show,playlist}` | → `podcast-fetch`（Spotify embed → iTunes Search → RSS） |

## Handoff 契约

所有子 skill 围绕单一概念 **`episode_dir`**（绝对路径）：

```
episode_dir/
├── README.md           # shownotes / 元数据
├── *.m4a / *.mp3       # 音频文件（仅 Level 2 本地 ASR 需要）
├── transcript.md       # 文字稿（所有路径最终产出）
├── {basename}.md       # 中文深度纪要（podcast-summary 产出）
└── (可选) source.json, subtitle_status.json, asr-required.json, tags.json
```

每个子 skill 的输入是 `episode_dir`，输出是在其中新增文件。幂等检查：已有产物则跳过。

## 扩展点

### 新增播客源

在 `scripts/` 下写一个 `<source>_fetch.py`，要求：
- 接受 URL 作为位置参数
- `--output-dir <dir>` 指定基目录
- `--no-transcribe` 选项
- 成功时 stdout 打印 `✓ Episode complete: <ep_dir>`

然后在 `.codebuddy/skills/podcast-fetch/SKILL.md` 的路由表中加一行。**其他 skill 完全不动**。

### 新增 ASR 后端

写一个新的 `<backend>-asr` skill，输入是 `episode_dir`，输出是 `transcript.md`。然后在 `podcast-asr-scheduler` 的决策树中插入一个新的优先级节点。

### 新增中央 transcript 复用（私有扩展）

如果你有自己的中央 transcript 缓存服务（例如自建的 transcript 仓库），可以在 `podcast-asr-scheduler` 的 Level 0 和 Level 1 之间插入一个 Level 0.5 节点。本仓库不内置此类私有源。

### 下游扩展（归档 / 推送）

本仓库到 summary 为止。如果你需要：
- **归档**：按公司/人物/领域分类归档纪要
- **标签**：自动生成 tags.json
- **推送**：推送到知识星球 / Notion / Obsidian 等

可以参考原始项目的设计，在 `podcast-summary` 之后追加 `podcast-tag` → `podcast-archive` / `podcast-push` 阶段。每个阶段只看 `episode_dir`，不在乎前面走了哪条转录路径。

## 环境分层

| 阶段 | 环境 | CUDA | 说明 |
|---|---|---|---|
| Skill/summary/能力检测 | Python 3.10+ 标准库 | 无 | 默认安装 0 个包 |
| 小宇宙元数据/Audio URL | Python 3.10+ 标准库 | 无 | 云端路径不下载音频 |
| 火山云 ASR | Bash + curl | 无 | Python/jq 均为可选，不安装 |
| PodcastTranscript | Python 3.10+ 标准库 | 无 | 不需要 uv 或 ffmpeg |
| RSS/Apple/Spotify | `uv run --group fetch` | 无 | 仅收到对应 URL 时安装 |
| 字幕抓取 | `uv run --group subtitle` | 无 | 仅视频 URL 使用；纯字幕无需 ffmpeg |
| 本地 GPU ASR | 专用 Docker | 是 | 仅检测到 GPU 且用户确认后懒加载 |

**禁止**在 Intel Mac 或无 GPU 的机器上安装 `requirements-asr.txt`（含 CUDA 依赖）。
