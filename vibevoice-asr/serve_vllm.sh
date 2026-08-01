#!/usr/bin/env bash
# serve_vllm.sh — Launch/manage the VibeVoice-ASR vLLM service (Docker).
#
# The service exposes an OpenAI-compatible API on http://localhost:8000
# (model name "vibevoice", endpoint /v1/chat/completions). The transcribe
# client (transcribe_vllm.py) talks to it.
#
# Usage:
#   bash serve_vllm.sh start      # start (idempotent: no-op if already healthy)
#   bash serve_vllm.sh stop       # stop & remove container
#   bash serve_vllm.sh restart
#   bash serve_vllm.sh status     # health + GPU memory
#   bash serve_vllm.sh logs       # follow logs
#
# Env overrides:
#   VV_MODEL_PATH   model dir (vLLM-format checkpoint)  [default below]
#   VV_REPO         VibeVoice repo (vllm_plugin lives here)
#   VV_PORT         host port                            [8000]
#   VV_TP           tensor-parallel size                 [4]
#   VV_GPU_MEM      gpu-memory-utilization               [0.7]
#   VV_IMAGE        docker image                         [vllm/vllm-openai:v0.14.1]
set -euo pipefail

# ── Config ─────────────────────────────────────────────────────────────────
# IMPORTANT: use the vLLM-format checkpoint (architectures=VibeVoiceForASRTraining,
# model_type=vibevoice). The transformers-native VibeVoice-ASR-HF checkpoint is
# NOT compatible with the vllm_plugin. See the VibeVoice repo for details.
#
# All paths below are overridable via environment variables. Defaults assume the
# layout produced by `setup/download_vibevoice_model.sh` and a local clone of
# https://github.com/microsoft/VibeVoice (only needed for the fallback path that
# mounts the repo into a stock vLLM image — prefer the self-contained image).
VV_MODEL_PATH="${VV_MODEL_PATH:-/workspace/models/VibeVoice-ASR-vllm}"
VV_REPO="${VV_REPO:-$HOME/VibeVoice}"
VV_MODELS_ROOT="${VV_MODELS_ROOT:-/workspace/models}"
VV_PORT="${VV_PORT:-8000}"
VV_TP="${VV_TP:-4}"
# gpu-memory-utilization. PROFILING NOTE (measured 2026-07, 4×RTX 4090, tp=4):
#   The audio-encoder forward pass has a FIXED peak of ~22.7GB/GPU regardless of
#   chunk size (60min == 80min) or concurrency (1×60 == 4×60) — its ~8.2GB working
#   set sits on top of the LM weights, OUTSIDE vLLM's KV budget. So headroom ≈
#   24×(1-util) − 8.2:
#     0.70 → 1.8GB free at idle, encoder fully absorbed → ~5.4GB "free" (KV-loose)
#     0.85 → 1.8GB headroom at PEAK (measured) → largest safe KV budget ✅
#     0.88 → ~0.6GB (risky), 0.90 → ~0.1GB, 0.92 → OOM (encoder can't fit)
#   At 0.7 the binding limit is KV cache, NOT the encoder: 4×60min saturates KV
#   to 99% (throughput collapses 430→21 tok/s). 0.85 enlarges the KV pool so 4×60min
#   (KV 44%) and 3×60min (KV 26%) run clean, while single-chunk throughput is
#   unchanged (4×40min: 0.7=139s == 0.85=142s). 0.85 is the ceiling — do NOT go higher.
VV_GPU_MEM="${VV_GPU_MEM:-0.85}"
VV_USE_MEAN="${VV_USE_MEAN:-1}"   # 1 = deterministic acoustic encoding (reproducible, fewer loops)
# Prefer the self-contained image (entrypoint = optimized launch). If it is not
# built yet, fall back to the stock vLLM image with the repo mounted.
# Prefer the pre-built image on Docker Hub (hxer7963/vibevoice-asr-vllm:latest).
# If you build locally, override with:  VV_IMAGE=vibevoice-asr-vllm:latest
VV_IMAGE="${VV_IMAGE:-hxer7963/vibevoice-asr-vllm:latest}"
VV_FALLBACK_IMAGE="${VV_FALLBACK_IMAGE:-vllm/vllm-openai:v0.14.1}"
CONTAINER="vibevoice-vllm"

log() { echo "[serve_vllm] $*" >&2; }

wait_ready() {
    local tries="${1:-60}"
    for ((i = 0; i < tries; i++)); do
        if curl -sf "http://localhost:${VV_PORT}/health" -o /dev/null 2>/dev/null; then
            return 0
        fi
        # Fail fast if the container died during startup
        if ! docker ps --filter "name=${CONTAINER}" --format '{{.Names}}' | grep -q "${CONTAINER}"; then
            log "container exited during startup; last logs:"
            docker logs "${CONTAINER}" 2>&1 | tail -25 >&2
            return 1
        fi
        sleep 5
    done
    return 1
}

cmd_start() {
    if docker ps --filter "name=${CONTAINER}" --format '{{.Names}}' | grep -q "${CONTAINER}" \
        && curl -sf "http://localhost:${VV_PORT}/health" -o /dev/null 2>/dev/null; then
        log "already running and healthy on :${VV_PORT}"
        return 0
    fi
    docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true

    if [[ ! -f "${VV_MODEL_PATH}/config.json" ]]; then
        log "ERROR: model config not found: ${VV_MODEL_PATH}/config.json"; exit 1
    fi

    log "starting ${CONTAINER}: model=${VV_MODEL_PATH} tp=${VV_TP} gpu_mem=${VV_GPU_MEM}"
    if docker image inspect "${VV_IMAGE}" >/dev/null 2>&1; then
        # Self-contained image: its ENTRYPOINT runs the optimized launch. Only
        # the model weights are mounted; config comes from -e VV_* vars.
        log "using self-contained image ${VV_IMAGE}"
        docker run -d --gpus all --name "${CONTAINER}" --ipc=host -p "${VV_PORT}:8000" \
            -e VV_MODEL_PATH="${VV_MODEL_PATH}" \
            -e VV_TP="${VV_TP}" \
            -e VV_GPU_MEM="${VV_GPU_MEM}" \
            -e VIBEVOICE_USE_MEAN="${VV_USE_MEAN}" \
            -v "${VV_MODELS_ROOT}:${VV_MODELS_ROOT}" \
            "${VV_IMAGE}" >/dev/null
    else
        # Fallback: stock vLLM image + repo mount + start_server.py.
        log "image ${VV_IMAGE} not found; falling back to ${VV_FALLBACK_IMAGE} + repo mount"
        docker run -d --gpus all --name "${CONTAINER}" --ipc=host -p "${VV_PORT}:8000" \
            -e PYTORCH_ALLOC_CONF=expandable_segments:True \
            -e PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
            -e VIBEVOICE_FFMPEG_MAX_CONCURRENCY=64 \
            -e VIBEVOICE_USE_MEAN="${VV_USE_MEAN}" \
            -v "${VV_REPO}:/app" \
            -v "${VV_MODELS_ROOT}:${VV_MODELS_ROOT}" \
            -w /app --entrypoint bash "${VV_FALLBACK_IMAGE}" \
            -c "python3 /app/vllm_plugin/scripts/start_server.py --model ${VV_MODEL_PATH} --tp ${VV_TP} --gpu-memory-utilization ${VV_GPU_MEM} --skip-tokenizer" \
            >/dev/null
    fi

    log "waiting for service to become healthy (model load takes ~2-3 min)..."
    if wait_ready 60; then
        log "READY: http://localhost:${VV_PORT} (model: vibevoice)"
    else
        log "ERROR: service did not become healthy in time"; exit 1
    fi
}

cmd_stop() {
    docker rm -f "${CONTAINER}" >/dev/null 2>&1 && log "stopped" || log "not running"
}

cmd_status() {
    docker ps -a --filter "name=${CONTAINER}" --format 'container: {{.Names}} — {{.Status}}'
    local code
    code=$(curl -s "http://localhost:${VV_PORT}/health" -o /dev/null -w '%{http_code}' 2>/dev/null || echo 000)
    echo "health(:${VV_PORT}) = ${code}"
    command -v nvidia-smi >/dev/null && nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader
}

case "${1:-start}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    restart) cmd_stop; cmd_start ;;
    status)  cmd_status ;;
    logs)    docker logs -f "${CONTAINER}" ;;
    *) echo "usage: $0 {start|stop|restart|status|logs}" >&2; exit 1 ;;
esac
