#!/usr/bin/env bash
#
# install.sh — Lightweight setup for podcast-summary.
#
# This script ONLY installs the lightweight Python deps needed for source
# fetching and subtitle extraction. It does NOT download the ~15GB VibeVoice
# model or the ~5GB Docker image — those are lazy-loaded on first use when
# local GPU ASR is actually needed (see podcast-asr-scheduler skill).
#
# Safe to re-run. Safe on macOS (no CUDA).
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

log()  { printf '\033[1;34m[INFO]\033[0m  %s\n' "$*"; }
ok()   { printf '\033[1;32m[OK]\033[0m    %s\n' "$*"; }
warn() { printf '\033[1;33m[WARN]\033[0m  %s\n' "$*" >&2; }

echo "============================================"
echo "  podcast-summary installer (lightweight)"
echo "============================================"
echo ""

# ── 1. Python deps (lightweight, no CUDA) ────────────────────────────────────
log "Installing Python dependencies (base + subtitle)..."

if ! command -v uv >/dev/null 2>&1; then
    log "uv not found, installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

uv sync
uv sync --group subtitle
ok "Python deps installed"

# ── 2. ffmpeg (required for audio processing) ────────────────────────────────
if ! command -v ffmpeg >/dev/null 2>&1; then
    warn "ffmpeg not found. Install it manually:"
    warn "  Ubuntu/Debian: sudo apt install ffmpeg"
    warn "  macOS:         brew install ffmpeg"
else
    ok "ffmpeg: $(ffmpeg -version | head -1)"
fi

# ── 3. Environment detection (report only, do NOT act) ──────────────────────
echo ""
echo "============================================"
echo "  Environment detection (report only)"
echo "============================================"

nvidia-smi >/dev/null 2>&1 \
    && ok "GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)" \
    || warn "GPU: not detected"

docker image inspect hxer7963/vibevoice-asr-vllm:latest >/dev/null 2>&1 \
    && ok "Docker image: pulled" \
    || warn "Docker image: not pulled (will lazy-load on first local-ASR use)"

[[ -n "${VOLC_ASR_API_KEY:-}" ]] \
    && ok "VOLC_ASR_API_KEY: set (cloud ASR available)" \
    || warn "VOLC_ASR_API_KEY: not set"

# ── 4. Summary ────────────────────────────────────────────────────────────────
echo ""
echo "============================================"
echo "  Ready!"
echo "============================================"
echo ""
echo "  Skills installed:  9 (auto-discovered by your AI agent)"
echo "  ASR backend:      lazy-loaded when first needed"
echo ""
echo "  Next step:"
echo "    Open this repo in your AI agent (Codex / Claude Code / CodeBuddy),"
echo "    then give it a podcast URL:"
echo ""
echo "      > 处理这集播客 https://www.xiaoyuzhoufm.com/episode/xxxxx"
echo ""
echo "  The agent auto-discovers skills from .agents/skills/ (Codex) or"
echo "  .claude/skills/ (Claude Code) and runs the full pipeline."
echo "  ASR backend is chosen automatically — cloud ASR if VOLC_ASR_API_KEY"
echo "  is set, else local GPU ASR (with confirmation before downloading ~20GB)."
echo ""
