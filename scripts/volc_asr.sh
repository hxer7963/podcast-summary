#!/usr/bin/env bash
# Curl-only Volcengine ASR transport. Requires bash + curl; jq is optional.
set -euo pipefail

SUBMIT_URL='https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit'
QUERY_URL='https://openspeech.bytedance.com/api/v3/auc/bigmodel/query'
RESOURCE_ID='volc.seedasr.auc'
POLL_INTERVAL="${VOLC_ASR_POLL_INTERVAL:-10}"
MAX_WAIT="${VOLC_ASR_MAX_WAIT:-1800}"

usage() {
    cat <<'EOF'
Usage:
  scripts/volc_asr.sh submit <public-audio-url> [request-id]
  scripts/volc_asr.sh query <request-id>
  scripts/volc_asr.sh run <public-audio-url> <episode-dir>

Environment:
  VOLC_ASR_API_KEY          required; never pass the key as a CLI argument
  VOLC_ASR_POLL_INTERVAL    seconds, default 10
  VOLC_ASR_MAX_WAIT         seconds, default 1800
  VOLC_ASR_ENABLE_ITN       true/false, default true
  VOLC_ASR_ENABLE_PUNC      true/false, default true
  VOLC_ASR_ENABLE_DDC       true/false, default true
  VOLC_ASR_SPEAKER_INFO     true/false, default true
  VOLC_ASR_CHANNEL_SPLIT    true/false, default false
  VOLC_ASR_VAD_SEGMENT      true/false, default false

The run command always writes volc-response.json. If jq is already available,
it also extracts transcript.md. jq is optional and is never installed here.
EOF
}

die() { printf '[volc-asr] ERROR: %s\n' "$*" >&2; exit 1; }

env_bool() {
    local name="$1" default="$2" value
    value="${!name:-$default}"
    case "$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')" in
        true|1|yes|on) printf 'true' ;;
        false|0|no|off) printf 'false' ;;
        *) die "$name must be true or false" ;;
    esac
}

require_api() {
    command -v curl >/dev/null 2>&1 || die 'curl is required'
    [[ -n "${VOLC_ASR_API_KEY:-}" ]] || die 'VOLC_ASR_API_KEY is not set'
}

new_uuid() {
    if command -v uuidgen >/dev/null 2>&1; then
        uuidgen | tr '[:upper:]' '[:lower:]'
        return
    fi
    local hex
    hex="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
    printf '%s-%s-%s-%s-%s\n' \
        "${hex:0:8}" "${hex:8:4}" "${hex:12:4}" "${hex:16:4}" "${hex:20:12}"
}

audio_format() {
    local path="${1%%\?*}"
    local ext="${path##*.}"
    ext="$(printf '%s' "$ext" | tr '[:upper:]' '[:lower:]')"
    case "$ext" in
        mp3|m4a|mp4|wav|aac|ogg|flac) printf '%s' "$ext" ;;
        opus) printf 'ogg' ;;
        *) printf 'mp3' ;;
    esac
}

validate_audio_url() {
    [[ "$1" == http://* || "$1" == https://* ]] || die 'audio URL must use http or https'
    [[ "$1" != *'"'* && "$1" != *'\'* ]] || die 'audio URL contains unsupported JSON characters'
}

status_from_headers() {
    awk -F': *' '
      tolower($1) == "x-api-status-code" { value=$2; gsub(/\r/, "", value) }
      END { print value }
    ' "$1"
}

message_from_headers() {
    awk -F': *' '
      tolower($1) == "x-api-message" || tolower($1) == "x-api-status-message" {
        value=$2; gsub(/\r/, "", value)
      }
      END { print value }
    ' "$1"
}

post_json() {
    local endpoint="$1" request_id="$2" payload="$3" headers_file="$4" body_file="$5" sequence="$6"
    # Feed the secret header through curl config on stdin so it is not exposed
    # in the process argument list.
    if [[ "$sequence" == 1 ]]; then
        curl --silent --show-error --location --request POST "$endpoint" \
            --dump-header "$headers_file" --output "$body_file" \
            --header 'Content-Type: application/json' \
            --header "X-Api-Resource-Id: $RESOURCE_ID" \
            --header "X-Api-Request-Id: $request_id" \
            --header 'X-Api-Sequence: -1' --data "$payload" \
            --config - <<EOF
header = "x-api-key: ${VOLC_ASR_API_KEY}"
EOF
    else
        curl --silent --show-error --location --request POST "$endpoint" \
            --dump-header "$headers_file" --output "$body_file" \
            --header 'Content-Type: application/json' \
            --header "X-Api-Resource-Id: $RESOURCE_ID" \
            --header "X-Api-Request-Id: $request_id" \
            --data "$payload" --config - <<EOF
header = "x-api-key: ${VOLC_ASR_API_KEY}"
EOF
    fi
}

submit() {
    local audio_url="$1" request_id="${2:-$(new_uuid)}"
    validate_audio_url "$audio_url"
    local format payload tmp_dir headers body code message
    local enable_itn enable_punc enable_ddc speaker_info channel_split vad_segment
    format="$(audio_format "$audio_url")"
    enable_itn="$(env_bool VOLC_ASR_ENABLE_ITN true)"
    enable_punc="$(env_bool VOLC_ASR_ENABLE_PUNC true)"
    enable_ddc="$(env_bool VOLC_ASR_ENABLE_DDC true)"
    speaker_info="$(env_bool VOLC_ASR_SPEAKER_INFO true)"
    channel_split="$(env_bool VOLC_ASR_CHANNEL_SPLIT false)"
    vad_segment="$(env_bool VOLC_ASR_VAD_SEGMENT false)"
    payload="{\"user\":{\"uid\":\"podcast-summary\"},\"audio\":{\"url\":\"$audio_url\",\"format\":\"$format\",\"codec\":\"raw\"},\"request\":{\"model_name\":\"bigmodel\",\"enable_itn\":$enable_itn,\"enable_punc\":$enable_punc,\"enable_ddc\":$enable_ddc,\"enable_speaker_info\":$speaker_info,\"enable_channel_split\":$channel_split,\"show_utterances\":true,\"vad_segment\":$vad_segment,\"sensitive_words_filter\":\"\"}}"
    tmp_dir="$(mktemp -d)"
    headers="$tmp_dir/headers"; body="$tmp_dir/body"
    post_json "$SUBMIT_URL" "$request_id" "$payload" "$headers" "$body" 1
    code="$(status_from_headers "$headers")"
    message="$(message_from_headers "$headers")"
    [[ "$code" == 20000000 ]] || die "submit failed: status=${code:-missing} message=${message:-none} body=$(head -c 500 "$body")"
    printf 'REQUEST_ID=%s\nSTATUS_CODE=%s\n' "$request_id" "$code"
    rm -rf "$tmp_dir"
}

query_to_files() {
    local request_id="$1" headers="$2" body="$3"
    post_json "$QUERY_URL" "$request_id" '{}' "$headers" "$body" 0
}

query() {
    local request_id="$1" tmp_dir headers body code message
    tmp_dir="$(mktemp -d)"
    headers="$tmp_dir/headers"; body="$tmp_dir/body"
    query_to_files "$request_id" "$headers" "$body"
    code="$(status_from_headers "$headers")"
    message="$(message_from_headers "$headers")"
    printf 'STATUS_CODE=%s\n' "${code:-missing}" >&2
    [[ -z "$message" ]] || printf 'STATUS_MESSAGE=%s\n' "$message" >&2
    cat "$body"
    rm -rf "$tmp_dir"
}

run() {
    local audio_url="$1" episode_dir="$2" request_id start now tmp_dir headers body code message
    validate_audio_url "$audio_url"
    mkdir -p "$episode_dir"
    if [[ -s "$episode_dir/transcript.md" ]] && [[ $(wc -c < "$episode_dir/transcript.md") -ge 100 ]]; then
        printf 'TRANSCRIPT=%s\n' "$(cd "$episode_dir" && pwd)/transcript.md"
        return
    fi
    if [[ ! -f "$episode_dir/README.md" ]]; then
        printf '# Audio\n\n> Audio URL: %s\n> Source: direct public audio URL\n' "$audio_url" > "$episode_dir/README.md"
    fi
    request_id="$(submit "$audio_url" | awk -F= '$1=="REQUEST_ID"{print $2}')"
    [[ -n "$request_id" ]] || die 'submit returned no request id'
    start="$(date +%s)"
    tmp_dir="$(mktemp -d)"
    headers="$tmp_dir/headers"; body="$tmp_dir/body"
    while :; do
        query_to_files "$request_id" "$headers" "$body"
        code="$(status_from_headers "$headers")"
        case "$code" in
            20000000)
                cp "$body" "$episode_dir/volc-response.json"
                if command -v jq >/dev/null 2>&1; then
                    jq -er '
                      def addition($key):
                        if (.additions | type) == "object" then .additions[$key] else null end;
                      def speaker:
                        (addition("speaker") // .speaker // .speaker_id // null);
                      def channel:
                        (addition("channel_id") // .channel_id // null);
                      def line:
                        . as $u
                        | (speaker) as $speaker
                        | (channel) as $channel
                        | "**[\(($u.start_time // 0))–\(($u.end_time // 0)) ms]"
                          + (if $speaker == null then "" else " [Speaker \($speaker)]" end)
                          + (if $channel == null then "" else " [Channel \($channel)]" end)
                          + "** \(($u.text // "") | gsub("[\\r\\n]+"; " "))";
                      (.result.utterances // .utterances // []) as $utterances
                      | (if ($utterances | length) > 0
                         then ($utterances | map(line) | join("\n\n"))
                         else (.result.text // .text // .transcript // "")
                         end) as $content
                      | select(($content | length) > 0)
                      | "# Transcription\n\n"
                        + "> ASR: Volcengine bigmodel; speaker diarization and utterance timing requested\n\n"
                        + $content
                    ' "$body" > "$episode_dir/transcript.md" \
                        || die 'query succeeded but no transcript text or utterances were found'
                    printf 'TRANSCRIPT=%s\n' "$(cd "$episode_dir" && pwd)/transcript.md"
                else
                    printf 'RESULT_JSON=%s\n' "$(cd "$episode_dir" && pwd)/volc-response.json"
                    printf 'NEXT=Agent should format result.utterances with timestamps and speaker labels; fall back to result.text\n'
                fi
                rm -rf "$tmp_dir"
                return
                ;;
            20000001|20000002)
                now="$(date +%s)"
                ((now - start < MAX_WAIT)) || die "polling timed out after ${MAX_WAIT}s"
                sleep "$POLL_INTERVAL"
                ;;
            20000003) die 'audio is silent' ;;
            *)
                message="$(message_from_headers "$headers")"
                die "query failed: status=${code:-missing} message=${message:-none} body=$(head -c 500 "$body")"
                ;;
        esac
    done
}

case "${1:-}" in
    submit) [[ $# -ge 2 && $# -le 3 ]] || { usage >&2; exit 2; }; require_api; submit "$2" "${3:-}" ;;
    query) [[ $# -eq 2 ]] || { usage >&2; exit 2; }; require_api; query "$2" ;;
    run) [[ $# -eq 3 ]] || { usage >&2; exit 2; }; require_api; run "$2" "$3" ;;
    -h|--help|help) usage ;;
    *) usage >&2; exit 2 ;;
esac
