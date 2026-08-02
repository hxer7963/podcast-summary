---
name: subtitle-fetch
description: 自动识别并拉取 YouTube / Bilibili 视频字幕，生成可直接进入研究流水线的 episode_dir（README.md、transcript.md、subtitle_status.json）。支持 macOS Chrome 登录态、Linux 标准 cookie 目录、字幕完整性检查与无字幕时的 GPU ASR 交接。当用户说"拉取字幕""获取视频文字稿""处理这个视频""跑完整视频流水线""fetch subtitles"，或 podcast-pipeline 收到 YouTube/Bilibili URL 时使用。
---

# subtitle-fetch

优先使用平台字幕生成 `transcript.md`，不下载视频或音频。仅当平台没有可用字幕时生成 `asr-required.json`，由 Linux GPU 主机继续音频转录。

## 环境

只有收到 YouTube/Bilibili URL 时才安装本组。不要安装 `requirements-asr.txt` 或 ffmpeg；纯字幕拉取不需要它们：

```bash
export PATH="$HOME/.local/bin:$PATH"
bash install.sh --with-subtitle
```

YouTube 还要求 Deno >= 2.3（推荐）或 Node >= 22。脚本按顺序发现 PATH 中的 Deno、`~/.deno/bin/deno`、Node；也可显式指定：

```bash
uv run --group subtitle python scripts/subtitle_fetch.py URL \
  --js-runtime deno --js-runtime-path /path/to/deno
```

Bilibili 不要求 JavaScript runtime。

## 输入与输出

```text
URL
└── audios/subtitles/<platform>/<channel>/<title>/
    ├── README.md
    ├── <video>.<language>.srt|vtt|json3
    ├── transcript.md
    └── subtitle_status.json
```

无字幕时同目录额外生成 `asr-required.json`，且不生成空的 `transcript.md`。

成功 stdout：

```text
✓ Episode complete: <episode_dir>
```

需 ASR stdout：

```text
⚠ ASR required: <episode_dir>
```

## Cookie 策略

同机浏览器 Cookie 获取、Cookie-Editor 后备流程、环境初始化与故障分类见 [`docs/youtube-cookie-runbook.md`](docs/youtube-cookie-runbook.md)。遇到 YouTube 认证问题时必须按该文档执行，不得输出 Cookie 内容。

Linux 可设置 `YOUTUBE_COOKIES_FILE` 或 `BILIBILI_COOKIES_FILE`。Cookie 文件必须放在仓库外或 `.secrets/`，禁止提交 Git。Cookie-Editor JSON 会转换成权限 `0600` 的临时 Netscape 文件，并在命令结束时删除。

无需参数时自动检查标准目录：

```text
$XDG_CONFIG_HOME/podcast-pipeline/cookies/youtube.txt
$XDG_CONFIG_HOME/podcast-pipeline/cookies/bilibili.txt
~/.config/podcast-pipeline/cookies/youtube.txt
~/.config/podcast-pipeline/cookies/bilibili.txt
```

优先级固定为：显式 `--cookies` → 显式 `--cookies-from-browser` → 平台环境变量 → XDG 标准目录 → `~/.config` 标准目录 → macOS Chrome → Linux 匿名访问。自动发现的持久文件必须是非符号链接的 Netscape 普通文件，权限不得宽于 `0600`；存在但不合规时退出 3，不得静默降级。

YouTube 持久 cookie 应从独立无痕窗口导出：登录后在同一标签打开 `https://www.youtube.com/robots.txt`，导出后立即关闭该无痕会话，避免 cookie 被浏览器轮换。

## 常用命令

### podcast-pipeline 自动调用契约

收到 YouTube/Bilibili URL 后必须直接执行，不要求用户先手工运行：

```bash
uv run --group subtitle python scripts/subtitle_fetch.py "$URL"
```

执行时将 `$URL` 替换为用户提供的完整 URL。脚本内部识别平台并自动选择对应 cookie。必须读取退出码和 stdout 中的 `episode_dir`，再按下方退出码表继续流水线。

```bash
# macOS：默认使用 Chrome 登录态
uv run --group subtitle python scripts/subtitle_fetch.py \
  "https://www.youtube.com/watch?v=VIDEO_ID"

# Bilibili：优先中文及 ai-zh
uv run --group subtitle python scripts/subtitle_fetch.py \
  "https://www.bilibili.com/video/BV_ID"

# Linux：默认自动读取标准 cookie 目录
uv run --group subtitle python scripts/subtitle_fetch.py URL \
  --output-dir audios/subtitles

# 需要覆盖默认路径时仍可显式指定
uv run --group subtitle python scripts/subtitle_fetch.py URL \
  --cookies /secure/path/cookies.txt

# 只列字幕
uv run --group subtitle python scripts/subtitle_fetch.py URL --list-only

# 允许覆盖不完整保护
uv run --group subtitle python scripts/subtitle_fetch.py URL --allow-partial
```

禁止使用来源不明的免费代理。只有用户明确提供时才传 `--proxy`。

## 字幕选择与完整性

- 默认语言顺序：`zh-Hans,zh-Hant,zh,ai-zh,en`。
- 同语言优先人工字幕，再选平台 AI/自动字幕。
- 只折叠相邻且完全相同的字幕；保留稍后再次出现的真实重复句。
- 完整字幕必须同时满足：正文不少于 100 字、时间跨度不少于视频 90%、首尾缺口分别不超过 `max(60秒, 视频时长5%)`。
- 不完整字幕退出 5，不得进入 summary；只有显式 `--allow-partial` 才可继续。

## 退出码

| 代码 | 含义 | 下一步 |
|---|---|---|
| 0 | transcript 完整 | AI/自动字幕先 transcript-fix，再 summary |
| 2 | 无字幕 | macOS 生成交接后停止；Linux pipeline 自动 video-fetch + GPU ASR |
| 3 | 登录/cookie 失败 | 更新 Chrome 登录态或外部 cookie |
| 4 | JS runtime/challenge 失败 | 安装或升级 Deno/Node |
| 5 | 字幕疑似不完整 | 人工核验；必要时改走 GPU ASR |

## Linux GPU 接力

```bash
uv run --group subtitle python scripts/video_fetch.py \
  --handoff /path/to/episode/asr-required.json
```

命令在同一 episode_dir 下载 `audio-<video-id>.m4a`。随后严格按 `podcast-transcribe` → `podcast-transcript-fix` → `podcast-summary` 执行。
