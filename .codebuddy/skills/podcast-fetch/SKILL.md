---
name: podcast-fetch
description: 根据 URL 自动路由并解析播客元数据、官方 transcript 或音频 URL；仅本地 ASR 路径才下载音频。支持小宇宙、RSS、Apple Podcasts、Spotify 及新增源。当用户说“下载播客”“拉取这一集”“只解析音频 URL”“fetch episode”，或 pipeline 需要普通播客 handoff 时使用。
---

# podcast-fetch

播客拉取阶段。负责 shownotes、官方 transcript 和可选音频下载，不做 ASR。云端路径使用 `--metadata-only`，避免把火山云会自行拉取的音频再下载一遍。

## 输入 → 输出

- **输入**：一个或多个 URL
- **输出**：每个 URL 对应一个 `episode_dir`，目录里至少含
  - `README.md` — shownotes / 节目介绍
  - `transcript.md`（官方文稿命中时），或 README 中的 `> Audio URL:`
  - `*.m4a` / `*.mp3` 仅本地 ASR 路径需要
- **stdout 约定**：每个成功的 episode 打印一行 `✓ Episode complete: <ep_dir>`

## 路由表（核心扩展点）

路由前先识别视频 URL：YouTube/Bilibili 不得直接进入本 skill。`podcast-pipeline` 必须先自动调用 `subtitle-fetch`；只有退出码 2 且存在 `asr-required.json` 时，才执行本 skill 的视频回退。

根据 URL 模式调用对应脚本。**新增播客源 = 这张表加一行 + 写一个新的 download 脚本，其他 skill 完全不动。**

| URL 模式 | Handler | 输出基目录 |
|---|---|---|
| `xiaoyuzhoufm.com/episode/<eid>` | 云端：`python3 scripts/xiaoyuzhou_download.py <url> --output-dir audios/xiaoyuzhou --metadata-only`；本地：去掉 `--metadata-only` | `audios/xiaoyuzhou/...` |
| `xiaoyuzhoufm.com/podcast/<pid>` | 同上（自动展开整档播客） | `audios/xiaoyuzhou/...` |
| RSS feed URL（任意域名） | `python3 scripts/rss_fetch.py <feed_url> --latest --podcast-name <slug> --output-dir audios` | `audios/<slug>/...` |
| `podcasts.apple.com/.../id<digits>` | `python3 scripts/apple_podcast_to_rss.py <apple_url> --latest --podcast-name <slug> --output-dir audios`（自动 iTunes Lookup → 委托 rss_fetch） | `audios/<slug>/...` |
| `open.spotify.com/{episode,show,playlist}/<id>` | `python3 scripts/spotify_fetch.py <spotify_url> --podcast-name <slug> --output-dir audios`（scrape embed 拿 show + episode 名 → iTunes Search → 委托 rss_download；非 exclusive 的 Spotify 播客全部走这条路） | `audios/<slug>/...` |
| `youtube.com/watch?v=<id>` / `youtu.be/<id>` | **仅字幕失败后的 ASR 回退**：`uv run --group subtitle python scripts/video_fetch.py --handoff <episode_dir>/asr-required.json --no-transcribe` | 复用字幕 `episode_dir` |
| `bilibili.com/video/BV<id>` / `b23.tv/<id>` | **仅字幕失败后的 ASR 回退**：同上；Linux 自动读取标准 cookie 目录 | 复用字幕 `episode_dir` |
| `music.amazon.com/*` / Spotify-exclusive | **不支持**（true walled garden / DRM）。Spotify 上的非 exclusive 播客走上面那行 | — |
| _新增源_ | _新增 `scripts/<source>_fetch.py`，加一行路由_ | `audios/<source>/...` |

> 所有 download 脚本统一约定：成功时 stdout 打印 `✓ Episode complete: <ep_dir>`。提取 `ep_dir` 的方法：`grep '✓ Episode complete:' | sed 's/.*✓ Episode complete: //'`

## 关键约束

1. **按来源选择环境**：小宇宙 handler 是 Python 标准库；RSS/Apple/Spotify 才需要 `bash install.sh --with-fetch`；视频回退需要 subtitle 组。不得在 Intel Mac 或无 GPU 主机安装 CUDA 依赖
2. **目录命名规则**（`sanitize_filename()` 已处理，新源脚本必须遵守）：
   - 只用 dash 连词，禁止特殊字符：`[](){}&,;!@#'~·…—–` 等
   - 目录结构：`<output-dir>/<podcast_name>/<short_title>/`
   - `<short_title>` 默认上限 80 字符；保留 `EP484` 等集数，去掉父目录里已有的播客名，英文词之间保留 dash
3. **云端始终 metadata-only**：有 `VOLC_ASR_API_KEY` 时只写 README/官方 transcript，不下载音频；转录交给 `volcengine-asr`
4. **不要 stage 音频到 git**：`*.m4a` / `*.mp3` 应在 `.gitignore` 中
5. **视频必须字幕优先**：YouTube/Bilibili URL 在 `podcast-pipeline` 中先调用 `subtitle-fetch`；只有收到退出码 2 / `asr-required.json` 后，Linux GPU 主机才调用本 skill 下载音频
6. **禁止内置公共代理**：只接受用户显式提供的 `--proxy`；不得使用来源不明的免费代理列表

## 常见用法

### 单集小宇宙
```bash
python3 scripts/xiaoyuzhou_download.py \
  https://www.xiaoyuzhoufm.com/episode/<eid> \
  --output-dir audios/xiaoyuzhou \
  --no-transcribe
```

### 多集小宇宙（串行）
```bash
for url in <url1> <url2> <url3>; do
  python3 scripts/xiaoyuzhou_download.py "$url" \
    --output-dir audios/xiaoyuzhou --no-transcribe
done
```

### 整档小宇宙播客
```bash
python3 scripts/xiaoyuzhou_download.py \
  https://www.xiaoyuzhoufm.com/podcast/<pid> \
  --output-dir audios/xiaoyuzhou --no-transcribe
```

### RSS（海外播客主流路径）
```bash
# 最新一集（推荐：用稳定的 slug 作为目录名）
python3 scripts/rss_fetch.py https://feeds.transistor.fm/acquired \
  --latest --podcast-name acquired --output-dir audios

# 列出所有集（不下载）
python3 scripts/rss_fetch.py https://feeds.transistor.fm/acquired --list-only

# 指定某一集 GUID
python3 scripts/rss_fetch.py https://feeds.transistor.fm/acquired \
  --episode-id <guid> --podcast-name acquired --output-dir audios
```

### Apple Podcasts URL（自动转 RSS）
```bash
python3 scripts/apple_podcast_to_rss.py \
  https://podcasts.apple.com/us/podcast/acquired/id1050462261 \
  --latest --podcast-name acquired --output-dir audios

# 仅解析、不下载（拿 RSS feed URL）
python3 scripts/apple_podcast_to_rss.py <apple_url> --resolve-only
```

### Spotify URL（自动 scrape embed → iTunes Search → RSS）
```bash
# 单集
python3 scripts/spotify_fetch.py \
  https://open.spotify.com/episode/25kYdPcO6zAPtVq8AQz2Rp \
  --podcast-name capital-allocators --output-dir audios

# Show 走 RSS 选择器
python3 scripts/spotify_fetch.py \
  https://open.spotify.com/show/3q6PrjHVfRzpD2lN1g2XRU \
  --latest --podcast-name capital-allocators --output-dir audios
```

> **原理**：Spotify embed (`open.spotify.com/embed/<kind>/<id>`) 返回的 `__NEXT_DATA__` 含 `entity.subtitle`（show 名）和 `entity.name`（集名），免登录直接 scrape。拿到 show 名后查 iTunes Search → feedUrl，再用 `rss_download` 按集名 fuzzy-match。整套机制对 Spotify 上 99% 的非 exclusive 播客都通。

### 视频无字幕后的 Linux GPU 回退

```bash
uv run --group subtitle python scripts/video_fetch.py \
  --handoff audios/subtitles/<platform>/<channel>/<title>/asr-required.json \
  --no-transcribe
```

`--handoff` 会读取原 URL，并把音频写回交接文件所在的 episode_dir。随后调用 `podcast-transcribe`；不要在 Intel Mac 上执行 GPU ASR。

## 添加一个新源（操作清单）

1. 写 `scripts/<source>_fetch.py`，要求：
   - 接受 URL 作为位置参数
   - `--output-dir <dir>` 指定基目录
   - `--no-transcribe` 选项（即使脚本本身从不转录也接受这个 flag，保持一致）
   - 成功时 stdout 打印 `✓ Episode complete: <ep_dir>`
   - 输出目录形如 `<output-dir>/<podcast_name>/<short_title>/`，里面有 `README.md` 和音频
2. 在本文件「路由表」中加一行
3. **不需要改任何其他 skill** — `podcast-transcribe` 之后的所有阶段只看 `episode_dir`，不在乎来源

## 下一步

- 有火山 key：对 metadata-only 的 `episode_dir` 调用 `volcengine-asr`。
- 只有本地 GPU：下载音频后调用 `podcast-transcribe`。
- 已命中官方 transcript：直接进入后处理/summary。

完整流水线参考 [`podcast-pipeline`](../podcast-pipeline/SKILL.md)。
