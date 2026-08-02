# AGENTS.md

This file is read by Codex when it opens this repository. It tells the agent what this project is, how it's structured, and how to operate it. **Read this first.**

## What this project is

`podcast-summary` is **a project repo bundling 13 skills** (not a single skill). It builds local knowledge from podcast, video, public article, WeChat, and PodHood sources, then generates a Chinese deep summary only when the user requests one. Podcast pipeline: **fetch → transcribe → summary**; article fetch stays fetch-only unless summary is explicit. Each stage is atomic, portable, and idempotent.

AI agents auto-discover the skills from `.agents/skills/` on clone — no manual import needed.

## Install (already done if repo is on disk)

If a user says "帮我安装 https://github.com/hxer7963/podcast-summary":
1. `git clone` the repo
2. Run `bash install.sh` (zero-package core install)
3. Skills are auto-discovered — ready to use

The default install does not install uv, Python packages, ffmpeg, Docker images, or models. Optional source/video dependencies are installed only for the route that needs them.

## Lazy ASR loading (important)

When the pipeline needs local GPU ASR (Priority 2 in `podcast-asr-scheduler`), **do not auto-download** the ~20GB assets. Follow the "Level 2 懒加载流程" in the `podcast-asr-scheduler` skill:

1. Check GPU available
2. Check if Docker image + model already exist
3. If missing: tell user the sizes (~5GB image + ~15GB model = ~20GB), speedup (7-10x realtime), and ask for confirmation
4. Only after user confirms: `docker pull` + `bash setup/download_vibevoice_model.sh` + `bash vibevoice-asr/serve_vllm.sh start`
5. Then run transcription

## Skill discovery

Codex scans `.agents/skills/*/SKILL.md` (symlinked to `.codebuddy/skills/`). Each skill has a `name` + `description` frontmatter that tells the agent when to trigger it.

| Skill | Trigger | One-liner |
|---|---|---|
| `podcast-pipeline` | "处理这集播客", "download a podcast", URL given | Orchestrator — decides the full chain |
| `podcast-asr-scheduler` | "调度转录", "选 ASR 路径" | Decides which transcription path to take |
| `podcast-fetch` | "下载播客", "fetch episode", podcast URL | URL → episode_dir (README + audio) |
| `subtitle-fetch` | "拉取字幕", YouTube/Bilibili URL | Video URL → episode_dir (README + transcript) |
| `podcasttranscript-fetch` | podcasttranscript.ai URL, "搜索 topic" | PodcastTranscript.ai library → episode_dir |
| `podcast-transcribe` | "转录", "transcribe", episode_dir with audio | Audio → transcript.md (local GPU, has speaker tags) |
| `volcengine-asr` | "火山云转录", VOLC_ASR_API_KEY set | Audio URL → transcript.md (cloud, no GPU) |
| `podcast-transcript-fix` | "修正转录", "fix transcript" | Fix ASR proper-noun errors in-place |
| `podcast-summary` | "生成纪要", "summarize this episode/article" | transcript.md 或 article.md → {basename}.md (5-section Chinese summary) |
| `wechat-to-md` | WeChat public article URL, "抓公众号文章" | WeChat URL → article_dir (article + metadata + optional images); no summary |
| `article-fetch` | public article/RSS URL, "抓取文章" | Web/RSS → article_dir; no summary |
| `podhood-fetch` | *.podhood.com, "抓取十分吸引转录" | PodHood REST → episode_dir with transcript |
| `article-pipeline` | explicit "抓取并总结文章" | Thin fetch → summary orchestration |

For podcast/video end-to-end requests, invoke `podcast-pipeline`. For article or WeChat knowledge-base requests, invoke only the matching fetch skill and stop. Invoke `article-pipeline` only when the user explicitly asks for both fetch and summary.

## Directory layout

```
podcast-summary/
├── .codebuddy/skills/      # Source of truth for skills (SKILL.md files)
├── .agents/skills/         # Symlink → .codebuddy/skills (Codex discovery)
├── .claude/skills/         # Symlink → .codebuddy/skills (Claude Code / CodeBuddy discovery)
├── scripts/                # Shared podcast + ASR scripts
├── .codebuddy/skills/<name>/scripts/ # Portable source-specific scripts when needed
├── vibevoice-asr/          # Local GPU ASR engine (VibeVoice-ASR via vLLM)
├── docker/                  # Dockerfile for the vLLM serving image
├── setup/                   # Model download scripts
├── docs/                    # Human-facing setup guides
├── pyproject.toml           # Base deps (no CUDA)
├── requirements-asr.txt    # GPU ASR deps (optional, CUDA)
└── audios/                  # Output directory (gitignored)
```

Podcast and article sources use parallel absolute-directory contracts:

```
episode_dir/
├── README.md           # Shownotes / metadata (from fetch)
├── *.m4a / *.mp3       # Audio (from fetch, Level 1/2 only)
├── transcript.md       # Transcript (from transcribe / volcengine-asr / subtitle-fetch)
└── {basename}.md       # Final summary (from podcast-summary)
```

```
article_dir/
├── README.md           # Article metadata
├── article.md          # Single source of truth for article body
├── images/             # Optional, primarily WeChat
└── {basename}.md       # Only when summary is explicitly requested
```

## Capability-first environment routing

Run `bash scripts/check_capabilities.sh --json` before choosing a path. Do not install a capability merely because it is missing.

- Skill discovery and summary: no local runtime packages.
- Direct-audio Volcengine transport: curl only; jq is optional because the agent can read `volc-response.json`.
- PodcastTranscript, PodHood, public article, WeChat, and Xiaoyuzhou lightweight helpers: Python 3.10+ standard library only.
- RSS/Apple/Spotify fetch: install only on demand with `bash install.sh --with-fetch`.
- YouTube/Bilibili subtitles: install only on demand with `bash install.sh --with-subtitle`; ffmpeg is not required for subtitle-only work.
- Local GPU ASR: requires GPU + Docker + ffmpeg and follows the confirmed lazy-loading flow. Never install this path on a no-GPU machine.

## How to operate

When a user gives a URL or says "处理这集播客":

1. Invoke `podcast-pipeline` skill — it will route based on URL.
2. The pipeline auto-selects transcription path via `podcast-asr-scheduler`:
   - Official transcript / subtitle first (zero cost)
   - Volcengine cloud ASR if `VOLC_ASR_API_KEY` is set
   - Local GPU ASR as fallback
3. After transcript.md is ready, `podcast-summary` generates `{basename}.md`.
4. The pipeline ends at summary. No archive / push stages in this repo.

For a public article or WeChat URL:

1. Invoke `article-fetch` or `wechat-to-md` and build `article_dir`.
2. Stop after fetch unless the user explicitly requests a summary.
3. If summary is explicit, invoke `podcast-summary`, which reads `article.md + README.md`.

**Do not** call scripts directly (e.g., `bash scripts/transcribe.sh`). Always go through the skill, which has idempotent checks and validation.

## Critical constraints

- **Environment separation**: Core/cloud is package-free; fetch and subtitle are separate optional groups; GPU ASR is Docker-based. Never install CUDA deps on macOS/Intel or a no-GPU host.
- **One transcription at a time**: `--dp 4` occupies all GPUs.
- **Filename sanitization**: No `[](){}&,;!@#'~` in dir/file names. Only alphanumerics, dash, underscore, CJK.
- **Audio never in git**: `*.m4a`, `*.mp3` are gitignored.
- **Cookies never in git**: Must be in `.secrets/` or outside repo, permissions `0600`.

## Setup status check

If unsure whether the environment is ready, run `bash scripts/check_capabilities.sh --json` and select the cheapest ready route. Missing optional capabilities are not installation failures.

## Extending

- **New podcast source**: Add the smallest self-contained script under the owning skill when possible; use shared `scripts/<source>_fetch.py` only when multiple skills consume it (must print `✓ Episode complete: <dir>`), add a row to `podcast-fetch` SKILL.md route table. Other skills unchanged.
- **New ASR backend**: Write a new `<backend>-asr` skill, add a priority node to `podcast-asr-scheduler` decision tree.
- **Downstream stages** (archive / push): This repo ends at summary. Add new skills after `podcast-summary` if you need archiving, tagging, or publishing.
