# CLAUDE.md

This file is read by Claude Code / CodeBuddy when it opens this repository. It tells the agent what this project is, how it's structured, and how to operate it. **Read this first.**

## What this project is

`podcast-summary` is **a project repo bundling 9 skills** (not a single skill). It turns a podcast or video URL into a Chinese deep-summary markdown file. Pipeline: **fetch → transcribe → summary**. Each stage is a self-contained skill with an `episode_dir` handoff contract and idempotent checks.

AI agents auto-discover the skills from `.claude/skills/` (symlinked to `.codebuddy/skills/`) on clone — no manual import needed.

## One-command install (already done if repo is on disk)

```bash
bash install.sh
```

This installs Python deps, detects GPU/Docker/cloud-key, and configures the ASR backend. If the user hasn't run it, suggest it.

## Skill discovery

Claude Code / CodeBuddy scans `.claude/skills/*/SKILL.md`. Each skill has a `name` + `description` frontmatter that tells the agent when to trigger it.

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
| `podcast-summary` | "生成纪要", "summarize this episode" | transcript.md → {basename}.md (5-section Chinese summary) |

The agent should usually invoke `podcast-pipeline` and let it dispatch to sub-skills. Direct sub-skill invocation is for resume/checkpoint scenarios only.

## Directory layout

```
podcast-summary/
├── .codebuddy/skills/      # Source of truth for skills (SKILL.md files)
├── .agents/skills/         # Symlink → .codebuddy/skills (Codex discovery)
├── .claude/skills/         # Symlink → .codebuddy/skills (Claude Code / CodeBuddy discovery)
├── scripts/                # Fetch + ASR Python scripts (called by skills)
├── vibevoice-asr/          # Local GPU ASR engine (VibeVoice-ASR via vLLM)
├── docker/                  # Dockerfile for the vLLM serving image
├── setup/                   # Model download scripts
├── docs/                    # Human-facing setup guides
├── pyproject.toml           # Base deps (no CUDA)
├── requirements-asr.txt    # GPU ASR deps (optional, CUDA)
└── audios/                  # Output directory (gitignored)
```

The `episode_dir` is the universal handoff contract. Every skill takes an `episode_dir` (absolute path) and adds files to it:

```
episode_dir/
├── README.md           # Shownotes / metadata (from fetch)
├── *.m4a / *.mp3       # Audio (from fetch, Level 1/2 only)
├── transcript.md       # Transcript (from transcribe / volcengine-asr / subtitle-fetch)
└── {basename}.md       # Final summary (from podcast-summary)
```

## Environment requirements

Before running the pipeline, check the environment:

1. **Python deps**: `uv sync` (base) + `uv sync --group subtitle` (YouTube/Bilibili)
2. **ASR backend** (one of):
   - `VOLC_ASR_API_KEY` env var set → cloud ASR (no GPU needed). See `docs/volcengine-asr-setup.md`.
   - Local GPU available (`nvidia-smi` works) + `vibevoice-asr-vllm` docker image pulled → local ASR. See `docs/vibevoice-local-setup.md`.
   - Neither → pipeline will fail at transcription stage. Tell the user to configure one.

## How to operate

When a user gives a URL or says "处理这集播客":

1. Invoke `podcast-pipeline` skill — it will route based on URL.
2. The pipeline auto-selects transcription path via `podcast-asr-scheduler`:
   - Official transcript / subtitle first (zero cost)
   - Volcengine cloud ASR if `VOLC_ASR_API_KEY` is set
   - Local GPU ASR as fallback
3. After transcript.md is ready, `podcast-summary` generates `{basename}.md`.
4. The pipeline ends at summary. No archive / push stages in this repo.

**Do not** call scripts directly (e.g., `bash scripts/transcribe.sh`). Always go through the skill, which has idempotent checks and validation.

## Critical constraints

- **Environment separation**: Subtitle stage uses `uv run --group subtitle` (no CUDA). GPU ASR uses `requirements-asr.txt` (CUDA). Never install CUDA deps on macOS/Intel.
- **One transcription at a time**: `--dp 4` occupies all GPUs.
- **Filename sanitization**: No `[](){}&,;!@#'~` in dir/file names. Only alphanumerics, dash, underscore, CJK.
- **Audio never in git**: `*.m4a`, `*.mp3` are gitignored.
- **Cookies never in git**: Must be in `.secrets/` or outside repo, permissions `0600`.

## Setup status check

If unsure whether the environment is ready, run:

```bash
# Base deps
uv sync 2>/dev/null && echo "base: OK" || echo "base: MISSING"

# Subtitle deps
uv sync --group subtitle 2>/dev/null && echo "subtitle: OK" || echo "subtitle: MISSING"

# Cloud ASR
[[ -n "$VOLC_ASR_API_KEY" ]] && echo "volc-asr: OK" || echo "volc-asr: not configured"

# Local GPU ASR
nvidia-smi >/dev/null 2>&1 && echo "gpu: OK" || echo "gpu: not available"
docker image inspect hxer7963/vibevoice-asr-vllm:latest >/dev/null 2>&1 && echo "docker-image: OK" || echo "docker-image: not pulled"
curl -sf http://localhost:8000/health -o /dev/null 2>/dev/null && echo "vllm-service: OK" || echo "vllm-service: not running"
```

## Extending

- **New podcast source**: Write `scripts/<source>_fetch.py` (must print `✓ Episode complete: <dir>`), add a row to `podcast-fetch` SKILL.md route table. Other skills unchanged.
- **New ASR backend**: Write a new `<backend>-asr` skill, add a priority node to `podcast-asr-scheduler` decision tree.
- **Downstream stages** (archive / push): This repo ends at summary. Add new skills after `podcast-summary` if you need archiving, tagging, or publishing.
