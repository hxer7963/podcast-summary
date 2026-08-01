#!/usr/bin/env bash
#
# download_vibevoice_model.sh — 下载 VibeVoice-ASR 模型并转换为 vLLM 格式。
#
# 流程:
#   1. 用 hf_download.sh 下载 microsoft/VibeVoice-ASR（transformers 原生格式）
#   2. 复制一份为 VibeVoice-ASR-vllm
#   3. 修改 config.json: architectures → VibeVoiceForASRTraining, model_type → vibevoice
#   4. (可选) 生成 tokenizer files — 通常由 docker-entrypoint 自动完成
#
# 用法:
#   bash setup/download_vibevoice_model.sh
#   HF_MODELS_ROOT=$HOME/models bash setup/download_vibevoice_model.sh
#
# 环境:
#   HF_MODELS_ROOT  模型存储根目录 (默认: /workspace/models)
#   HF_TOKEN        HuggingFace 访问令牌 (如需要)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODELS_ROOT="${HF_MODELS_ROOT:-${MODELS_ROOT:-/workspace/models}}"
HF_MODEL_ID="microsoft/VibeVoice-ASR"
HF_MODEL_DIR="${MODELS_ROOT}/VibeVoice-ASR"
VLLM_MODEL_DIR="${MODELS_ROOT}/VibeVoice-ASR-vllm"

log()  { printf '\033[1;34m[INFO]\033[0m  %s\n' "$*"; }
warn() { printf '\033[1;33m[WARN]\033[0m  %s\n' "$*" >&2; }
err()  { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; }

# ── 1. 下载 HF 原生模型 ──────────────────────────────────────────────────────
if [[ ! -d "$HF_MODEL_DIR" ]] || [[ -z "$(ls -A "$HF_MODEL_DIR" 2>/dev/null)" ]]; then
    log "下载 ${HF_MODEL_ID} → ${HF_MODEL_DIR}"
    bash "${SCRIPT_DIR}/hf_download.sh" "$HF_MODEL_ID"
else
    log "HF 原生模型已存在: ${HF_MODEL_DIR} (跳过下载)"
fi

# ── 2. 复制为 vLLM 格式目录 ───────────────────────────────────────────────────
if [[ ! -d "$VLLM_MODEL_DIR" ]]; then
    log "复制为 vLLM 格式目录: ${VLLM_MODEL_DIR}"
    cp -r "$HF_MODEL_DIR" "$VLLM_MODEL_DIR"
else
    log "vLLM 格式目录已存在: ${VLLM_MODEL_DIR} (跳过复制)"
fi

# ── 3. 转换 config.json ───────────────────────────────────────────────────────
CONFIG="${VLLM_MODEL_DIR}/config.json"
if [[ ! -f "$CONFIG" ]]; then
    err "config.json 未找到: ${CONFIG}"
    exit 1
fi

log "转换 config.json 为 vLLM 格式 (architectures=VibeVoiceForASRTraining, model_type=vibevoice)"
python3 - "$CONFIG" <<'PYEOF'
import json
import sys

config_path = sys.argv[1]
with open(config_path, "r", encoding="utf-8") as f:
    cfg = json.load(f)

# vLLM plugin 注册的架构名 (与 transformers 原生版不同)
cfg["architectures"] = ["VibeVoiceForASRTraining"]
cfg["model_type"] = "vibevoice"

with open(config_path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)

print(f"  architectures = {cfg['architectures']}")
print(f"  model_type    = {cfg['model_type']}")
PYEOF

# ── 4. tokenizer files 提示 ───────────────────────────────────────────────────
if [[ ! -f "${VLLM_MODEL_DIR}/added_tokens.json" ]]; then
    warn "tokenizer files (added_tokens.json, vocab.json, merges.txt) 尚未生成。"
    warn "这些文件会在首次启动 docker 容器时由 docker-entrypoint 自动生成"
    warn "(需要网络访问以下载 Qwen2.5-7B tokenizer)。"
    warn "或手动运行 (需要先 pip install -e /path/to/VibeVoice):"
    warn "  python3 -m vllm_plugin.tools.generate_tokenizer_files --output ${VLLM_MODEL_DIR}"
else
    log "tokenizer files 已存在，无需生成"
fi

echo
log "完成！vLLM 格式模型位于: ${VLLM_MODEL_DIR}"
echo
echo "下一步:"
echo "  1. 拉取预构建 Docker 镜像 (见 docs/vibevoice-local-setup.md):"
echo "     docker pull hxer7963/vibevoice-asr-vllm:latest"
echo "  2. 启动服务:"
echo "     bash vibevoice-asr/serve_vllm.sh start"
echo "  3. 验证:"
echo "     curl http://localhost:8000/health"
