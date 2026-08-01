#!/bin/bash
# Launch VibeVoice ASR server
# Uses all 4 GPUs with device_map="auto" distribution

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/../.venv"
cd "$SCRIPT_DIR"

# Set library path for NVIDIA packages installed via pip
SITE_PKGS="$VENV_DIR/lib/python3.12/site-packages/nvidia"
export LD_LIBRARY_PATH="${SITE_PKGS}/cusparselt/lib:${SITE_PKGS}/cudnn/lib:${SITE_PKGS}/cublas/lib:${SITE_PKGS}/cuda_runtime/lib:${SITE_PKGS}/nvjitlink/lib:${SITE_PKGS}/cufft/lib:${SITE_PKGS}/cusolver/lib:${SITE_PKGS}/cusparse/lib:${SITE_PKGS}/curand/lib:${SITE_PKGS}/nccl/lib:/usr/local/cuda/lib64:${LD_LIBRARY_PATH}"

echo "Starting VibeVoice ASR server on port 8900..."
echo "Model: ${VIBEVOICE_MODEL_PATH:-/workspace/models/VibeVoice-ASR}"
echo "GPUs:  all available (device_map=auto)"
echo ""

CUDA_VISIBLE_DEVICES=0,1,2,3 "$VENV_DIR/bin/python" -m uvicorn vibevoice_asr.server:app \
    --host 0.0.0.0 \
    --port 8900 \
    --workers 1 \
    --log-level info
