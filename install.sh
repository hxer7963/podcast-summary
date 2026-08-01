#!/usr/bin/env bash
#
# install.sh — One-command setup for podcast-summary.
#
# Usage:
#   curl -fsSL <raw-url>/install.sh | bash
#   # or, after cloning:
#   bash install.sh
#
# What it does:
#   1. Install Python deps (uv sync + subtitle group)
#   2. Detect environment (GPU? Docker? VOLC_ASR_API_KEY?)
#   3. If GPU + Docker available: pull image + download model (local ASR path)
#   4. If no GPU: print instructions for volcengine cloud ASR
#   5. Print a ready-to-use summary
#
# This script is idempotent — safe to re-run.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

log()  { printf '\033[1;34m[INFO]\033[0m  %s\n' "$*"; }
ok()   { printf '\033[1;32m[OK]\033[0m    %s\n' "$*"; }
warn() { printf '\033[1;33m[WARN]\033[0m  %s\n' "$*" >&2; }
err()  { printf '\033[1;31m[ERR]\033[0m   %s\n' "$*" >&2; }

echo "============================================"
echo "  podcast-summary installer"
echo "============================================"
echo ""

# ── 1. Python deps ────────────────────────────────────────────────────────────
log "Installing Python dependencies..."

if ! command -v uv >/dev/null 2>&1; then
    warn "uv not found. Installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

uv sync
uv sync --group subtitle
ok "Python deps installed"

# ── 2. ffmpeg (required for audio processing) ────────────────────────────────
if ! command -v ffmpeg >/dev/null 2>&1; then
    warn "ffmpeg not found. Install it:"
    warn "  Ubuntu/Debian: sudo apt install ffmpeg"
    warn "  macOS:         brew install ffmpeg"
else
    ok "ffmpeg: $(ffmpeg -version | head -1)"
fi

# ── 3. Detect ASR backend ────────────────────────────────────────────────────
echo ""
echo "============================================"
echo "  ASR backend detection"
echo "============================================"

HAS_GPU=false
HAS_DOCKER_IMAGE=false
HAS_VOLC_KEY=false
ASR_PATH=""

if nvidia-smi >/dev/null 2>&1; then
    HAS_GPU=true
    ok "GPU detected: $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
else
    warn "No GPU detected"
fi

if docker image inspect hxer7963/vibevoice-asr-vllm:latest >/dev/null 2>&1; then
    HAS_DOCKER_IMAGE=true
    ok "Docker image: hxer7963/vibevoice-asr-vllm:latest (pulled)"
elif $HAS_GPU; then
    log "Pulling Docker image (local ASR path)..."
    docker pull hxer7963/vibevoice-asr-vllm:latest && HAS_DOCKER_IMAGE=true || warn "Docker pull failed"
fi

if [[ -n "${VOLC_ASR_API_KEY:-}" ]]; then
    HAS_VOLC_KEY=true
    ok "VOLC_ASR_API_KEY: set (cloud ASR available)"
else
    warn "VOLC_ASR_API_KEY: not set"
fi

# ── 4. Configure ASR backend ──────────────────────────────────────────────────
if $HAS_GPU && $HAS_DOCKER_IMAGE; then
    ASR_PATH="local-gpu"
    echo ""
    log "Local GPU ASR: image ready. Downloading model (~15GB, one-time)..."

    # Check if model already exists
    MODELS_ROOT="${HF_MODELS_ROOT:-/workspace/models}"
    if [[ -f "${MODELS_ROOT}/VibeVoice-ASR-vllm/config.json" ]]; then
        ok "Model already downloaded: ${MODELS_ROOT}/VibeVoice-ASR-vllm"
    else
        bash setup/download_vibevoice_model.sh
    fi
    ok "Local GPU ASR: fully configured"
fi

if [[ "$ASR_PATH" == "" ]] && $HAS_VOLC_KEY; then
    ASR_PATH="volcengine-cloud"
    ok "Cloud ASR: configured (VOLC_ASR_API_KEY)"
fi

if [[ "$ASR_PATH" == "" ]]; then
    echo ""
    err "No ASR backend configured. Choose one:"
    echo ""
    echo "  Option A — Cloud ASR (no GPU needed, 5 min setup):"
    echo "    1. Open https://console.volcengine.com/ark/region:cn-beijing/subscription/agent-plan"
    echo "       Buy an Agent Plan."
    echo "    2. Open https://console.volcengine.com/speech/new/setting/activate"
    echo "       Enable '录音文件识别 2.0' under 豆包语音 → 系统管理 → 开通管理."
    echo "    3. Get API key at 豆包语音 → 语音识别 → API 调用."
    echo "    4. export VOLC_ASR_API_KEY=\"<your-key>\""
    echo "    5. Re-run: bash install.sh"
    echo ""
    echo "  Option B — Local GPU ASR (needs 4× RTX 4090, ~30 min setup):"
    echo "    See docs/vibevoice-local-setup.md"
    echo "    1. docker pull hxer7963/vibevoice-asr-vllm:latest"
    echo "    2. bash setup/download_vibevoice_model.sh"
    echo "    3. bash vibevoice-asr/serve_vllm.sh start"
    echo ""
    exit 1
fi

# ── 5. Summary ────────────────────────────────────────────────────────────────
echo ""
echo "============================================"
echo "  Installation complete!"
echo "============================================"
echo ""
echo "  ASR backend: $ASR_PATH"
echo "  Skills:      9 (auto-discovered by Codex / Claude Code / CodeBuddy)"
echo "  Output dir:  audios/ (gitignored)"
echo ""
echo "  Next step:"
echo "    Open this repo in your AI agent (Codex / Claude Code / CodeBuddy),"
echo "    then give it a podcast URL:"
echo ""
echo "      > 处理这集播客 https://www.xiaoyuzhoufm.com/episode/xxxxx"
echo ""
echo "  The agent will auto-discover skills from .agents/skills/ (Codex) or"
echo "  .claude/skills/ (Claude Code) and run the full pipeline."
echo ""
