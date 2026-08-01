---
name: podcast-transcribe
description: 用 vibevoice-asr 转录播客音频，输出带 speaker 标签的 transcript.md。处理 >100 分钟的长播客（自动 ffmpeg 切片 + 合并）。当用户说"转录播客"、"转录这一集"、"做 ASR"、"transcribe podcast"、或拿到一个含音频的 episode_dir 要求出文字稿时使用。
---

# podcast-transcribe

播客转录阶段。把音频转成带说话人标签的 `transcript.md`。

## 输入 → 输出

- **输入**：一个或多个 `episode_dir`，每个目录已含 `*.m4a` 或 `*.mp3`（通常由 [`podcast-fetch`](../podcast-fetch/SKILL.md) 产出）
- **输出**：`<episode_dir>/transcript.md`
- **stdout 约定**：每个成功打印 `TRANSCRIPT=<path>`

## 调用方式

```bash
bash scripts/transcribe.sh <episode_dir> [<episode_dir> ...]
```

## 内部流水线（脚本已封装）

1. 在 `episode_dir` 中定位音频（`*.m4a` 优先，其次 `*.mp3`）
2. `ffprobe` 探测时长
3. 默认走 **vLLM 后端**（推荐）：`transcribe_vllm.py` 把音频切成 60 分钟重叠块（30s 重叠），并发提交给本地 vLLM 服务，自动恢复重复循环
4. 可选 **PyTorch 后端**（legacy）：`transcribe.py --dp 4` 一次跑完或切片合并
5. 校验 `transcript.md` ≥ 100 字节，否则报错退出 1

**幂等**：如果 `transcript.md` 已存在且非空，跳过不重复转录。

## vLLM 后端（推荐）

vLLM 后端通过 `serve_vllm.sh` 启动一个 OpenAI 兼容的 HTTP 服务，客户端 `transcribe_vllm.py` 与之通信。优点：

- 服务常驻，无需每次重新加载模型（~6s 启动 vs 每次 6s 加载）
- 长音频并发分块，实测 ~3x 吞吐（tp=4 单副本连续批处理）
- 自动重复循环检测 + 升温重试恢复

部署见 `docs/vibevoice-local-setup.md`。启动服务：

```bash
bash vibevoice-asr/serve_vllm.sh start     # 加载约 2-3 分钟，等到 health=200
bash vibevoice-asr/serve_vllm.sh status    # 看健康 + 显存
bash vibevoice-asr/serve_vllm.sh logs      # 跟日志
bash vibevoice-asr/serve_vllm.sh stop      # 停止
```

`transcribe.sh` 默认会自动拉起服务（如果未运行）。

## 关键约束

1. **只用 vibevoice-asr**（无其他 ASR fallback）。火山云路径走 `volcengine-asr` skill，是独立的云端选项。

2. **GPU 串行**：`--dp 4` 占用全部 4 块 GPU，所以**多个 episode_dir 串行处理**，不要并行

3. **环境隔离**：默认 vLLM 后端运行在专用 Docker 容器，不把 CUDA/Torch 依赖装进 hub 的可选 fetch/subtitle 环境。legacy PyTorch 后端才读取 `$PODCAST_SUMMARY_VENV` / `$PODCAST_SUMMARY_VENV_PY`。

4. **音频路径不能含特殊 shell 字符**（`xiaoyuzhou_download.py` 的 sanitize 已保证）

5. **后端选择**：`VV_BACKEND=vllm`（默认）或 `VV_BACKEND=pytorch`（legacy）。除非显式回退，否则用 vllm。

## 常见用法

### 单集
```bash
bash scripts/transcribe.sh "audios/xiaoyuzhou/硅谷101/E230-1万亿收入/"
```

### 多集（串行）
```bash
bash scripts/transcribe.sh \
  "audios/xiaoyuzhou/硅谷101/E230-..." \
  "audios/xiaoyuzhou/硅谷101/E229-..."
```

### 跳过已转录的，只补未完成的
脚本内置幂等检测：直接对所有 `episode_dir` 喂一遍即可，已有 `transcript.md` 的会被跳过。

```bash
find audios/xiaoyuzhou -mindepth 2 -maxdepth 2 -type d -print0 \
  | xargs -0 bash scripts/transcribe.sh
```

### 热词提升专名识别
```bash
VV_HOTWORDS="Temu,拼多多,黄峥" bash scripts/transcribe.sh <episode_dir>
```

## transcript.md 格式（vibevoice 输出）

```markdown
# audio

> VibeVoice ASR (vLLM) | 2 speakers | 44 segments | 10.0min audio | 34.8s inference

---

**Speaker 0:** 欢迎收听，咱们今天来聊个……
**Speaker 1:** 确实是这样……
```

## 故障排查

| 现象 | 可能原因 | 处理 |
|---|---|---|
| `No audio file found in: <dir>` | fetch 阶段没产出音频 | 检查 fetch 输出，重跑 `podcast-fetch` |
| `Transcript file is suspiciously small` | 音频损坏或 ASR 静默失败 | 直接听音频；查看 `transcribe.sh` 日志 |
| GPU OOM / CUDA 错误 | 上一次任务没正常释放显存 | `nvidia-smi` 看显存，必要时重启转录服务 |
| vLLM 服务起不来 | 镜像未构建或模型路径错误 | 见 `docs/vibevoice-local-setup.md` |
| 切片合并出现重复语句 | 30s 重叠区被两次转录 | 这是已知现象，下游 `podcast-transcript-fix` 会顺手清理 |

## 下一步

转录完调用 [`podcast-transcript-fix`](../podcast-transcript-fix/SKILL.md) 校验 ASR 错误，然后再走 [`podcast-summary`](../podcast-summary/SKILL.md)。

完整流水线参考 [`podcast-pipeline`](../podcast-pipeline/SKILL.md)。
