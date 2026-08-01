---
name: podcast-asr-scheduler
description: 播客/视频转录调度器。按官方文稿/字幕 → 火山云 URL ASR → 本地 GPU ASR 的顺序，以最低安装成本拿到 transcript.md。用于 podcast-pipeline 选择路径，或用户说“调度转录”“选 ASR 路径”“自动选转录方式”时。只负责编排和降级，不安装未被当前路径使用的依赖。
---

# podcast-asr-scheduler

给定内容 URL，先读取本机能力，再选择成本最低的路径。缺少某个可选能力不等于安装失败。

## 第一步：读取能力

```bash
bash scripts/check_capabilities.sh --json
```

只在当前 URL 确实需要时安装可选组：

- RSS / Apple / Spotify：`bash install.sh --with-fetch`
- YouTube / Bilibili 字幕：`bash install.sh --with-subtitle`
- 本地 GPU ASR：不得由安装器预装；按下方 Level 2 流程执行

核心与 summary 不需要本地 runtime 包；火山云通信只需 curl。PodcastTranscript 和小宇宙元数据助手需要 Python 3.10+ 标准库，但不需要 uv、ffmpeg 或第三方包。

## 决策顺序

### Level 0：复用文字稿

1. `podcasttranscript.ai/library/`：调用 `podcasttranscript-fetch`。
2. YouTube / Bilibili：仅当用户给出视频 URL 时安装 subtitle 组并调用 `subtitle-fetch`。
3. RSS / Apple / Spotify：仅当用户给出这些 URL 时安装 fetch 组；先探测官方 transcript。
4. 小宇宙：用标准库脚本解析元数据和 Audio URL；不下载音频：

```bash
python3 scripts/xiaoyuzhou_download.py "$URL" --metadata-only --output-dir audios/xiaoyuzhou
```

任一路径得到 `transcript.md` 后立即停止调度并进入 summary；AI/自动字幕可先做 transcript-fix。

### Level 1：火山云 URL ASR

条件：`VOLC_ASR_API_KEY` 已设置。火山云从公网 URL 拉音频，因此不要先把音频下载到本机，也不要求 ffmpeg。

- 用户直接给公网音频 URL：

```bash
bash scripts/volc_asr.sh run "$URL" "$EPISODE_DIR"
```

- 小宇宙：先运行上面的 `--metadata-only`，再从其输出取 `episode_dir`：

```bash
python3 scripts/volc_asr.py --episode-dir "$EPISODE_DIR"
```

- RSS / Apple / Spotify：用相应 fetch handler 的 `--metadata-only` 写 README/官方 transcript，不下载音频；若仍无 transcript，再运行 `volc_asr.py --episode-dir`。

退出码：`0` 成功；`1` 配置/参数错误；`2` API/网络失败；`3` 轮询超时。Level 1 失败后只有在本机确有 GPU 时才考虑 Level 2。

### Level 2：本地 GPU ASR

仅当以下条件同时成立时进入：

1. Level 0 未命中；
2. 火山云未配置或失败；
3. `nvidia-smi` 可用；
4. 用户同意下载本地音频和缺失的 ASR 资产。

无 GPU 时停止并提示配置 `VOLC_ASR_API_KEY`；不要安装 ffmpeg、Docker、CUDA 包或模型。

## Level 2 懒加载

先检查：

```bash
nvidia-smi >/dev/null 2>&1
docker image inspect hxer7963/vibevoice-asr-vllm:latest >/dev/null 2>&1
MODEL_DIR="${VV_MODEL_PATH:-/workspace/models/VibeVoice-ASR-vllm}"
test -f "$MODEL_DIR/config.json"
```

若镜像或模型缺失，告知用户：

- Docker 镜像约 5GB；模型约 15GB；合计约 20GB；
- 4× RTX 4090 实测约 7–10× 实时；
- 当前可用磁盘空间；
- 这是一次性下载。

取得明确确认后才执行：

```bash
docker pull hxer7963/vibevoice-asr-vllm:latest
bash setup/download_vibevoice_model.sh
bash vibevoice-asr/serve_vllm.sh start
```

随后再下载音频并运行 `podcast-transcribe`。本地路径需要 ffmpeg；该要求不得上移到默认安装或云端路径。

## 降级与错误规则

- 按 Level 0 → 1 → 2 顺序，不为“可能以后会用”而安装依赖。
- `subtitle-fetch` 的 2 表示无字幕，可降级；3/4/5 是鉴权、runtime 或完整性错误，不自动误判为无字幕。
- 每一级先检查 `episode_dir/transcript.md` 是否存在且至少 100 字节；命中即保持幂等。
- 火山云输出可直接 summary；官方/AI transcript 和本地 VibeVoice 是否先 fix，按来源质量决定。
- 任何密钥只从环境变量读取，不写入仓库、README 或命令历史。

## 成本矩阵

| 路径 | 本地下载音频 | Python 包 | ffmpeg | GPU |
|---|---:|---:|---:|---:|
| PodcastTranscript | 否 | 0 | 否 | 否 |
| 小宇宙元数据 → 火山云 | 否 | 0 | 否 | 否 |
| 直接 audio URL → 火山云 | 否 | 0（curl） | 否 | 否 |
| RSS/Apple/Spotify → 火山云 | 否 | fetch 组 | 否 | 否 |
| YouTube/Bilibili 字幕 | 否 | subtitle 组 | 否 | 否 |
| 本地 VibeVoice | 是 | Docker/本地专用 | 是 | 是 |
