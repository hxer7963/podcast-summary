# 本地 GPU ASR 部署指南 (VibeVoice-ASR vLLM)

本指南介绍如何在本地 GPU 主机上部署 VibeVoice-ASR vLLM 服务，用于播客音频转录（带 speaker 分离）。

## 前置条件

### 硬件

- **GPU**：4× NVIDIA RTX 4090 (24GB each) 或同等显存（最低单卡 24GB，但推荐 4 卡以获得 ~3x 吞吐）
- **磁盘**：~20GB（模型权重 ~15GB + Docker 镜像 ~5GB）
- **内存**：≥ 64GB

### 软件

- **NVIDIA Driver**：≥ 550（支持 CUDA 12.4）
- **Docker**：≥ 24.0，安装了 `nvidia-container-toolkit`
- **Python**：≥ 3.12（用于客户端脚本）
- **uv**：Python 包管理器（`curl -LsSf https://astral.sh/uv/install.sh | sh`）
- **ffmpeg**：音频处理

```bash
# 验证 GPU
nvidia-smi

# 验证 Docker + GPU
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi

# 验证 ffmpeg
ffmpeg -version | head -1
```

## 步骤 1：安装 podcast-summary 仓库

```bash
git clone <repo-url> podcast-summary
cd podcast-summary

# 安装基础依赖（字幕路径，无 CUDA）
uv sync --group subtitle

# 安装 GPU ASR 依赖（仅 Linux GPU 主机）
uv venv .venv
uv pip install --python .venv/bin/python3 -r requirements-asr.txt \
  --index-url https://download.pytorch.org/whl/cu124
```

## 步骤 2：下载 VibeVoice-ASR 模型

### 2.1 克隆 Microsoft VibeVoice 仓库（构建 Docker 镜像需要）

```bash
git clone https://github.com/microsoft/VibeVoice.git ~/VibeVoice
```

### 2.2 下载模型并转换为 vLLM 格式

```bash
# 设置模型存储目录（默认 /workspace/models，可改）
export HF_MODELS_ROOT=/workspace/models   # 或 $HOME/models

# 下载 + 转换
bash setup/download_vibevoice_model.sh
```

脚本会：
1. 从 HuggingFace 下载 `microsoft/VibeVoice-ASR`（~15GB，需要 ~10-30 分钟）
2. 复制为 `VibeVoice-ASR-vllm` 目录
3. 修改 `config.json`：`architectures` → `VibeVoiceForASRTraining`，`model_type` → `vibevoice`

> **tokenizer files**（`added_tokens.json`, `vocab.json`, `merges.txt`）会在首次启动 Docker 容器时由 docker-entrypoint 自动生成（需要网络访问以下载 Qwen2.5-7B tokenizer）。

### 2.3 (可选) 手动生成 tokenizer files

如果想避免容器启动时联网，可以提前生成：

```bash
cd ~/VibeVoice
pip install -e .[vllm]
python3 -m vllm_plugin.tools.generate_tokenizer_files \
  --output $HF_MODELS_ROOT/VibeVoice-ASR-vllm
```

## 步骤 3：获取 Docker 镜像

### 3a. 拉取预构建镜像（推荐）

```bash
docker pull hxer7963/vibevoice-asr-vllm:latest
```

镜像包含：
- vLLM v0.14.1
- ffmpeg + libsndfile1（音频解码）
- VibeVoice vllm_plugin（注册 `VibeVoiceForASRTraining` 架构）
- 优化的启动参数（USE_MEAN=1, expandable_segments 等）

`serve_vllm.sh` 默认使用这个镜像，无需额外配置即可 `serve_vllm.sh start`。

### 3b. 本地构建（可选，用于自定义修改）

如果你需要修改 Dockerfile 或不想用预构建镜像：

```bash
# 在 VibeVoice 仓库根目录构建
cd ~/VibeVoice
docker build -f Dockerfile.asr-vllm -t vibevoice-asr-vllm:latest .

# 启动时指定本地镜像名
VV_IMAGE=vibevoice-asr-vllm:latest bash ~/podcast-summary/vibevoice-asr/serve_vllm.sh start
```

构建约 5-10 分钟。

## 步骤 4：启动服务

```bash
cd ~/podcast-summary

# 启动（首次加载模型约 2-3 分钟）
bash vibevoice-asr/serve_vllm.sh start

# 查看健康状态 + GPU 显存
bash vibevoice-asr/serve_vllm.sh status

# 跟踪日志
bash vibevoice-asr/serve_vllm.sh logs

# 停止
bash vibevoice-asr/serve_vllm.sh stop
```

### 自定义配置

所有配置通过环境变量覆盖（`serve_vllm.sh` 顶部）：

```bash
# 模型路径（默认 /workspace/models/VibeVoice-ASR-vllm）
export VV_MODEL_PATH=$HOME/models/VibeVoice-ASR-vllm

# GPU 显存利用率（默认 0.85，4×4090 实测天花板，不要更高）
export VV_GPU_MEM=0.85

# Tensor parallel size（默认 4，等于 GPU 数量）
export VV_TP=4

# 端口（默认 8000）
export VV_PORT=8000

# 模型存储根目录（用于 Docker volume 挂载）
export VV_MODELS_ROOT=$HOME/models

bash vibevoice-asr/serve_vllm.sh start
```

## 步骤 5：验证

### 健康检查

```bash
curl http://localhost:8000/health
# {"status": "ok", ...}
```

### 转录一个音频文件

```bash
# 直接用客户端脚本
.venv/bin/python3 vibevoice-asr/transcribe_vllm.py \
  /path/to/audio.m4a -o /path/to/transcript.md

# 或通过 pipeline
bash scripts/transcribe.sh "audios/xiaoyuzhou/<podcast>/<episode>"
```

### HTTP API

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "vibevoice",
    "messages": [{"role": "user", "content": [
      {"type": "audio_url", "audio_url": {"url": "data:audio/mpeg;base64,<base64>"}},
      {"type": "text", "text": "This is a 60 seconds audio, please transcribe it with these keys: Start time, End time, Speaker ID, Content"}
    ]}],
    "max_tokens": 32768, "temperature": 0.0, "stream": false
  }'
```

## 性能参考

| 音频时长 | 转录耗时 | 实时倍率 | GPU 显存 |
|---|---|---|---|
| 2 min | ~20s | 6x | 8GB |
| 31 min | ~250s | 7.5x | 14GB |
| 42 min | ~245s | 10.3x | 16GB |
| 60 min | ~8 min | ~7.5x | 18GB |
| 97 min (2×60min 并发) | 2m37s | ~37x | 22GB/卡 |

**测试环境**：4× RTX 4090 (24GB)，tp=4，gpu-memory-utilization=0.85，VIBEVOICE_USE_MEAN=1

## 关键调优参数

### `--gpu-memory-utilization 0.85`（实测上限）

音频编码器的 forward pass 峰值是**固定 ~22.7GB/卡**，与块大小和并发数无关。0.85 是天花板：
- **0.85** → 编码器峰值时余 1.8GB，KV 池放大到 4×60min 仅 44% → 安全 ✅
- **0.90** → ~0.1GB 余量，危险
- **0.92** → OOM（编码器放不下）

### 60 分钟切块

客户端把长音频切成 60 分钟重叠块（30s 重叠），并发数默认 4。60min 是甜点：
- 更大块不降低峰值显存，只拉长单块运行时间
- 更小块增加切片开销，不提升吞吐

### `VIBEVOICE_USE_MEAN=1`

确定性声学编码（镜像已内置），可复现、减少采样引起的重复循环。

## 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 启动即 `EngineCore failed` | `gpu-memory-utilization` 太低 | 提到 0.85 |
| 转录中途 `CUDA out of memory` | util 太高（>0.88） | 降到 0.85 |
| `Model architecture ... not supported` | 用了 transformers 原生版模型 | 用 `VibeVoice-ASR-vllm`（见步骤 2） |
| 400 `zero-size array` | 音频用 `-c copy` 切出来解码为空 | 客户端已自动重编码为 mono 24k mp3 |
| 输出 `finish=length` + 尾部重复 | 重复循环 | 客户端已自动恢复；检查 `VIBEVOICE_USE_MEAN=1` |
| 服务起不来但 GPU 被占 | 上个容器没释放 | `serve_vllm.sh stop` 后重启；`nvidia-smi` 确认 |

## 参考

- [Microsoft VibeVoice GitHub](https://github.com/microsoft/VibeVoice)
- [VibeVoice-ASR HuggingFace](https://huggingface.co/microsoft/VibeVoice-ASR)
- [vLLM 文档](https://docs.vllm.ai/)
