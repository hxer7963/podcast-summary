---
name: podcast-pipeline
description: 播客与视频研究流水线编排器。从 URL 自动识别普通播客、YouTube、Bilibili 或 PodcastTranscript.ai，也支持按 topic 搜索 PodcastTranscript 公共文字库；优先复用已有完整文字稿，否则下载音频并转录，再执行 transcript-fix、summary。当用户说"处理这一集播客""处理这个视频""按 topic 拉播客文字稿""跑完整视频/播客流水线""download a podcast"或要求端到端把一集播客从 URL 做成中文深度纪要时使用。
---

# podcast-pipeline

> 薄编排层。所有实际工作由 9 个独立子 skill 完成，每个子 skill 都有幂等检查和明确的输入输出契约。

完整流水线（视频 URL 先走字幕快速路径）：

```
URL ──▶ podcast-asr-scheduler ──▶ episode_dir + transcript.md
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

`podcast-asr-scheduler` 是转录调度大脑，按优先级依次尝试：官方文稿/字幕 → 火山云 ASR → 本地 GPU ASR。详见 [`podcast-asr-scheduler`](../podcast-asr-scheduler/SKILL.md)。

```text
YouTube/Bilibili URL ──▶ subtitle-fetch ──┬─ transcript 完整 ─▶ [AI字幕: transcript-fix] ─▶ summary
                                         └─ 无字幕 ─▶ asr-required.json ─▶ Linux video-fetch + transcribe
```

## 子 skill 索引

| 阶段 | 子 skill | 一句话职责 |
|---|---|---|
| 0 | [`podcast-asr-scheduler`](../podcast-asr-scheduler/SKILL.md) | **转录调度大脑**：按优先级决策走哪条转录路径 |
| 1t | [`podcasttranscript-fetch`](../podcasttranscript-fetch/SKILL.md) | PodcastTranscript URL/topic → README + 完整 transcript，无需音频/ASR |
| 1v | [`subtitle-fetch`](../subtitle-fetch/SKILL.md) | 视频 URL → README + transcript；无字幕则生成 GPU ASR 交接 |
| 1a | [`podcast-fetch`](../podcast-fetch/SKILL.md) | URL → episode_dir（含 README + 音频）。**新增源只改这里** |
| 1b | [`podcast-transcribe`](../podcast-transcribe/SKILL.md) | 音频 → transcript.md（vibevoice-asr，本地 GPU） |
| 1c | [`volcengine-asr`](../volcengine-asr/SKILL.md) | audio URL → transcript.md（火山云大模型，无需 GPU / 无需本地音频） |
| 2a | [`podcast-transcript-fix`](../podcast-transcript-fix/SKILL.md) | 修正 ASR 错误（英文专名、技术术语、中英混杂） |
| 2b | [`podcast-summary`](../podcast-summary/SKILL.md) | 生成详尽纪要 `{basename}.md` |

> 本仓库到 summary 为止。后续的归档 / 推送 / 标签等阶段由下游项目自行实现，参考 `docs/architecture.md` 的扩展点说明。

## Handoff 契约（所有 skill 共享的状态）

所有子 skill 都围绕单一概念 **`episode_dir`**（绝对路径）。每一步执行后，目录里多出哪些文件：

| 步骤之后 | episode_dir/ 里新增的文件 |
|---|---|
| podcasttranscript-fetch | `README.md`, `transcript.md`, `source.json`；可选 `transcript.json` |
| subtitle-fetch | `README.md`, `transcript.md`, `subtitle_status.json`；无字幕时改为 `asr-required.json` |
| fetch | `README.md`, `*.m4a` / `*.mp3` |
| transcribe | + `transcript.md` |
| volcengine-asr | + `transcript.md`（无需音频文件，直接传 audio URL） |
| transcript-fix | `transcript.md` 原地修正 |
| summary | + `{basename(episode_dir)}.md` |

## 端到端执行（标准链路）

> **强制规则**：以下每一个 Stage 都**必须**通过对应的 sub-skill 执行。**严禁**绕过 skill 直接调用底层脚本（如 `xiaoyuzhou_download.py`、`transcribe.sh`）。每个 sub-skill 内置了校验、查重、幂等检查等防护逻辑，绕过 skill 会导致重复工作、内容缺失等问题。

### Stage 0：URL 自动路由

收到 URL 后必须先分类，不得要求用户手工运行 fetch 命令：

| URL | 自动路由 |
|---|---|
| 包含 `podcasttranscript.ai/library/` | 立即调用 `podcasttranscript-fetch`，然后从 transcript-fix 继续 |
| 包含 `youtube.com` 或 `youtu.be` | 立即调用 `subtitle-fetch` |
| 包含 `bilibili.com/video` 或 `b23.tv` | 立即调用 `subtitle-fetch` |
| 其他 URL | 调用 `podcast-fetch` |

用户要求"从 PodcastTranscript 按 topic/关键词搜索"时，不要求 URL，直接调用：

```bash
python3 scripts/podcasttranscript_fetch.py --topic "<topic>" --limit <N>
```

从每行 `✓ Episode complete:` 提取 `episode_dir`。

`subtitle-fetch` 子 skill 必须执行以下确定性入口；Linux 会自动读取标准 cookie 目录：

```bash
uv run --group subtitle python scripts/subtitle_fetch.py "$URL"
```

执行时将 `$URL` 替换为用户提供的完整 URL。从 stdout 的 `✓ Episode complete:` 或 `⚠ ASR required:` 提取绝对 `episode_dir`，并保留进程退出码作为后续路由的唯一依据。不得因目录中已有旧文件而忽略本次退出码。

### YouTube / Bilibili 字幕优先链路

1. 先调用 `subtitle-fetch`，不得先下载音频。
2. 若退出 0 且 `subtitle_status.json.result` 为 `complete`：
   - 人工字幕直接进入 `podcast-summary`。
   - `track_type` 为 `ai` 或 `automatic` 时先调用 `podcast-transcript-fix`，再进入 summary。
3. 若退出 2：
   - macOS 保留 `README.md` + `asr-required.json` 后停止，不得生成 summary。
   - Linux GPU 主机自动调用 `podcast-fetch` 的视频回退入口 `uv run --group subtitle python scripts/video_fetch.py --handoff <episode_dir>/asr-required.json --no-transcribe`，再依次调用 `podcast-transcribe` 和 `podcast-transcript-fix`；不得要求用户手工续跑。
4. 若退出 3/4/5，先解决 cookie、runtime 或字幕完整性问题，不得将失败误判为"无字幕"。
5. transcript 就绪后复用标准的 summary 链路。

### 火山云 ASR 链路（当 `VOLC_ASR_API_KEY` 存在时可选）

当环境变量 `VOLC_ASR_API_KEY` 已设置且不想占用本地 GPU 时，可走火山云路径，**跳过 transcript-fix**：

```
URL ──▶ podcast-fetch (正常下载音频, README 含 "> Audio URL:" 行)
   ──▶ volcengine-asr (从 README 解析 audio_url, 传给火山云, 不读本地音频)
   ──▶ podcast-summary (跳过 podcast-transcript-fix)
```

调用方式见 [`volcengine-asr`](../volcengine-asr/SKILL.md)。火山云 API key 的开通步骤见 `docs/volcengine-asr-setup.md`。

**与标准链路的差异**：

| 维度 | 标准链路 (vibevoice-asr) | 火山云链路 (volcengine-asr) |
|---|---|---|
| 音频下载 | 需要 | 需要（README 要写 audio_url），但转录不读本地文件 |
| GPU | 需要（`--dp 4`） | 不需要 |
| speaker 标签 | 有 | 无（纯文本） |
| transcript-fix | 需要 | 不需要 |
| 鉴权 | 无 | `VOLC_ASR_API_KEY` |

### 本地 GPU ASR 链路（默认）

```
URL ──▶ podcast-fetch (下载音频)
   ──▶ podcast-transcribe (本地 vibevoice-asr, 需要 GPU)
   ──▶ podcast-transcript-fix (修正 ASR 专名错误)
   ──▶ podcast-summary
```

本地 GPU ASR 的部署见 `docs/vibevoice-local-setup.md`。

### 多集串行

对每个 URL 重复上述链路。Stage 1b 必须串行（GPU `--dp 4` 占满 4 卡）；Stage 2 可以等一批转录完之后批量做。每个 Stage 仍然**必须**走对应的 sub-skill。

### 仅补做某一阶段（断点续跑）

所有子 skill 都有幂等检查（已有产物则跳过）。可以单独调用任一阶段：
- 已有 transcript.md 但未 summarize → 直接调 `podcast-summary`
- 已 summary 但想重跑 → 删掉 `{basename}.md` 后重调 `podcast-summary`

## 关键约束（全局）

1. **环境分层**：字幕阶段使用 `uv run --group subtitle`，不安装 CUDA；只有 Linux GPU 转录阶段使用项目 GPU venv（含 vibevoice 依赖）。禁止在 Intel Mac 安装 `requirements-asr.txt` 中的 CUDA 包。

2. **只用 vibevoice-asr 转录**（本地 GPU 路径）：不用其他 ASR fallback。火山云路径是独立的云端选项。

3. **同一时刻只跑一个转录任务**：`--dp 4` 占满 GPU。

4. **目录与文件命名规则**：
   - **禁止特殊字符**：目录名和文件名中不得出现 `[` `]` `(` `)` `{` `}` `&` `,` `!` `@` `#` `'` `~` `;` 等字符，只允许字母、数字、连字符 `-`、下划线 `_` 和中文
   - **目录名 = 播客名称**：`{podcast_name}/{short_title}/` 中的 `podcast_name` 只取播客节目名
   - **summary 文件名基于内容精简命名**：`{basename}.md` 的 basename 是对本期内容的高度概括（≤20字），不得照搬目录名中的 `short_title`
   - fetch 阶段的 `sanitize_filename()` 已强制执行字符过滤

5. **纪要文件名 = `{basename(episode_dir)}.md`**：不是 `summary.md` 或 `research.md`

6. **不要 stage 音频到 git**：`*.m4a` / `*.mp3` 在 `.gitignore` 中

## 虚拟环境管理

字幕阶段使用 uv 的 `subtitle` dependency group；GPU ASR 环境保持独立 venv：

```bash
# Mac/Linux 字幕依赖（无 CUDA）
export PATH="$HOME/.local/bin:$PATH"
uv sync --group subtitle

# 仅 Linux GPU 主机：首次部署 ASR 全量依赖
uv pip install --python .venv/bin/python3 -r requirements-asr.txt \
  --index-url https://download.pytorch.org/whl/cu124
```

## 新增播客源（最常见的扩展）

只需要两步，**其他 skill 完全不动**：

1. 写一个新的 download 脚本 `scripts/<source>_fetch.py`，要求：
   - 接受 URL 作为位置参数 + `--output-dir <dir>` + `--no-transcribe`
   - 成功时 stdout 打印 `✓ Episode complete: <ep_dir>`
2. 在 [`podcast-fetch`](../podcast-fetch/SKILL.md) 的路由表中加一行

之后所有现有播客都能复用 transcribe / summary 这些 skill。

## 文件结构参考

```
podcast-summary/
├── .codebuddy/skills/
│   ├── podcast-pipeline/SKILL.md          ← 本文件（编排器）
│   ├── podcast-asr-scheduler/SKILL.md      ← 转录调度大脑
│   ├── podcast-fetch/SKILL.md              ← URL 路由表
│   ├── subtitle-fetch/SKILL.md             ← YouTube/Bilibili 字幕
│   ├── podcasttranscript-fetch/SKILL.md   ← PodcastTranscript.ai 公共库
│   ├── podcast-transcribe/SKILL.md         ← 本地 GPU ASR
│   ├── volcengine-asr/SKILL.md             ← 火山云 ASR
│   ├── podcast-transcript-fix/SKILL.md     ← ASR 校验
│   └── podcast-summary/SKILL.md            ← 中文深度纪要
├── scripts/                                ← 信源抓取 + ASR 脚本
│   ├── subtitle_fetch.py
│   ├── video_fetch.py
│   ├── xiaoyuzhou_download.py
│   ├── rss_fetch.py
│   ├── rss_download.py
│   ├── apple_podcast_to_rss.py
│   ├── spotify_fetch.py
│   ├── podcasttranscript_fetch.py
│   ├── podcast_transcript_probe.py
│   ├── volc_asr.py
│   ├── transcribe.sh
│   └── podcast_transcribe.sh
├── vibevoice-asr/                          ← 本地 GPU ASR 引擎
│   ├── transcribe.py
│   ├── transcribe_vllm.py
│   ├── serve_vllm.sh
│   └── ...
├── docker/
│   └── Dockerfile.asr-vllm                ← 自包含 vLLM 镜像
├── setup/
│   ├── hf_download.sh                      ← HuggingFace 模型下载
│   └── download_vibevoice_model.sh         ← VibeVoice-ASR 专用下载
├── docs/
│   ├── architecture.md                     ← 信源抓取多级结构
│   ├── volcengine-asr-setup.md            ← 火山云 ASR 配置
│   └── vibevoice-local-setup.md           ← 本地 GPU ASR 部署
├── pyproject.toml
├── requirements-asr.txt
└── README.md
```
