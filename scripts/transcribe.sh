#!/usr/bin/env bash
# transcribe.sh — Pure transcription stage (used by the podcast-transcribe skill).
#
# Usage:
#   bash scripts/transcribe.sh <episode_dir> [<episode_dir> ...]
#
# Backends (VV_BACKEND):
#   vllm     (default) — talk to the VibeVoice-ASR vLLM service. The client
#                        (vibevoice-asr/transcribe_vllm.py) chunks long audio,
#                        auto-recovers from repetition loops, and writes a
#                        transcript.md identical in format to the PyTorch path.
#                        The service is auto-started via serve_vllm.sh if down.
#   pytorch            — legacy in-process transformers path (transcribe.py --dp 4).
#
# Pre-condition: <episode_dir> already contains an audio file (*.m4a or *.mp3).
# Output:        <episode_dir>/transcript.md  (prints `TRANSCRIPT=<path>` on success)
# Skip behavior: if transcript.md already exists and is non-empty, skip.
set -euo pipefail

# ── Paths ────────────────────────────────────────────────────────────────────
# Resolve the project root from this script's location (scripts/ → repo root).
# Override with $PODCAST_SUMMARY_ROOT if you install the repo elsewhere or want
# to share one vibevoice-asr engine across multiple projects.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${PODCAST_SUMMARY_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
VIBEVOICE_DIR="$PROJECT_DIR/vibevoice-asr"
# Optional: reuse a shared venv instead of the repo-local one.
PROJECT_VENV="${PODCAST_SUMMARY_VENV:-$PROJECT_DIR/.venv/bin/activate}"
VENV_PY="${PODCAST_SUMMARY_VENV_PY:-$PROJECT_DIR/.venv/bin/python3}"

# ── Backend selection ─────────────────────────────────────────────────────────
VV_BACKEND="${VV_BACKEND:-vllm}"
VV_URL="${VV_URL:-http://localhost:8000}"
VV_HOTWORDS="${VV_HOTWORDS:-}"                      # optional comma-separated terms
VV_CONCURRENCY="${VV_CONCURRENCY:-4}"               # parallel chunks; tp=4 server batches ~3x
SERVE_SCRIPT="$VIBEVOICE_DIR/serve_vllm.sh"
VLLM_CLIENT="$VIBEVOICE_DIR/transcribe_vllm.py"

# ── pytorch-backend constants (legacy path) ────────────────────────────────────
TRANSCRIBE_SCRIPT="$VIBEVOICE_DIR/transcribe.py"
VIBEVOICE_MODEL_PATH="${VIBEVOICE_MODEL_PATH:-/workspace/models/VibeVoice-ASR}"
MAX_DURATION_SINGLE=3000
SPLIT_DURATION=3000
SPLIT_OVERLAP=30

log() { echo "[$(date '+%H:%M:%S')] $*" >&2; }
err() { echo "[$(date '+%H:%M:%S')] ERROR: $*" >&2; }

get_duration() {
    ffprobe -v quiet -print_format json -show_format "$1" \
        | python3 -c "import sys,json; print(int(float(json.load(sys.stdin)['format']['duration'])))"
}

find_audio() {
    find "$1" -maxdepth 1 \( -name "*.m4a" -o -name "*.mp3" \) -type f | head -1
}

# ── vLLM backend: ensure the service is up, then run the client ─────────────────
ensure_vllm_service() {
    if curl -sf "$VV_URL/health" -o /dev/null 2>/dev/null; then
        return 0
    fi
    log "vLLM service not up — starting via serve_vllm.sh ..."
    bash "$SERVE_SCRIPT" start
}

transcribe_vllm() {
    local audio="$1" out="$2"
    ensure_vllm_service
    local vv_concurrency="${VV_CONCURRENCY:-4}"   # tp=4 server batches ~3x via continuous batching
    local args=("$audio" -o "$out" --url "$VV_URL" --concurrency "$vv_concurrency")
    [[ -n "$VV_HOTWORDS" ]] && args+=(--hotwords "$VV_HOTWORDS")
    "$VENV_PY" "$VLLM_CLIENT" "${args[@]}"
}

# ── pytorch backend (legacy in-process) ─────────────────────────────────────────
transcribe_pytorch() {
    local audio="$1" out="$2" ep_dir="$3"
    local duration; duration=$(get_duration "$audio")
    # shellcheck disable=SC1090
    source "$PROJECT_VENV"
    if [[ $duration -le $MAX_DURATION_SINGLE ]]; then
        python3 "$TRANSCRIBE_SCRIPT" "$audio" -o "$out" \
            --model "$VIBEVOICE_MODEL_PATH" --prompt "播客对话转录" --dp 4
    else
        local split_dir; split_dir=$(mktemp -d)
        # shellcheck disable=SC2064
        trap "rm -rf '$split_dir'" RETURN
        local audio_ext="${audio##*.}"
        local num_splits=$(( (duration + SPLIT_DURATION - 1) / SPLIT_DURATION ))
        local i start chunk_dur
        for ((i = 0; i < num_splits; i++)); do
            if [[ $i -eq 0 ]]; then start=0; else start=$(( i * SPLIT_DURATION - SPLIT_OVERLAP )); fi
            if [[ $i -eq $((num_splits - 1)) ]]; then chunk_dur=$(( duration - start )); else chunk_dur=$(( SPLIT_DURATION + SPLIT_OVERLAP )); fi
            ffmpeg -y -ss "$start" -i "$audio" -t "$chunk_dur" -c copy \
                "$split_dir/part_$(printf '%03d' $i).$audio_ext" 2>/dev/null
        done
        for part in "$split_dir"/part_*."$audio_ext"; do
            local part_name; part_name=$(basename "$part" ."$audio_ext")
            python3 "$TRANSCRIBE_SCRIPT" "$part" -o "$split_dir/${part_name}.md" \
                --model "$VIBEVOICE_MODEL_PATH" --prompt "播客对话转录" --dp 4
        done
        local first=true
        for part_md in "$split_dir"/part_*.md; do
            if $first; then cat "$part_md" > "$out"; first=false; else tail -n +6 "$part_md" >> "$out"; fi
        done
    fi
}

# ── Core: transcribe single episode_dir ──────────────────────────────────────
transcribe_episode() {
    local ep_dir="$1"
    [[ -d "$ep_dir" ]] || { err "Not a directory: $ep_dir"; return 1; }

    local audio; audio=$(find_audio "$ep_dir")
    [[ -n "$audio" ]] || { err "No audio file (*.m4a or *.mp3) found in: $ep_dir"; return 1; }

    if [[ -f "$ep_dir/transcript.md" ]] && [[ -s "$ep_dir/transcript.md" ]]; then
        log "Transcript already exists: $ep_dir/transcript.md (skipping)"
        return 0
    fi

    log "Audio: $(basename "$audio") | backend: $VV_BACKEND"
    case "$VV_BACKEND" in
        vllm)    transcribe_vllm "$audio" "$ep_dir/transcript.md" ;;
        pytorch) transcribe_pytorch "$audio" "$ep_dir/transcript.md" "$ep_dir" ;;
        *)       err "Unknown VV_BACKEND: $VV_BACKEND (use vllm|pytorch)"; return 1 ;;
    esac

    # Validate
    [[ -f "$ep_dir/transcript.md" ]] || { err "Transcript not created: $ep_dir/transcript.md"; return 1; }
    local size; size=$(wc -c < "$ep_dir/transcript.md")
    [[ $size -ge 100 ]] || { err "Transcript suspiciously small (${size} bytes): $ep_dir/transcript.md"; return 1; }
    log "Transcription complete: $ep_dir/transcript.md ($(wc -l < "$ep_dir/transcript.md") lines)"
}

# ── Main ─────────────────────────────────────────────────────────────────────
main() {
    if [[ $# -lt 1 ]]; then
        cat >&2 <<EOF
Usage: $0 <episode_dir> [<episode_dir> ...]
Transcribes audio in each episode_dir to transcript.md via the VibeVoice-ASR
vLLM service (VV_BACKEND=vllm, default) or the legacy PyTorch path (pytorch).
Prints TRANSCRIPT=<path> for each success.
EOF
        exit 1
    fi

    local failed=0
    local results=()
    for ep_dir in "$@"; do
        ep_dir=$(realpath -m "$ep_dir")
        log "━━━ Transcribing: $ep_dir ━━━"
        if transcribe_episode "$ep_dir"; then
            results+=("$ep_dir/transcript.md")
        else
            err "Transcription failed for: $ep_dir"; ((failed++))
        fi
    done

    log "Transcription stage complete: ${#results[@]} succeeded, $failed failed"
    for path in "${results[@]}"; do echo "TRANSCRIPT=$path"; done
    [[ $failed -eq 0 ]]
}

main "$@"
