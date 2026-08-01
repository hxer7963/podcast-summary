---
name: podcast-pipeline
description: 播客与视频研究流水线编排器。从公网音频、普通播客、YouTube、Bilibili 或 PodcastTranscript.ai URL 获取 transcript，再生成中文深度纪要；自动按本机能力选择零依赖云端或按需本地路径。用户说“处理这一集播客”“处理这个视频”“跑完整播客流水线”“download a podcast”，或要求从 URL 端到端生成纪要时使用。
---

# podcast-pipeline

保持薄编排：读取能力 → 取得 `episode_dir + transcript.md` → 按来源决定是否校对 → summary。不要预装当前 URL 用不到的环境。

```text
URL → podcast-asr-scheduler → episode_dir/transcript.md
                             → [可选 transcript-fix]
                             → podcast-summary
```

## 子 skill

| Skill | 独立职责 | 最小环境 |
|---|---|---|
| `podcast-asr-scheduler` | 选择最低成本转录路径 | Bash |
| `podcasttranscript-fetch` | 公共库文字稿 | Python 标准库 |
| `podcast-fetch` | 普通播客元数据/音频 | 小宇宙为标准库；其他源按需 fetch 组 |
| `subtitle-fetch` | YouTube/Bilibili 字幕 | 按需 subtitle 组 |
| `volcengine-asr` | 公网 audio URL → transcript/result JSON | curl + API key |
| `podcast-transcribe` | 本地音频 → speaker transcript | GPU + Docker + ffmpeg |
| `podcast-transcript-fix` | 事实校对 | Agent 能力 |
| `podcast-summary` | transcript → 深度纪要 | Agent 能力 |

每个子 skill 可被单独调用。已有 transcript 时直接调用 `podcast-summary`，不要强制重跑 fetch 或 ASR。

## 统一 handoff

所有阶段围绕绝对路径 `episode_dir`：

```text
episode_dir/
├── README.md
├── transcript.md
├── source/status JSON       # 某些来源可选
├── audio.*                  # 仅本地 GPU 路径需要
└── {basename}.md            # summary 输出
```

## 执行

### 1. 读取能力

```bash
bash scripts/check_capabilities.sh --json
```

不要把“可选能力未安装”当作整个 hub 失败。按 URL 和当前阶段调用：

- `bash install.sh --with-fetch`：仅 RSS / Apple / Spotify
- `bash install.sh --with-subtitle`：仅 YouTube / Bilibili
- 默认安装、火山云、summary、小宇宙元数据、PodcastTranscript：不运行 uv

### 2. 路由到 transcript

1. 已给 `episode_dir/transcript.md`：保持幂等，直接进入第 3 步。
2. PodcastTranscript URL/topic：调用 `podcasttranscript-fetch`。
3. YouTube/Bilibili：调用 `subtitle-fetch`；仅此时安装 subtitle 组。
4. 公网音频 URL + 火山 key：直接调用 `volcengine-asr`，不 fetch、不下载音频。
5. 普通播客 URL：优先官方 transcript；需要火山 ASR 时只解析公网 Audio URL，不下载音频。
6. 云路径不可用且本机确有 GPU：按 scheduler 的 Level 2 确认流程下载本地音频和约 20GB ASR 资产。
7. 无云 key 且无 GPU：停止并提示用户提供 `VOLC_ASR_API_KEY`；不要安装本地 GPU 依赖。

### 3. 来源后处理

| transcript 来源 | 默认处理 |
|---|---|
| 人工官方字幕 | 直接 summary |
| PodcastTranscript / 自动字幕 | transcript-fix → summary |
| 火山云 | 直接 summary；需要时可校对专名 |
| 本地 VibeVoice | transcript-fix → summary |

### 4. 输出纪要

调用 `podcast-summary`，输出 `{basename}.md`。本仓库到 summary 为止。

## 视频错误语义

- subtitle-fetch 退出 0：有 transcript。
- 退出 2：确实无字幕，可以转云端或本地 ASR。
- 退出 3/4/5：cookie、JS runtime 或完整性错误；先处理错误，不要误判为“无字幕”。
- 视频需要云 ASR 时，火山云必须能访问一个公网音频 URL；若平台不能提供可直接访问的音频 URL，下载/转码才可能需要 subtitle 组和 ffmpeg。

## 全局约束

- 密钥只从环境变量读取，不写进仓库或产物。
- 不把音频提交 git。
- 目录名只使用字母、数字、中文、dash、下划线和点。
- 本地 GPU 首次资产下载必须明确告知体积、磁盘和预期性能并取得确认。
- 多集本地 GPU 转录串行；云端和 summary 不继承此限制。
