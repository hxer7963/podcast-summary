#!/bin/bash
# Launch VibeVoice ASR with vLLM in Docker
# Uses DP=4 (4 model replicas) for maximum throughput
# Model mounted from /app/models/VibeVoice-ASR

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Default model path inside the container; override with $VIBEVOICE_MODEL_DIR.
MODEL_DIR="${VIBEVOICE_MODEL_DIR:-/app/models/VibeVoice-ASR}"
VIBEVOICE_REPO="${VIBEVOICE_REPO:-/tmp/VibeVoice}"
PORT="${VV_PORT:-8000}"

# Check model exists
if [ ! -f "${MODEL_DIR}/config.json" ]; then
    echo "Error: Model not found at ${MODEL_DIR}"
    echo "Download with: huggingface-cli download microsoft/VibeVoice-ASR --local-dir ${MODEL_DIR}"
    exit 1
fi

echo "Starting VibeVoice vLLM server (Docker)..."
echo "  Model:  ${MODEL_DIR}"
echo "  Port:   ${PORT}"
echo "  GPUs:   all (4x RTX 4090)"
echo "  Mode:   DP=4 (4 replicas for throughput)"
echo ""

docker run -d --gpus all --name vibevoice-vllm \
    --ipc=host \
    -p ${PORT}:${PORT} \
    -e VIBEVOICE_FFMPEG_MAX_CONCURRENCY=64 \
    -e PYTORCH_ALLOC_CONF=expandable_segments:True \
    -v /app:/app \
    -v ${VIBEVOICE_REPO}:/workspace \
    -w /workspace \
    --entrypoint bash \
    vllm/vllm-openai:v0.14.1 \
    -c "apt-get update && apt-get install -y ffmpeg libsndfile1 && \
        pip install -e /workspace[vllm] && \
        python3 -m vllm_plugin.tools.generate_tokenizer_files --output /app/models/VibeVoice-ASR && \
        vllm serve /app/models/VibeVoice-ASR \
            --served-model-name vibevoice \
            --trust-remote-code \
            --dtype bfloat16 \
            --max-num-seqs 64 \
            --max-model-len 65536 \
            --gpu-memory-utilization 0.85 \
            --no-enable-prefix-caching \
            --enable-chunked-prefill \
            --chat-template-content-format openai \
            --data-parallel-size 4 \
            --allowed-local-media-path /app \
            --port ${PORT}"

echo ""
echo "Container started. Checking logs..."
echo "  docker logs -f vibevoice-vllm"
echo ""
echo "Test:"
echo "  curl http://localhost:${PORT}/health"
