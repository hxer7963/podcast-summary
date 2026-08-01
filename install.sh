#!/usr/bin/env bash
# Install the skill hub itself by default. Optional runtimes are explicit.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

WITH_FETCH=0
WITH_SUBTITLE=0

usage() {
    cat <<'EOF'
Usage: bash install.sh [options]

Default: install nothing. Skill discovery and summary are agent-native; the
Volcengine transport uses curl. Python helpers are optional and are never
installed by the default path.

Options:
  --with-fetch       Install dependencies for RSS/Apple/Spotify fetch
  --with-subtitle    Install yt-dlp for YouTube/Bilibili subtitles
  --with-all         Install both optional groups above
  -h, --help         Show this help

Local GPU ASR is intentionally not installed here. Its Docker image and model
are lazy-loaded only after a GPU check and explicit user confirmation.
EOF
}

while (($#)); do
    case "$1" in
        --with-fetch) WITH_FETCH=1 ;;
        --with-subtitle) WITH_SUBTITLE=1 ;;
        --with-all) WITH_FETCH=1; WITH_SUBTITLE=1 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

if ((WITH_FETCH || WITH_SUBTITLE)); then
    command -v python3 >/dev/null 2>&1 || {
        printf '[ERROR] Optional Python features require Python 3.10+.\n' >&2
        exit 1
    }
    python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || {
        printf '[ERROR] Optional Python features require Python 3.10+.\n' >&2
        exit 1
    }
    if ! command -v uv >/dev/null 2>&1; then
        printf '[ERROR] Optional dependencies requested, but uv is not installed.\n' >&2
        printf 'Install uv from https://docs.astral.sh/uv/ and rerun this command.\n' >&2
        exit 1
    fi
    groups=()
    ((WITH_FETCH)) && groups+=(--group fetch)
    ((WITH_SUBTITLE)) && groups+=(--group subtitle)
    uv sync "${groups[@]}"
fi

printf '\n[OK] podcast-summary skill hub is ready.\n'
printf '     Default install added 0 packages and requires no Python, uv, or ffmpeg.\n'
bash scripts/check_capabilities.sh
printf '\nGive your agent a public audio URL and VOLC_ASR_API_KEY, or invoke any\n'
printf 'individual skill such as podcast-summary or podcasttranscript-fetch.\n'
