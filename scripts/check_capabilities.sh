#!/usr/bin/env bash
# Dependency-free capability report for the agent-facing shell path.
set -u

has() { command -v "$1" >/dev/null 2>&1; }
bool() { "$@" && printf true || printf false; }

python_ok=false
if has python3; then
    python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1 && python_ok=true
fi
curl_ok="$(bool has curl)"
key_ok=false; [[ -n "${VOLC_ASR_API_KEY:-}" ]] && key_ok=true
uv_ok="$(bool has uv)"
ffmpeg_ok="$(bool has ffmpeg)"
gpu_ok=false; has nvidia-smi && nvidia-smi >/dev/null 2>&1 && gpu_ok=true
docker_ok=false; has docker && docker version >/dev/null 2>&1 && docker_ok=true
jq_ok="$(bool has jq)"

if [[ "${1:-}" == --json ]]; then
    cat <<EOF
{
  "skill_hub": {"ready": true, "requires": []},
  "summary": {"ready": true, "requires": []},
  "volcengine_curl_transport": {"ready": $curl_ok, "configured": $key_ok, "jq_optional": $jq_ok},
  "python_helpers": {"ready": $python_ok, "requires": ["python>=3.10"]},
  "article_fetch": {"ready": $python_ok, "requires": ["python>=3.10"], "packages": []},
  "wechat_fetch": {"ready": $python_ok, "requires": ["python>=3.10"], "packages": []},
  "podhood_fetch": {"ready": $python_ok, "requires": ["python>=3.10"], "packages": []},
  "optional_installer": {"ready": $uv_ok, "requires": ["uv"]},
  "local_gpu_asr": {"gpu": $gpu_ok, "docker": $docker_ok, "ffmpeg": $ffmpeg_ok}
}
EOF
    exit 0
fi

printf 'Capabilities:\n'
printf '  ✓ skill_hub: ready (no runtime packages)\n'
printf '  ✓ summary: ready (agent-native)\n'
[[ "$curl_ok" == true ]] && printf '  ✓ volcengine curl transport: ready\n' || printf '  · volcengine curl transport: curl missing\n'
[[ "$key_ok" == true ]] && printf '  ✓ VOLC_ASR_API_KEY: configured\n' || printf '  · VOLC_ASR_API_KEY: not set\n'
[[ "$python_ok" == true ]] && printf '  ✓ optional Python helpers: ready\n' || printf '  · optional Python helpers: Python 3.10+ missing\n'
[[ "$python_ok" == true ]] && printf '  ✓ article/WeChat/PodHood fetch: ready (standard library)\n' || printf '  · article/WeChat/PodHood fetch: Python 3.10+ missing\n'
[[ "$gpu_ok" == true ]] && printf '  ✓ local GPU detected\n' || printf '  · local GPU: not detected (no GPU packages will be installed)\n'
