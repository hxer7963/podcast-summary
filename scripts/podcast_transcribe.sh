#!/usr/bin/env bash
# podcast_transcribe.sh — Backward-compat wrapper: download + transcribe (xiaoyuzhou)
#
# Usage:
#   bash scripts/podcast_transcribe.sh <url1> [url2] [url3] ...
#
# This is a thin wrapper that preserves the old end-to-end CLI:
#   1. Calls xiaoyuzhou_download.py to fetch each URL → episode_dir
#   2. Calls transcribe.sh on each episode_dir → transcript.md
#
# For new code, prefer invoking the two stages independently:
#   - Fetch:      python3 scripts/xiaoyuzhou_download.py <url> --output-dir audios/xiaoyuzhou --no-transcribe
#   - Transcribe: bash    scripts/transcribe.sh <episode_dir>
#
# Output:
#   Prints EPISODE_DIR=<path> for each successfully processed URL.
#   Exit 0 if all succeed, 1 if any fail.

set -euo pipefail

# ── Paths ────────────────────────────────────────────────────────────────────
# Resolve the project root from this script's location (scripts/ → repo root).
# Override with $PODCAST_SUMMARY_ROOT if you install the repo elsewhere.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${PODCAST_SUMMARY_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
SCRIPTS_DIR="$PROJECT_DIR/scripts"
PROJECT_VENV="${PODCAST_SUMMARY_VENV:-$PROJECT_DIR/.venv/bin/activate}"
DOWNLOAD_SCRIPT="$SCRIPTS_DIR/xiaoyuzhou_download.py"
TRANSCRIBE_SH="$SCRIPTS_DIR/transcribe.sh"
OUTPUT_BASE="${PODCAST_OUTPUT_DIR:-$PROJECT_DIR/audios/xiaoyuzhou}"

# ── Helpers ──────────────────────────────────────────────────────────────────
log() { echo "[$(date '+%H:%M:%S')] $*" >&2; }
err() { echo "[$(date '+%H:%M:%S')] ERROR: $*" >&2; }

download_episode() {
    local url="$1"
    log "Downloading: $url"

    # shellcheck disable=SC1090
    source "$PROJECT_VENV"

    local output
    output=$(python3 "$DOWNLOAD_SCRIPT" "$url" \
        --output-dir "$OUTPUT_BASE" \
        --no-transcribe 2>&1)

    # Extract episode directory from output (✓ is UTF-8 multi-byte)
    local ep_dir
    ep_dir=$(echo "$output" | grep '✓ Episode complete:' | sed 's/.*✓ Episode complete: //' | tail -1)

    if [[ -z "$ep_dir" ]]; then
        err "Could not determine episode directory from download output"
        echo "$output" >&2
        return 1
    fi

    echo "$ep_dir"
}

# ── Main ─────────────────────────────────────────────────────────────────────
main() {
    if [[ $# -lt 1 ]]; then
        cat >&2 <<EOF
Usage: $0 <xiaoyuzhou_url> [url2] [url3] ...

Downloads and transcribes podcast episodes from xiaoyuzhoufm.com.
Outputs the episode directory path for each processed URL.

Stages:
  1. Download   ← xiaoyuzhou_download.py
  2. Transcribe ← transcribe.sh (via vibevoice-asr)
EOF
        exit 1
    fi

    local urls=("$@")
    local failed=0
    local results=()

    for url in "${urls[@]}"; do
        log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        log "Processing: $url"
        log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

        local ep_dir
        ep_dir=$(download_episode "$url") || { err "Download failed for: $url"; ((failed++)); continue; }

        log "Episode dir: $ep_dir"

        # Delegate transcription
        if bash "$TRANSCRIBE_SH" "$ep_dir" >/dev/null; then
            results+=("$ep_dir")
            log "Done: $ep_dir"
        else
            err "Transcription failed for: $ep_dir"
            ((failed++))
        fi
        echo "" >&2
    done

    log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    log "Pipeline complete: ${#results[@]} succeeded, $failed failed"
    for dir in "${results[@]}"; do
        echo "EPISODE_DIR=$dir"
    done

    [[ $failed -eq 0 ]]
}

main "$@"
