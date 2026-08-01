---
name: volcengine-asr
description: 用火山引擎录音文件识别 2.0 将公网音频 URL 转成 transcript.md。最小通信层只需 curl，不需要 Python、uv、第三方包、ffmpeg、本地音频或 GPU；也提供可选 Python 自动化客户端。用户给出公网音频 URL 并要求转录、说“用火山云转录”“走火山 ASR”“云端转录”，或设置 VOLC_ASR_API_KEY 时使用。
---

# volcengine-asr

把公网音频 URL 交给火山云异步识别，再生成标准 `episode_dir/transcript.md`。完整开通步骤见 `docs/volcengine-asr-setup.md`。

## 最小路径：curl transport

只需要 Bash、curl 和 `VOLC_ASR_API_KEY`。不要安装 Python、uv、jq、httpx、requests、ffmpeg 或 GPU 依赖。

```bash
bash scripts/volc_asr.sh run \
  'https://example.com/episode.mp3' \
  'audios/cloud/episode'
```

脚本通过 stdin 向 curl 传入密钥 header，避免 key 出现在进程参数中。它会：

1. 生成 request ID；
2. 调用 submit；
3. 按响应头状态码轮询 query；
4. 写 `README.md` 和 `volc-response.json`；
5. 若系统本来已有 jq，再额外写 `transcript.md`。

没有 jq 时不要安装它。读取 `volc-response.json`，提取 `result.text`，写入 `transcript.md` 即可。AI agent 能直接完成这一步。

也可把协议拆开调用，便于组合或调试：

```bash
bash scripts/volc_asr.sh submit "$AUDIO_URL"
bash scripts/volc_asr.sh query "$REQUEST_ID"
```

## 可选 Python 便利客户端

若本机已经有 Python 3.10+，可以使用标准库客户端；不要为它安装包：

```bash
python3 scripts/volc_asr.py --audio-url "$AUDIO_URL"
python3 scripts/volc_asr.py --episode-dir "$EPISODE_DIR"
```

Python 客户端自动创建默认 episode_dir、解析 JSON 并写 `transcript.md`，但不是 hub 安装或云端通信的前提。

## 上游 handoff

火山云从公网 URL 自行拉取音频，不应先下载本地副本：

- 直接 audio URL：立即调用 curl transport。
- 小宇宙：`xiaoyuzhou_download.py --metadata-only` 写 README 后，可用 Python 客户端从 README 取 URL，或由 agent 读取该行传给 curl。
- RSS/Apple/Spotify：对应 handler 使用 `--metadata-only`；这些来源才按需安装 fetch 组。

## 契约与安全

- 密钥只从 `VOLC_ASR_API_KEY` 读取，不接受命令行 key，不写入日志或文件。
- 输入必须是火山云可访问的 HTTP(S) 音频 URL。
- 已有至少 100 字节 `transcript.md` 时保持幂等。
- 默认轮询 10 秒、超时 30 分钟；可用 `VOLC_ASR_POLL_INTERVAL` / `VOLC_ASR_MAX_WAIT` 覆盖。
- 火山响应状态：20000000 成功；20000001/20000002 处理中；20000003 静音；其他为失败。
- 成功后调用 `podcast-summary`；需要时再校对专名。
