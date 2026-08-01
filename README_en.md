# podcast-summary

**English** | [中文](./README.md)

Agent-friendly podcast pipeline: **fetch → transcribe (local GPU or cloud ASR) → deep summary**.

Turns a podcast or video URL into a standalone Chinese deep-summary markdown file. Supports multi-tier source fetching (official transcripts / platform subtitles / PodcastTranscript.ai public library / Volcengine cloud ASR / local GPU ASR), automatically selecting the lowest-cost path.

> **AI agent entry**: This repo has `AGENTS.md` (Codex) and `CLAUDE.md` (Claude Code / CodeBuddy) at the root. AI agents should read the relevant one first when opening this repo. Those tell the agent what this repo is, how to use it, and what constraints apply. This document is for humans.

---

<details open>
<summary><h2>Quick Install</h2></summary>

**One sentence to install** — tell your AI agent:

> Install https://github.com/hxer7963/podcast-summary

Your AI agent (Codex / Claude Code / CodeBuddy) will clone the repo, run `install.sh`, and auto-discover the 9 skills. No programming needed.

`install.sh` is a **zero-package install** by default: it does not install uv, Python packages, ffmpeg, Docker images, or models. Skill discovery and summary are agent-native; the minimal Volcengine transport uses the commonly available `curl`. Python 3.10+ is only for optional helpers, not hub installation.

With `VOLC_ASR_API_KEY` and a public audio URL, transcribe immediately:

```bash
bash scripts/volc_asr.sh run 'https://example.com/episode.mp3' 'audios/cloud/my-episode'
```

The script creates the `episode_dir`, `README.md`, and raw Volcengine result. If `jq` already exists it also writes `transcript.md`; otherwise the agent extracts `result.text` from `volc-response.json` without installing another tool. RSS/Apple/Spotify fetching and video subtitles are installed only when their route is needed.

**One sentence to use** — tell your AI agent:

> Process this podcast https://www.xiaoyuzhoufm.com/episode/xxxxx

The agent runs the full pipeline: fetch → transcribe → summary. ASR backend is chosen automatically:
- Official transcript/subtitle first (zero cost)
- Cloud ASR if `VOLC_ASR_API_KEY` is set (no GPU needed)
- Local GPU ASR as fallback — **with user confirmation before downloading ~20GB** (see below)

`bash scripts/check_capabilities.sh --json` gives the agent a zero-package capability report so it selects only a ready or genuinely needed route.

</details>

<details open>
<summary><h2>Lazy ASR Loading</h2></summary>

The 20GB local GPU ASR assets (5GB Docker image + 15GB model weights) are **not** downloaded at install time. They are lazy-loaded only when all of these conditions are met:

1. Official transcript / subtitle is unavailable (Level 0 missed)
2. Cloud ASR is unavailable (no `VOLC_ASR_API_KEY` set)
3. Local GPU is available

At that point, the agent asks the user for confirmation, showing:
- Asset sizes (~5GB image + ~15GB model = ~20GB total)
- Expected speedup (7-10x realtime, e.g. 60min audio → ~8min transcription)
- Available disk space

Download only proceeds after user confirms. See the `podcast-asr-scheduler` skill → "Level 2 lazy init flow" for details.

</details>

<details open>
<summary><h2>Multi-AI-Agent Support</h2></summary>

This repo's skills support three AI agents simultaneously. Skill source files live in `.codebuddy/skills/`; the other two directories are symlinks:

| Agent | Skill discovery path | Project-level config |
|---|---|---|
| Codex (OpenAI) | `.agents/skills/` → symlink → `.codebuddy/skills/` | `AGENTS.md` |
| Claude Code | `.claude/skills/` → symlink → `.codebuddy/skills/` | `CLAUDE.md` |
| CodeBuddy | `.codebuddy/skills/` | (built-in) |

All agents share the same set of `SKILL.md` files — no duplicate maintenance. If your agent isn't listed here, just symlink its skill directory to `.codebuddy/skills/`.

</details>

<details open>
<summary><h2>Why This Project</h2></summary>

- **Fragmented sources**: Podcasts are spread across xiaoyuzhou, RSS, Apple Podcasts, Spotify, YouTube, Bilibili, and a dozen other platforms — each with a different scraping method.
- **High transcription cost**: Local GPU ASR needs 4× RTX 4090; cloud ASR costs money — but many podcasts already have official subtitles or transcripts.
- **Inconsistent summary quality**: Most "AI summary" tools only compress, losing interview arcs, human details, and underlying reasoning.
- **Hard agent integration**: Traditional CLI tools lack clear input/output contracts and idempotent checks, making AI agent invocation error-prone.

This project uses 9 independent sub-skills as a thin orchestration layer. Each skill has a clear `episode_dir` contract and idempotent checks, so AI agents can run end-to-end easily.

> **This is not a single skill — it's a project repo bundling 9 skills.** AI agents auto-discover them on clone; no manual install or import needed.

</details>

<details open>
<summary><h2>Multi-Tier Source Fetching</h2></summary>

This is the core design of this project. See [`docs/architecture.md`](docs/architecture.md) for details.

```
URL
 │
 ├─ Level 0: Zero cost (no ASR, no audio download)
 │   ├─ 0a. PodcastTranscript.ai public library (podcasttranscript-fetch)
 │   ├─ 0b. YouTube/Bilibili official subtitles (subtitle-fetch)
 │   └─ 0c. RSS/xiaoyuzhou/Apple/Spotify official transcript (podcast-fetch internal probe)
 │
 ├─ Level 1: Volcengine cloud ASR (passes public audio URL, no local download/GPU)
 │   └─ volcengine-asr (VOLC_ASR_API_KEY)
 │
 └─ Level 2: Local GPU ASR (fallback, needs GPU)
     └─ podcast-transcribe (vibevoice-asr vLLM)
```

The scheduler (`podcast-asr-scheduler`) tries each level in priority order; if a higher level misses, it automatically falls back to the next.

</details>

<details open>
<summary><h2>Supported Sources</h2></summary>

| Platform | URL pattern | Fetch method |
|---|---|---|
| xiaoyuzhou | `xiaoyuzhoufm.com/episode/<eid>` | API + __NEXT_DATA__ scrape |
| xiaoyuzhou (whole show) | `xiaoyuzhoufm.com/podcast/<pid>` | Auto-expand all episodes |
| RSS (any domain) | `https://feeds.transistor.fm/acquired` | feedparser + audio download |
| Apple Podcasts | `podcasts.apple.com/.../id<digits>` | iTunes Lookup → RSS |
| Spotify (non-exclusive) | `open.spotify.com/{episode,show,playlist}/<id>` | embed scrape → iTunes Search → RSS |
| YouTube | `youtube.com/watch?v=` / `youtu.be/` | yt-dlp subtitle first, fallback to ASR |
| Bilibili | `bilibili.com/video/BV<id>` / `b23.tv/<id>` | yt-dlp subtitle first, fallback to ASR |
| PodcastTranscript.ai | `podcasttranscript.ai/library/<slug>` | Public read-only REST API |
| Amazon Music / Spotify exclusive | — | Not supported (DRM) |

Adding a new source requires only writing a `scripts/<source>_fetch.py` and adding one row to the route table — other skills remain untouched.

</details>

<details open>
<summary><h2>Repository Structure</h2></summary>

```
podcast-summary/
├── AGENTS.md                             # Codex project-level config (AI agent entry)
├── CLAUDE.md                             # Claude Code / CodeBuddy project-level config (AI agent entry)
├── .codebuddy/skills/                    # Skill source files (9 sub-skills, source of truth)
│   ├── podcast-pipeline/SKILL.md         # Orchestrator
│   ├── podcast-asr-scheduler/SKILL.md    # Transcription scheduler
│   ├── podcast-fetch/SKILL.md            # URL → audio
│   ├── subtitle-fetch/SKILL.md           # Video → subtitles
│   ├── podcasttranscript-fetch/SKILL.md  # PodcastTranscript.ai public library
│   ├── podcast-transcribe/SKILL.md       # Local GPU ASR
│   ├── volcengine-asr/SKILL.md           # Volcengine cloud ASR
│   ├── podcast-transcript-fix/SKILL.md   # ASR error correction
│   └── podcast-summary/SKILL.md          # Chinese deep summary
├── .agents/skills/                       # Symlink → .codebuddy/skills (Codex discovery)
├── .claude/skills/                       # Symlink → .codebuddy/skills (Claude Code / CodeBuddy discovery)
├── scripts/                              # Source fetching + ASR scripts
├── vibevoice-asr/                        # Local GPU ASR engine (transcribe.py, serve_vllm.sh, ...)
├── docker/
│   └── Dockerfile.asr-vllm              # Self-contained vLLM image
├── setup/
│   ├── hf_download.sh                    # HuggingFace model download
│   └── download_vibevoice_model.sh       # VibeVoice-ASR download + vLLM format conversion
├── docs/
│   ├── architecture.md                   # Multi-tier source fetching structure
│   ├── volcengine-asr-setup.md          # Volcengine cloud ASR setup
│   └── vibevoice-local-setup.md         # Local GPU ASR deployment
├── pyproject.toml                        # Optional fetch/subtitle dependency groups
├── requirements-asr.txt                  # Legacy GPU ASR deps (optional)
├── .gitignore
├── LICENSE
└── README.md
```

</details>

<details open>
<summary><h2>Core Skill Index</h2></summary>

| Stage | Skill | One-liner |
|---|---|---|
| 0 | `podcast-asr-scheduler` | Transcription scheduler — decides which path by priority |
| 1t | `podcasttranscript-fetch` | PodcastTranscript URL/topic → README + full transcript |
| 1v | `subtitle-fetch` | Video URL → README + transcript; generates GPU ASR handoff if no subtitle |
| 1a | `podcast-fetch` | URL → episode_dir (README + audio) |
| 1b | `podcast-transcribe` | Audio → transcript.md (vibevoice-asr, local GPU) |
| 1c | `volcengine-asr` | Audio URL → transcript.md (Volcengine cloud, no GPU) |
| 2a | `podcast-transcript-fix` | Fix ASR errors (English proper nouns, technical terms, mixed CN/EN) |
| 2b | `podcast-summary` | Generate 5-section deep summary `{basename}.md` |

</details>

<details open>
<summary><h2>Summary Format</h2></summary>

The `{basename}.md` produced by `podcast-summary` follows a 5-section inverted-pyramid structure:

```markdown
# Episode Title

> Podcast: **Podcast Full Name**
> Link: https://...
> Guest: Guest Names
> Host: Host Names
> Duration: HH:MM:SS
> Published: YYYY-MM-DD

## TL;DR
(1-2 dense conclusion paragraphs)

## Core Conclusions
(5-10 refutable judgments, each with mechanism + evidence + boundary)

## Implicit Reasoning
(4-8 high-confidence inferences, "inference—basis—boundary" structure)

## Detailed Content
(≥55% of body, 6-12 chapters, each preserving Q&A progression + specific evidence + human details)

## Conclusion & Synthesis
(Underlying mechanisms + one-sentence-per-chapter recap + final closing)
```

Not simple compression — it achieves: thorough coverage, interview feel, human texture, underlying reasoning, clear structure — all at once.

</details>

<details open>
<summary><h2>Environment Variables</h2></summary>

| Variable | Purpose | Default |
|---|---|---|
| `VOLC_ASR_API_KEY` | Volcengine cloud ASR API key (enables cloud path) | — |
| `PODCAST_SUMMARY_ROOT` | Repo root (for `transcribe.sh` to locate venv) | Parent of script dir |
| `PODCAST_SUMMARY_VENV` | venv activate path | `$PODCAST_SUMMARY_ROOT/.venv/bin/activate` |
| `PODCAST_SUMMARY_VENV_PY` | venv python path | `$PODCAST_SUMMARY_ROOT/.venv/bin/python3` |
| `PODCAST_OUTPUT_DIR` | Audio output root | `$PODCAST_SUMMARY_ROOT/audios` |
| `VV_MODEL_PATH` | vLLM-format model path | `/workspace/models/VibeVoice-ASR-vllm` |
| `VV_TP` | Tensor parallel size | `4` |
| `VV_GPU_MEM` | gpu-memory-utilization | `0.85` |
| `VV_PORT` | vLLM service port | `8000` |
| `VV_MODELS_ROOT` | Model storage root (Docker volume mount) | `/workspace/models` |
| `HF_MODELS_ROOT` | HuggingFace model download root | `/workspace/models` |
| `VIBEVOICE_MODEL_PATH` | Transformers-format model path (legacy PyTorch backend) | `/workspace/models/VibeVoice-ASR` |
| `VV_HOTWORDS` | Hot words (comma-separated, improves proper-noun recognition) | — |
| `VV_BACKEND` | Transcription backend (`vllm` or `pytorch`) | `vllm` |

</details>

<details open>
<summary><h2>Docker Image</h2></summary>

The vLLM service uses the pre-built image `hxer7963/vibevoice-asr-vllm:latest` (Docker Hub), which includes:
- vLLM v0.14.1
- ffmpeg + libsndfile1
- VibeVoice vllm_plugin (registers `VibeVoiceForASRTraining` architecture)
- Optimized launch parameters

See [`docs/vibevoice-local-setup.md`](docs/vibevoice-local-setup.md) for build instructions.

</details>

<details open>
<summary><h2>Privacy & Security</h2></summary>

- **Audio files never in git**: `.gitignore` includes `*.m4a`, `*.mp3`, `*.wav`, etc.
- **Cookies never in git**: `.gitignore` includes `cookies.txt`, `cookies.*.txt`, `.secrets/`
- **API keys via env vars**: `VOLC_ASR_API_KEY` etc. — never in code or config files
- **Cloud ASR path**: Audio URLs are sent to Volcengine servers; privacy-conscious users should use the local GPU path
- **SSRF protection**: `podcasttranscript_fetch.py` request bases are hardcoded; user-supplied URLs are not accepted
- **Cookie permissions**: Auto-discovered persistent cookie files must have permissions ≤ `0600`, else exit 3

</details>

<details open>
<summary><h2>License</h2></summary>

MIT License — see [LICENSE](LICENSE).

Note: The orchestration code in this project is MIT, but the VibeVoice ASR model weights it calls follow [Microsoft's research license](https://huggingface.co/microsoft/VibeVoice-ASR), and the Volcengine API is governed by [Volcengine's terms of service](https://www.volcengine.com/docs/6257/68966). Please verify your use case complies with these terms.

</details>

<details open>
<summary><h2>Acknowledgments</h2></summary>

- [Microsoft VibeVoice](https://github.com/microsoft/VibeVoice) — ASR model & vLLM plugin
- [vLLM](https://github.com/vllm-project/vllm) — Inference engine
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — Video subtitle fetching
- [PodcastTranscript.ai](https://podcasttranscript.ai) — Public podcast transcript library
- [Volcengine](https://www.volcengine.com/) — Cloud ASR service

</details>
