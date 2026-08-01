#!/usr/bin/env bash
#
# hf_download.sh - 下载 HuggingFace 模型到 /workspace/models/
#
# 用法:
#   hf_download.sh <org>/<repo>        # 下载模型
#   hf_download.sh -h | --help         # 查看帮助
#
# 示例:
#   hf_download.sh microsoft/VibeVoice-ASR
#   hf_download.sh Qwen/Qwen3-Coder-480B-A35B-Instruct
#
set -euo pipefail

# Default model storage root. Override with $HF_MODELS_ROOT or $MODELS_ROOT.
# Common choices: /workspace/models, $HOME/models, /data/models
MODELS_ROOT="${HF_MODELS_ROOT:-${MODELS_ROOT:-/workspace/models}}"
MAX_WORKERS="${HF_DOWNLOAD_WORKERS:-24}"

print_usage() {
    cat <<'EOF'
用法: hf_download.sh <model_id>
  model_id   HuggingFace 模型 ID，形如 <org>/<repo> 或 <repo>
             例如: microsoft/VibeVoice-ASR

环境变量:
  HF_DOWNLOAD_WORKERS   并发下载线程数 (默认: 24)
  HF_TOKEN              HuggingFace 访问令牌 (下载 gated repo 时需要)

示例:
  hf_download.sh microsoft/VibeVoice-ASR
  HF_DOWNLOAD_WORKERS=8 hf_download.sh Qwen/Qwen3-Coder-480B-A35B-Instruct
EOF
}

log()  { printf '\033[1;34m[INFO]\033[0m  %s\n' "$*"; }
warn() { printf '\033[1;33m[WARN]\033[0m  %s\n' "$*" >&2; }
err()  { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; }

# -------- 参数解析 --------
if [[ $# -ne 1 ]]; then
    print_usage
    exit 1
fi

case "$1" in
    -h|--help)
        print_usage
        exit 0
        ;;
esac

MODEL_ID="$1"

# -------- 输入校验 --------
# 允许 <org>/<repo> 或 <repo>，字符集限定 [A-Za-z0-9._-]
# 防止路径穿越、命令注入
if [[ ! "$MODEL_ID" =~ ^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)?$ ]]; then
    err "无效的 model_id: '$MODEL_ID'"
    err "model_id 只能包含字母、数字、点、下划线、短横线，以及最多一个斜杠。"
    exit 2
fi

# 禁止 .. 片段
if [[ "$MODEL_ID" == *"/.."* || "$MODEL_ID" == "../"* || "$MODEL_ID" == ".." ]]; then
    err "model_id 含非法路径片段。"
    exit 2
fi

# 提取 repo 名 (斜杠之后的部分)，用于本地目录
if [[ "$MODEL_ID" == */* ]]; then
    REPO_NAME="${MODEL_ID#*/}"
else
    REPO_NAME="$MODEL_ID"
fi

# 再次校验提取出的目录名不含路径分隔符或为特殊目录
if [[ "$REPO_NAME" == */* || "$REPO_NAME" == "." || "$REPO_NAME" == ".." ]]; then
    err "无效的 repo 名称: '$REPO_NAME'"
    exit 2
fi

TARGET_DIR="${MODELS_ROOT}/${REPO_NAME}"

# -------- 环境检查 --------
if ! command -v hf >/dev/null 2>&1; then
    err "未找到 hf 命令，请先安装:  uv pip install -U 'huggingface_hub[cli]'"
    exit 3
fi

if [[ ! -d "$MODELS_ROOT" ]]; then
    err "目标根目录不存在: $MODELS_ROOT"
    exit 4
fi

if [[ ! -w "$MODELS_ROOT" ]]; then
    err "目标根目录不可写: $MODELS_ROOT"
    exit 4
fi

# -------- 处理已存在目录 --------
if [[ -d "$TARGET_DIR" ]]; then
    if [[ -n "$(ls -A "$TARGET_DIR" 2>/dev/null)" ]]; then
        warn "目标目录已存在且非空: $TARGET_DIR"
        warn "将在此基础上断点续传 (hf download 默认支持)。"
        read -r -p "是否继续? [y/N] " ans
        case "$ans" in
            y|Y|yes|YES) ;;
            *)
                log "已取消。"
                exit 0
                ;;
        esac
    else
        log "目标目录已存在但为空: $TARGET_DIR"
    fi
else
    log "创建目标目录: $TARGET_DIR"
    mkdir -p "$TARGET_DIR"
fi

# -------- 执行下载 --------
log "model_id    : $MODEL_ID"
log "目标目录    : $TARGET_DIR"
log "并发线程数  : $MAX_WORKERS"
log "命令        : hf download $MODEL_ID --local-dir $TARGET_DIR --max-workers $MAX_WORKERS"
echo

set -x
hf download "$MODEL_ID" \
    --local-dir "$TARGET_DIR" \
    --max-workers "$MAX_WORKERS"
set +x

echo
log "下载完成: $TARGET_DIR"
ls -lh "$TARGET_DIR" | head -20
