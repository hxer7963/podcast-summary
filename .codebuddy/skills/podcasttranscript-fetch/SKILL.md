---
name: podcasttranscript-fetch
description: 搜索并下载 PodcastTranscript.ai 公共播客库的完整文字实录，支持单集 library URL、稳定 ID、关键词/topic、分类、语言和排序筛选，产出可直接进入 podcast-pipeline 的 episode_dir。当用户说"从 PodcastTranscript 搜索 topic""下载这个 podcasttranscript.ai 文字稿""批量拉取某主题的播客实录"或提供 podcasttranscript.ai/library URL 时使用。
---

# podcasttranscript-fetch

从 PodcastTranscript.ai 的公开只读 REST API 搜索并下载完整文字稿。只负责发现、下载和落盘，不生成中文纪要或推送。

## 输出契约

每集输出一个 `episode_dir`：

```text
audios/transcripts/podcasttranscript/<category>/<title>/
├── README.md
├── transcript.md
├── source.json
└── transcript.json        # 仅 --save-json
```

成功时 stdout 必须包含：

```text
✓ Episode complete: <absolute_episode_dir>
```

从该行提取 `episode_dir`，随后进入 `podcast-transcript-fix → podcast-summary`。

## 执行入口

使用仓库根目录脚本：

```bash
python3 scripts/podcasttranscript_fetch.py <URL-or-query>
```

### 单集 URL

```bash
python3 scripts/podcasttranscript_fetch.py \
  'https://podcasttranscript.ai/library/<slug>'
```

脚本自动解析 slug、查找稳定 ID，并处理平台目录中的重复记录，只保存一个 canonical 副本。

### 按 topic / 关键词搜索

```bash
python3 scripts/podcasttranscript_fetch.py --topic 'artificial intelligence' --limit 10
```

可叠加筛选：

```bash
python3 scripts/podcasttranscript_fetch.py \
  --topic 'semiconductor' \
  --category technology \
  --language English \
  --sort newest \
  --limit 20
```

先预览、不下载：

```bash
python3 scripts/podcasttranscript_fetch.py --topic 'AI agents' --limit 20 --list-only
```

按稳定 ID：

```bash
python3 scripts/podcasttranscript_fetch.py --id <transcription-id>
```

增量刷新与原始数据：

```bash
--refresh          # 覆盖已有文字稿
--save-json        # 保存 API 原始记录
--with-timestamps  # API 有分段时间戳时写入 transcript.md
--output-dir DIR   # 覆盖默认输出根目录
```

## 数据源约束

- 使用 `https://backend.podcasttranscript.ai/api/v1`。
- 只调用公开读取接口；读取无需 API key，受每 IP 每分钟 60 次限制。
- 不调用创建转录接口，不消耗付费额度。
- 同时兼容旧记录的完整 `transcript` 字符串和带时间戳的分段数组。
- `transcript.md` 已存在时默认跳过；用 `--refresh` 强制刷新。
- 目录名只允许字母、数字、中文、dash、下划线和点。
- 不下载音频、不运行 GPU ASR；平台已提供文字稿。

## 后续流水线

文字稿来自 AI 转录，默认先调用 `podcast-transcript-fix` 做专名与术语校验，再严格按 `podcast-pipeline` 运行 summary。每个阶段保持其原有幂等与查重规则。
