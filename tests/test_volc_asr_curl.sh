#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TEST_DIR="$(mktemp -d)"
trap 'rm -rf "$TEST_DIR"' EXIT
mkdir -p "$TEST_DIR/bin" "$TEST_DIR/out"

cat > "$TEST_DIR/bin/curl" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
headers=''; output=''; endpoint=''
for ((i=1; i<=$#; i++)); do
    arg="${!i}"
    [[ "$arg" != *test-secret* ]] || { echo 'secret leaked in argv' >&2; exit 90; }
    case "$arg" in
        --dump-header) next=$((i+1)); headers="${!next}" ;;
        --output) next=$((i+1)); output="${!next}" ;;
        https://*) endpoint="$arg" ;;
    esac
done
if [[ "$endpoint" == */submit ]]; then
    printf 'HTTP/2 200\r\nX-Api-Status-Code: 20000000\r\n\r\n' > "$headers"
    printf '{}' > "$output"
else
    printf 'HTTP/2 200\r\nX-Api-Status-Code: 20000000\r\n\r\n' > "$headers"
    printf '{"result":{"text":"mock transcript long enough for the transport test"}}' > "$output"
fi
FAKE
chmod +x "$TEST_DIR/bin/curl"

export PATH="$TEST_DIR/bin:/usr/bin:/bin"
export VOLC_ASR_API_KEY='test-secret'
export VOLC_ASR_POLL_INTERVAL=0
export VOLC_ASR_MAX_WAIT=5

submit_output="$(bash "$ROOT_DIR/scripts/volc_asr.sh" submit 'https://example.com/audio.mp3' '11111111-1111-1111-1111-111111111111')"
grep -q '^REQUEST_ID=11111111-1111-1111-1111-111111111111$' <<<"$submit_output"

bash "$ROOT_DIR/scripts/volc_asr.sh" run 'https://example.com/audio.mp3' "$TEST_DIR/out" > "$TEST_DIR/run-output"
test -s "$TEST_DIR/out/README.md"
test -s "$TEST_DIR/out/volc-response.json"
grep -q 'mock transcript' "$TEST_DIR/out/volc-response.json"
grep -Eq '^(RESULT_JSON|TRANSCRIPT)=' "$TEST_DIR/run-output"

printf 'curl transport test: OK\n'
