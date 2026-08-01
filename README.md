# podcast-summary

Agent-friendly podcast pipeline: **fetch → transcribe (local GPU or cloud ASR) → deep summary**.

把一集播客或视频从 URL 自动变成一篇可独立阅读的中文深度纪要。支持多级信源抓取（官方文稿 / 平台字幕 / PodcastTranscript.ai 公共库 / 火山云 ASR / 本地 GPU ASR），按成本从低到高自动选择最优路径。

## 为什么需要这个项目

- **信源分散**：播客分布在小宇宙、RSS、Apple Podcasts、Spotify、YouTube、Bilibili 等十几个平台，每个平台的抓取方式都不同
- **转录成本高**：本地 GPU ASR 需要 4 卡 4090，云端 ASR 需要付费，但很多播客其实已有官方字幕或文稿
- **纪要质量参差**：大多数 "AI summary" 工具只做压缩，丢失访谈弧线、人物细节和底层推理
- **Agent 集成难**：传统 CLI 工具缺少清晰的输入输出契约和幂等检查，AI agent 调用容易出错

本项目用 9 个独立子 skill 组成薄编排层，每个 skill 都有明确的 `episode_dir` 契约和幂等检查，AI agent 可以轻松端到端调用。

## 快速开始

### 1. 安装

```bash
git clone <repo-url> podcast-summary
cd podcast-summary

# 基础依赖（字幕路径 + 抓取路径，无 CUDA，macOS/Linux 通用）
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --group subtitle
```

### 2. 选择 ASR 后端

你有两个选择（二选一，或都配置让调度器自动选）：

#### 选项 A：火山云 ASR（无需 GPU，5 分钟配置）

见 [`docs/volcengine-asr-setup.md`](docs/volcengine-asr-setup.md)。简述：
1. 在火山引擎控制台购买 Agent Plan 套餐
2. 在豆包语音 → 系统管理 → 开通管理 → 服务管理 → 大模型 里开通"录音文件识别 2.0"
3. 在豆包语音 → 语音识别 → 【右上角】API 调用 里设置 API key
4. `export VOLC_ASR_API_KEY="<your-key>"`

#### 选项 B：本地 GPU ASR（需要 GPU，1-2 小时部署）

见 [`docs/vibevoice-local-setup.md`](docs/vibevoice-local-setup.md)。简述：
1. 克隆 [Microsoft VibeVoice](https://github.com/microsoft/VibeVoice) 仓库
2. `bash setup/download_vibevoice_model.sh` 下载模型并转换为 vLLM 格式
3. `docker pull hxer7963/vibevoice-asr-vllm:latest`（预构建镜像，无需本地 build）
4. `bash vibevoice-asr/serve_vllm.sh start`

### 3. 安装 Skill 到 AI agent

本仓库的 skill 位于 `.codebuddy/skills/`。如果你的 AI agent（如 CodeBuddy / Claude Code）支持 skill 加载：

```bash
# CodeBuddy / Claude Code: skills 已在 .codebuddy/skills/ 下，自动加载
# 其他 agent: 把 .codebuddy/skills/ 链接或拷贝到你的 agent skills 目录
```

### 4. 使用

把一个播客 URL 给 AI agent，它会自动：

1. **URL 路由**：识别平台（小宇宙/RSS/YouTube/Bilibili/...）
2. **信源抓取**：优先尝试官方文稿/字幕（零成本），命中即跳过 ASR
3. **转录**：未命中官方文稿时，按 `VOLC_ASR_API_KEY` 是否设置选择火山云或本地 GPU
4. **校验**：本地 GPU ASR 的输出会经过 `podcast-transcript-fix` 修正专名错误
5. **纪要**：生成五段式中文深度纪要 `{basename}.md`

```
用户: 帮我处理这集播客 https://www.xiaoyuzhoufm.com/episode/xxx

AI: [自动调用 podcast-pipeline skill]
    → podcast-fetch (下载音频 + shownotes)
    → podcast-asr-scheduler (检测到 VOLC_ASR_API_KEY)
    → volcengine-asr (火山云转录)
    → podcast-summary (生成纪要)
    
    完成！纪要位于: audios/xiaoyuzhou/.../某主题-嘉宾名.md
```

## 信源抓取的多级结构

这是本项目的核心设计。详见 [`docs/architecture.md`](docs/architecture.md)。

```
URL
 │
 ├─ Level 0: 零成本 (无 ASR, 无音频下载)
 │   ├─ 0a. PodcastTranscript.ai 公共库 (podcasttranscript-fetch)
 │   ├─ 0b. YouTube/Bilibili 官方字幕 (subtitle-fetch)
 │   └─ 0c. RSS/小宇宙/Apple/Spotify 官方 transcript (podcast-fetch 内部 probe)
 │
 ├─ Level 1: 火山云 ASR (需下载音频, 不需 GPU)
 │   └─ volcengine-asr (VOLC_ASR_API_KEY)
 │
 └─ Level 2: 本地 GPU ASR (兜底, 需 GPU)
     └─ podcast-transcribe (vibevoice-asr vLLM)
```

调度器（`podcast-asr-scheduler`）按优先级逐级尝试，上一级未命中自动降级到下一级。

## 支持的信源

| 平台 | URL 形式 | 抓取方式 |
|---|---|---|
| 小宇宙 | `xiaoyuzhoufm.com/episode/<eid>` | API + __NEXT_DATA__ scrape |
| 小宇宙整档 | `xiaoyuzhoufm.com/podcast/<pid>` | 自动展开所有集 |
| RSS（任意域名） | `https://feeds.transistor.fm/acquired` | feedparser + 音频下载 |
| Apple Podcasts | `podcasts.apple.com/.../id<digits>` | iTunes Lookup → RSS |
| Spotify（非独占） | `open.spotify.com/{episode,show,playlist}/<id>` | embed scrape → iTunes Search → RSS |
| YouTube | `youtube.com/watch?v=` / `youtu.be/` | yt-dlp 字幕优先，无字幕走 ASR |
| Bilibili | `bilibili.com/video/BV<id>` / `b23.tv/<id>` | yt-dlp 字幕优先，无字幕走 ASR |
| PodcastTranscript.ai | `podcasttranscript.ai/library/<slug>` | 公共只读 REST API |
| Amazon Music / Spotify 独占 | — | 不支持（DRM） |

新增源只需写一个 `scripts/<source>_fetch.py` 并在路由表加一行，其他 skill 完全不动。

## 仓库结构

```
podcast-summary/
├── .codebuddy/skills/                    # AI agent skills (9 个子 skill)
│   ├── podcast-pipeline/SKILL.md         # 编排器
│   ├── podcast-asr-scheduler/SKILL.md    # 转录调度大脑
│   ├── podcast-fetch/SKILL.md            # URL → 音频
│   ├── subtitle-fetch/SKILL.md           # 视频 → 字幕
│   ├── podcasttranscript-fetch/SKILL.md  # PodcastTranscript.ai 公共库
│   ├── podcast-transcribe/SKILL.md        # 本地 GPU ASR
│   ├── volcengine-asr/SKILL.md           # 火山云 ASR
│   ├── podcast-transcript-fix/SKILL.md   # ASR 校验
│   └── podcast-summary/SKILL.md          # 中文深度纪要
├── scripts/                              # 信源抓取 + ASR 脚本
├── vibevoice-asr/                        # 本地 GPU ASR 引擎 (transcribe.py, serve_vllm.sh, ...)
├── docker/
│   └── Dockerfile.asr-vllm              # 自包含 vLLM 镜像
├── setup/
│   ├── hf_download.sh                    # HuggingFace 模型下载
│   └── download_vibevoice_model.sh       # VibeVoice-ASR 专用下载 + vLLM 格式转换
├── docs/
│   ├── architecture.md                   # 信源抓取多级结构
│   ├── volcengine-asr-setup.md          # 火山云 ASR 配置
│   └── vibevoice-local-setup.md         # 本地 GPU ASR 部署
├── pyproject.toml                        # 基础依赖 (uv)
├── requirements-asr.txt                  # GPU ASR 依赖 (可选)
├── .gitignore
├── LICENSE
└── README.md
```

## 核心 Skill 索引

| 阶段 | Skill | 一句话职责 |
|---|---|---|
| 0 | `podcast-asr-scheduler` | 转录调度大脑，按优先级决策走哪条转录路径 |
| 1t | `podcasttranscript-fetch` | PodcastTranscript URL/topic → README + 完整 transcript |
| 1v | `subtitle-fetch` | 视频 URL → README + transcript；无字幕则生成 GPU ASR 交接 |
| 1a | `podcast-fetch` | URL → episode_dir（含 README + 音频） |
| 1b | `podcast-transcribe` | 音频 → transcript.md（vibevoice-asr，本地 GPU） |
| 1c | `volcengine-asr` | audio URL → transcript.md（火山云，无需 GPU） |
| 2a | `podcast-transcript-fix` | 修正 ASR 错误（英文专名、技术术语、中英混杂） |
| 2b | `podcast-summary` | 生成五段式详尽纪要 `{basename}.md` |

## 纪要格式

`podcast-summary` 产出的 `{basename}.md` 遵循五段式倒金字塔结构：

```markdown
# Episode Title

> 播客：**播客全称**
> 链接：https://...
> 嘉宾：Guest Names
> 主持：Host Names
> 时长：HH:MM:SS
> 发布日期：YYYY-MM-DD

## TL;DR
（1-2 段高密度结论）

## 核心结论
（5-10 条可被反驳的判断，含机制 + 证据 + 边界）

## 隐含推理与未明说暗示
（4-8 条高置信度推论，"推论—依据—边界"结构）

## 详尽内容
（占正文 ≥55%，6-12 章，每章保留问答推进 + 具体证据 + 人物细节）

## 总结升华
（底层机制 + 各章一句话回望 + 最后收束）
```

不是简单压缩，而是同时做到：覆盖充分、有访谈感、有烟火气、有底层思考、结构清楚。

## 环境变量

| 变量 | 用途 | 默认值 |
|---|---|---|
| `VOLC_ASR_API_KEY` | 火山云 ASR API key（设置后启用火山云路径） | — |
| `PODCAST_SUMMARY_ROOT` | 仓库根目录（用于 `transcribe.sh` 定位 venv） | 脚本所在目录的上一级 |
| `PODCAST_SUMMARY_VENV` | venv activate 路径 | `$PODCAST_SUMMARY_ROOT/.venv/bin/activate` |
| `PODCAST_SUMMARY_VENV_PY` | venv python 路径 | `$PODCAST_SUMMARY_ROOT/.venv/bin/python3` |
| `PODCAST_OUTPUT_DIR` | 音频输出根目录 | `$PODCAST_SUMMARY_ROOT/audios` |
| `VV_MODEL_PATH` | vLLM 格式模型路径 | `/workspace/models/VibeVoice-ASR-vllm` |
| `VV_TP` | tensor parallel size | `4` |
| `VV_GPU_MEM` | gpu-memory-utilization | `0.85` |
| `VV_PORT` | vLLM 服务端口 | `8000` |
| `VV_MODELS_ROOT` | 模型存储根目录（Docker volume 挂载） | `/workspace/models` |
| `HF_MODELS_ROOT` | HuggingFace 模型下载根目录 | `/workspace/models` |
| `VIBEVOICE_MODEL_PATH` | transformers 格式模型路径（legacy PyTorch 后端） | `/workspace/models/VibeVoice-ASR` |
| `VV_HOTWORDS` | 热词（逗号分隔，提升专名识别） | — |
| `VV_BACKEND` | 转录后端（`vllm` 或 `pytorch`） | `vllm` |

## Docker 镜像

vLLM 服务使用预构建镜像 `hxer7963/vibevoice-asr-vllm:latest`（Docker Hub），包含：
- vLLM v0.14.1
- ffmpeg + libsndfile1
- VibeVoice vllm_plugin（注册 `VibeVoiceForASRTraining` 架构）
- 优化的启动参数

构建见 [`docs/vibevoice-local-setup.md`](docs/vibevoice-local-setup.md) 步骤 3。

## 隐私与安全

- **音频文件不入 git**：`.gitignore` 已包含 `*.m4a`, `*.mp3`, `*.wav` 等
- **Cookie 不入 git**：`.gitignore` 已包含 `cookies.txt`, `cookies.*.txt`, `.secrets/`
- **API key 通过环境变量**：`VOLC_ASR_API_KEY` 等，不写入代码或配置文件
- **火山云路径**：音频 URL 会传给火山云服务器，介意隐私的用户请用本地 GPU 路径
- **SSRF 防护**：`podcasttranscript_fetch.py` 和 `ai_signal_fetch.py`（如有）的请求 base 硬编码，不接受用户输入 URL
- **Cookie 权限**：自动发现的持久 cookie 文件必须权限 ≤ `0600`，否则退出 3

## 许可证

MIT License — 见 [LICENSE](LICENSE)。

注意：本项目的编排代码是 MIT，但它调用的 VibeVoice ASR 模型权重遵循 [Microsoft 的研究许可证](https://huggingface.co/microsoft/VibeVoice-ASR)，火山引擎 API 遵循[火山引擎服务条款](https://www.volcengine.com/docs/6257/68966)。请自行确认你的使用场景符合这些条款。

## 致谢

- [Microsoft VibeVoice](https://github.com/microsoft/VibeVoice) — ASR 模型与 vLLM plugin
- [vLLM](https://github.com/vllm-project/vllm) — 推理引擎
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — 视频字幕抓取
- [PodcastTranscript.ai](https://podcasttranscript.ai) — 公共播客文字稿库
- [火山引擎](https://www.volcengine.com/) — 云端 ASR 服务
