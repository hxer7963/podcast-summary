# YouTube Cookie 与字幕抓取：单机自动配置 Runbook

本文档面向执行 subtitle-fetch 的 AI agent。范围仅限同一台机器：浏览器、Cookie 文件和字幕抓取命令都在当前机器运行，不包含 SSH、SCP 或跨机器同步。

目标是以最低迁移成本完成：

1. 自动检查字幕抓取环境。
2. 从当前机器的浏览器获得 YouTube Cookie。
3. 把 Cookie 安装到脚本可自动发现的位置。
4. 完成字幕清单、字幕下载、文稿生成和完整性验证。
5. 视频确实没有平台字幕时，明确转入 ASR，而不是误判为 Cookie 失败。

## 安全红线

- 不得在聊天、终端回显、日志、补丁或 Git 中输出 Cookie 内容。
- 只可回报文件大小、权限和 Cookie 条目数。
- Cookie 文件权限必须为 0600；临时目录和 .secrets 目录使用 0700。
- Cookie 文件只能放在仓库外，或已被 Git 忽略的 .secrets/。
- 不得代替用户输入账号密码、验证码或处理二次认证。
- 不使用来源不明的免费代理。

## 分享 skill 时必须携带的文件

仅复制 SKILL.md 不足以执行抓取。skill 包或目标项目还必须能访问：

    .agents/skills/subtitle-fetch/SKILL.md
    .agents/skills/subtitle-fetch/docs/youtube-cookie-runbook.md
    scripts/subtitle_fetch.py
    scripts/video_fetch.py
    pyproject.toml
    uv.lock

pyproject.toml 必须包含 subtitle 依赖组及 yt-dlp。若目标项目采用别的依赖管理方式，至少需要 Python 3.12+ 与当前版 yt-dlp。

## 第一步：自动检查和配置环境

在项目根目录执行：

    command -v uv
    uv sync --group subtitle
    uv run --group subtitle python -m yt_dlp --version
    node --version
    deno --version

YouTube 需要 Deno >= 2.3 或 Node >= 22，二者满足一个即可；优先 Deno。Bilibili 不需要 JavaScript runtime。

如果仓库随附 install.sh，可使用：

    bash install.sh --with-subtitle

如果没有 install.sh，必须使用 uv sync --group subtitle，不得因为文档示例缺失而停止。

最终自检：

    uv run --group subtitle python scripts/subtitle_fetch.py --help

## 第二步：先尝试当前浏览器登录态

macOS Chrome：

    uv run --group subtitle python scripts/subtitle_fetch.py \
      "https://youtube.com/watch?v=VIDEO_ID" \
      --cookies-from-browser chrome \
      --list-only

macOS 可能弹出钥匙串访问提示，需要用户允许。若 Chrome Cookie 数据库被锁，完全退出 Chrome 后重试。

此方式适合快速诊断，但不适合作为长期持久 Cookie 的唯一来源：日常浏览器继续运行时，YouTube 可能轮换 Cookie。若出现 cookies are no longer valid，改走下一节的独立无痕导出流程。

## 第三步：稳定获取 Cookie 文件

推荐流程：

1. 打开独立无痕窗口。
2. 在该窗口登录 YouTube；账号密码和验证码由用户自行输入。
3. 登录后在同一标签打开 https://www.youtube.com/robots.txt。
4. 打开 Cookie-Editor，选择 Export All，再选择 Netscape 格式；不要只导出当前两条匿名 Cookie。
5. 导出完成后不要继续在该无痕会话浏览。关闭无痕会话，避免 YouTube 轮换刚导出的 Cookie。

不要从仍在日常使用的普通 Chrome 会话导出持久 Cookie。

### Netscape 格式：安装到标准目录

    cookie_config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/podcast-pipeline/cookies"
    mkdir -p "$cookie_config_dir"
    chmod 700 "$cookie_config_dir"
    install -m 600 "/absolute/path/to/exported-youtube.txt" \
      "$cookie_config_dir/youtube.txt"

标准路径只能存放 Netscape 格式，不能把 Cookie-Editor JSON 改名为 youtube.txt。

### Cookie-Editor 只有 JSON：使用受保护的显式文件

    mkdir -p .secrets
    chmod 700 .secrets
    install -m 600 "/absolute/path/to/exported-youtube.json" \
      .secrets/youtube-cookies.json
    export YOUTUBE_COOKIES_FILE="$PWD/.secrets/youtube-cookies.json"

subtitle_fetch.py 会把显式 JSON 转换成 0600 的临时 Netscape 文件，并在命令结束时删除临时文件。

确认 .gitignore 包含：

    .secrets/
    cookies*.json
    cookies*.txt

## 第四步：只验证元数据，不泄露 Cookie

不得 cat、head、tail 或打印 Cookie 文件。

对标准 Netscape 文件，只验证：

    stat -f 'mode=%Lp size=%z' "$cookie_config_dir/youtube.txt" 2>/dev/null \
      || stat -c 'mode=%a size=%s' "$cookie_config_dir/youtube.txt"

脚本还会自动验证：

- 是普通文件，不是符号链接。
- 权限不宽于 0600。
- 文件头是 Netscape Cookie 格式。
- 解析后至少有一条 Cookie。

如果报 Cookies file contains no cookies，表示文件只有文件头或完全为空；这不是 Cookie 过期。

如果导出文件只有 2 条 Cookie，并且 yt-dlp 仍要求登录，通常表示导出的只是匿名 Cookie。确认无痕窗口确实已登录，然后在同一 Tab 打开 robots.txt，使用 Cookie-Editor 的 Export All 重新导出。条目数本身不是最终成功标准，必须通过下一节的实际字幕下载验收。

## 第五步：字幕清单和完整下载自检

先列字幕：

    uv run --group subtitle python scripts/subtitle_fetch.py \
      "https://youtube.com/watch?v=VIDEO_ID" --list-only

再完整抓取：

    uv run --group subtitle python scripts/subtitle_fetch.py \
      "https://youtube.com/watch?v=VIDEO_ID"

只有满足以下条件，才能宣布字幕链路打通：

- 命令退出码为 0。
- stdout 出现 ✓ Episode complete。
- episode_dir 中存在非空 transcript.md。
- subtitle_status.json 的 result 为 complete。
- 字幕覆盖率、首尾缺口和正文长度通过完整性检查。

list-only 退出码为 0 只代表成功访问字幕清单，不代表该视频一定有字幕。若输出同时包含 has no subtitles 与 has no automatic captions，说明视频本身没有平台字幕。

## 第六步：退出码决策

| 代码 | 判定 | 下一步 |
|---|---|---|
| 0 | 文稿完整 | AI/自动字幕先 transcript-fix，再 summary |
| 2 | 视频无平台字幕 | 下载音轨并转 GPU ASR |
| 3 | Cookie、登录或文件格式失败 | 重新导出、安装和验证 Cookie |
| 4 | JavaScript runtime/challenge 失败 | 升级 yt-dlp 与 Deno/Node |
| 5 | 字幕疑似不完整 | 人工核验或转 ASR |

无字幕时：

    uv run --group subtitle python scripts/video_fetch.py \
      --handoff "/absolute/path/to/asr-required.json"

    bash scripts/transcribe.sh "/absolute/path/to/episode_dir"

GPU ASR 生成的 transcript.md 还应使用 podcast-transcript-fix 校对专名、数字和切片重复。

## Cookie 更新条件

仅在下列情况重新导出：

- cookies are no longer valid。
- Sign in to confirm you're not a bot，并且确认脚本已实际加载 Cookie。
- Cookie 文件为空、格式错误或权限错误。
- 用户主动退出 YouTube、修改密码或撤销会话。
- YouTube 轮换了浏览器会话。

更新时重复"独立无痕窗口 → 登录 → robots.txt → Cookie-Editor 导出 → 立即关闭无痕会话"。

## 故障对照表

| 现象 | 判定 | 处理 |
|---|---|---|
| Cookies file contains no cookies | 文件为空，不是过期 | 重新导出并安装 |
| Sign in to confirm you're not a bot | 匿名访问被拦截或 Cookie 未生效 | 检查加载路径，必要时重新导出 |
| cookies are no longer valid | Cookie 已过期或被轮换 | 独立无痕会话重新导出 |
| Chrome database locked | Chrome 占用 Cookie 数据库 | 完全退出 Chrome 后重试 |
| macOS Keychain denied | 无法解密 Chrome Cookie | 用户批准钥匙串权限，或用 Cookie-Editor |
| No supported JavaScript runtime | Deno/Node 缺失或版本过低 | 安装 Deno >= 2.3 或 Node >= 22 |
| challenge solving failed | yt-dlp/runtime 无法解 challenge | 升级 yt-dlp 与 JS runtime |
| has no subtitles | 视频本身无字幕 | 下载音轨并走 ASR |

## 迁移验收清单

AI agent 在新机器上必须依次确认：

- Python、uv、yt-dlp 和 JS runtime 就绪。
- Cookie 来源是当前机器的独立无痕会话。
- Cookie 文件位于标准目录或受保护的 .secrets/。
- 文件权限为 0600，且未被 Git 跟踪。
- 使用一个确认带字幕的视频完成完整下载，而不只是 list-only。
- 再处理用户目标视频；目标视频无字幕时走 ASR。

## 验证记录

以下记录证明本 runbook 的 Cookie 导出与字幕下载流程已在真实 Mac 上端到端验证通过：

- 验收视频 ID：arj7oStGLkU（YouTube，公开视频）。
- 字幕语言：en，类型：人工字幕。
- 第一次导出仅 2 条匿名 Cookie，yt-dlp 仍要求登录，验收失败，未安装。
- 用户确认无痕 YouTube 已登录，在同一 Tab 打开 robots.txt，并使用 Cookie-Editor 的 Export All → Netscape 重新导出。
- 第二次导出为 15 条记录、2,241 字节、权限 0600；Netscape 格式有效。
- list-subs 退出码：0；字幕下载退出码：0。
- 字幕文件：22,473 字节、1,084 行；生成的纯文本文稿：12,672 字节、450 行。
- Cookie 已安装到 ~/.config/podcast-pipeline/cookies/youtube.txt，权限 0600。
- 临时导出和验收文件已删除；无痕会话已关闭。

结论：在同一台 Mac 上，按本 runbook 导出的 Cookie 已通过真实字幕下载验证，可供 subtitle-fetch 使用。
