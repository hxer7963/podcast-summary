#!/usr/bin/env python3
"""volc_asr.py — 调用火山引擎大模型 ASR，把音频 URL 转成结构化 transcript.md。

火山引擎录音文件识别 2.0 (大模型版本) 异步转录:
- API: https://openspeech.bytedance.com/api/v3/auc/bigmodel/{submit,query}
- 鉴权: 环境变量 VOLC_ASR_API_KEY (HTTP 头 X-Api-Key)
- 模式: 异步 submit + 轮询 query
- 输出: 带 speaker 标签的 Markdown（服务返回时）

与 vibevoice-asr (podcast-transcribe) 的区别:
- 不需要本地音频文件,直接传公网 audio URL 给火山云
- 不需要 GPU
- 默认请求 speaker diarization、标点和 utterance 明细
- 不需要走 podcast-transcript-fix,可直接进 podcast-summary

Usage:
  # 直接传 audio URL
  python3 scripts/volc_asr.py --audio-url https://example.com/ep.mp3

  # 从 episode_dir/README.md 的 "> Audio URL:" 行解析 URL
  python3 scripts/volc_asr.py --episode-dir audios/foo/ep1

  # 自定义轮询
  python3 scripts/volc_asr.py --audio-url <url> --episode-dir <dir> --poll-interval 15 --max-wait 3600

Environment:
  VOLC_ASR_API_KEY  火山引擎 AUC ASR 的 API Key (必需)

Exit codes:
  0  transcript.md 已写入 (或已存在跳过)
  1  参数错误 / API key 缺失
  2  火山 ASR 调用失败 (submit 或 query 返回错误)
  3  轮询超时
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

# ── 火山引擎 AUC 大模型 ASR (v3) ──────────────────────────────────────────────
SUBMIT_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit"
QUERY_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query"
RESOURCE_ID = "volc.seedasr.auc"

# 轮询默认值
DEFAULT_POLL_INTERVAL = 10   # 秒
DEFAULT_MAX_WAIT = 1800      # 30 分钟

# query 返回 code → 处理
CODE_SUCCESS = "20000000"
CODE_RUNNING_1 = "20000001"
CODE_RUNNING_2 = "20000002"
CODE_SILENT = "20000003"

# transcript.md 最小字节数 (与 transcribe.sh 一致)
MIN_TRANSCRIPT_BYTES = 100

# README.md 中 audio URL 的标记行
_AUDIO_URL_RE = re.compile(r"^>\s*Audio URL:\s*(\S+)\s*$", re.MULTILINE)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def log(msg: str) -> None:
    print(f"[volc-asr] {msg}", file=sys.stderr)


def err(msg: str) -> None:
    print(f"[volc-asr] ERROR: {msg}", file=sys.stderr)


def env_bool(name: str, default: bool) -> bool:
    """Read a boolean environment override without third-party dependencies."""
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false")


def request_options() -> dict[str, object]:
    """Defaults optimized for readable, information-rich podcast transcripts."""
    return {
        "model_name": "bigmodel",
        "enable_itn": env_bool("VOLC_ASR_ENABLE_ITN", True),
        "enable_punc": env_bool("VOLC_ASR_ENABLE_PUNC", True),
        "enable_ddc": env_bool("VOLC_ASR_ENABLE_DDC", True),
        "enable_speaker_info": env_bool("VOLC_ASR_SPEAKER_INFO", True),
        "enable_channel_split": env_bool("VOLC_ASR_CHANNEL_SPLIT", False),
        # Required for utterance/timing/word detail in the response.
        "show_utterances": True,
        "vad_segment": env_bool("VOLC_ASR_VAD_SEGMENT", False),
        # Do not silently redact source material by default.
        "sensitive_words_filter": "",
    }


# ── audio URL 解析 ────────────────────────────────────────────────────────────

def audio_format(url: str) -> str:
    """从 URL path 推断音频格式 (与 ai-signal 一致)。"""
    path = urlparse(url or "").path.lower()
    for ext in ("mp3", "m4a", "mp4", "wav", "aac", "ogg", "opus", "flac"):
        if path.endswith(f".{ext}"):
            return "ogg" if ext == "opus" else ext
    return "mp3"


def parse_audio_url_from_readme(ep_dir: Path) -> str | None:
    """从 episode_dir/README.md 的 '> Audio URL: <url>' 行解析音频 URL。"""
    readme = ep_dir / "README.md"
    if not readme.is_file():
        return None
    text = readme.read_text(encoding="utf-8", errors="replace")
    m = _AUDIO_URL_RE.search(text)
    return m.group(1) if m else None


# ── 火山 ASR 调用 ─────────────────────────────────────────────────────────────

def _headers(api_key: str, request_id: str, *, is_submit: bool) -> dict[str, str]:
    h = {
        "Content-Type": "application/json",
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": RESOURCE_ID,
        "X-Api-Request-Id": request_id,
    }
    if is_submit:
        h["X-Api-Sequence"] = "-1"
    return h


def _post_json(url: str, headers: dict[str, str], payload: dict) -> tuple[dict[str, str], bytes]:
    """POST JSON with the standard library and return lowercase headers + body."""
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            return {key.lower(): value for key, value in response.headers.items()}, response.read()
    except HTTPError as exc:
        body = exc.read(500).decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"network error: {exc.reason}") from exc


def _decode_json(body: bytes) -> dict:
    if not body:
        return {}
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid JSON response: {body[:500]!r}") from exc
    return value if isinstance(value, dict) else {"data": value}


def submit_task(
    api_key: str,
    audio_url: str,
    *,
    request_id: str | None = None,
) -> str:
    """提交转录任务,返回 request_id (供后续 query)。"""
    request_id = request_id or str(uuid.uuid4())
    payload = {
        "user": {"uid": "podcast-summary"},
        "audio": {
            "url": audio_url,
            "format": audio_format(audio_url),
        },
        "request": request_options(),
    }
    headers, body = _post_json(
        SUBMIT_URL,
        _headers(api_key, request_id, is_submit=True),
        payload,
    )
    # 火山 v3: 成功标志在响应头 X-Api-Status-Code, body 可能为空 {}
    code = headers.get("x-api-status-code", "")
    if code != CODE_SUCCESS:
        body_preview = body[:500].decode("utf-8", errors="replace") or "(empty)"
        raise RuntimeError(
            f"submit failed: status={code or '(missing)'} "
            f"message={headers.get('x-api-message', headers.get('x-api-status-message', '(none)'))} "
            f"body={body_preview}"
        )
    return request_id


def query_task(
    api_key: str,
    request_id: str,
) -> dict:
    """查询任务状态,返回原始 JSON。"""
    headers, body = _post_json(
        QUERY_URL,
        _headers(api_key, request_id, is_submit=False),
        {},
    )
    # 火山 v3: 状态码在响应头, body 可能为空 (运行中)
    code = headers.get("x-api-status-code", "")
    if code and code != CODE_SUCCESS and code not in (CODE_RUNNING_1, CODE_RUNNING_2, CODE_SILENT):
        body_preview = body[:500].decode("utf-8", errors="replace") or "(empty)"
        raise RuntimeError(
            f"query failed: status={code} "
            f"message={headers.get('x-api-message', headers.get('x-api-status-message', '(none)'))} "
            f"body={body_preview}"
        )
    data = _decode_json(body)
    # 把响应头状态码注入到 data 里, poll_until_done 优先读它
    if isinstance(data, dict) and "code" not in data:
        data["code"] = code
    return data


def extract_text(data: dict) -> str:
    """从 query 返回的 JSON 中递归提取纯文本。

    火山 v3 query 成功响应结构: {"result": {"text": "...", "additions": {...}}, "audio_info": {...}}
    先深入 result 再取 text,避免误取 result 本身 (dict) 而非其中的 text 字符串。
    """
    if isinstance(data, dict):
        # 先深入 result (火山 v3 实际结构: result.text)
        result = data.get("result")
        if isinstance(result, dict):
            t = extract_text(result)
            if t:
                return t
        # 顶层直接是字符串字段
        for key in ("text", "transcript"):
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        # 列表字段
        for key in ("data", "utterances", "sentences"):
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, list):
                parts = []
                for item in v:
                    t = extract_text(item)
                    if t:
                        parts.append(t)
                if parts:
                    return "\n".join(parts)
            if isinstance(v, dict):
                t = extract_text(v)
                if t:
                    return t
    if isinstance(data, list):
        parts = []
        for item in data:
            t = extract_text(item)
            if t:
                parts.append(t)
        if parts:
            return "\n".join(parts)
    return ""


def poll_until_done(
    api_key: str,
    request_id: str,
    *,
    poll_interval: int,
    max_wait: int,
) -> dict:
    """轮询 query 接口直到完成，返回完整结果以保留 speaker 和时间信息。"""
    deadline = time.monotonic() + max_wait
    last_code = ""
    while time.monotonic() < deadline:
        data = query_task(api_key, request_id)
        code = str(data.get("code", ""))
        last_code = code
        if code == CODE_SUCCESS:
            text = extract_text(data)
            if not text:
                raise RuntimeError(
                    f"query success but no text extracted: {json.dumps(data, ensure_ascii=False)[:500]}"
                )
            return data
        if code in (CODE_RUNNING_1, CODE_RUNNING_2):
            log(f"running (code={code}), waiting {poll_interval}s ...")
            time.sleep(poll_interval)
            continue
        if code == CODE_SILENT:
            raise RuntimeError("audio is silent (code=20000003)")
        raise RuntimeError(
            f"query failed: code={code} message={data.get('message')} "
            f"payload={json.dumps(data, ensure_ascii=False)[:500]}"
        )
    raise TimeoutError(f"polling timed out after {max_wait}s (last code={last_code})")


# ── transcript.md 写入 ─────────────────────────────────────────────────────────

def _utterance_metadata(utterance: dict, key: str) -> object | None:
    additions = utterance.get("additions")
    if isinstance(additions, dict) and additions.get(key) is not None:
        return additions[key]
    aliases = {
        "speaker": ("speaker", "speaker_id"),
        "channel_id": ("channel_id",),
    }
    for alias in aliases.get(key, (key,)):
        if utterance.get(alias) is not None:
            return utterance[alias]
    return None


def format_transcript(data: dict) -> str:
    """Preserve utterance boundaries, speakers, and channels for AI use."""
    result = data.get("result") if isinstance(data.get("result"), dict) else data
    utterances = result.get("utterances") if isinstance(result, dict) else None
    if isinstance(utterances, list) and utterances:
        lines = []
        for utterance in utterances:
            if not isinstance(utterance, dict):
                continue
            text = str(utterance.get("text") or "").replace("\r", " ").replace("\n", " ").strip()
            if not text:
                continue
            labels = []
            speaker = _utterance_metadata(utterance, "speaker")
            channel = _utterance_metadata(utterance, "channel_id")
            if speaker is not None:
                labels.append(f"Speaker {speaker}")
            if channel is not None:
                labels.append(f"Channel {channel}")
            prefix = f"**[{' | '.join(labels)}]** " if labels else ""
            lines.append(f"{prefix}{text}")
        if lines:
            return "\n\n".join(lines)
    return extract_text(data)


def write_transcript(ep_dir: Path, data: dict, *, audio_url: str, request_id: str) -> Path:
    """Write an AI-readable transcript and retain the complete cloud response."""
    out = ep_dir / "transcript.md"
    text = format_transcript(data)
    if not text:
        raise RuntimeError("query succeeded but no transcript text or utterances were found")
    header = (
        "# Transcription\n"
        f"> Audio URL: {audio_url}\n"
        f"> ASR: volcengine bigmodel (request_id={request_id})\n"
        "> Detail: speaker labels shown; timing retained in volc-response.json\n"
        f"> Generated by volcengine-asr\n\n"
    )
    out.write_text(header + text + "\n", encoding="utf-8")
    (ep_dir / "volc-response.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out


# ── 主流程 ─────────────────────────────────────────────────────────────────────

def transcribe_one(
    audio_url: str,
    ep_dir: Path,
    *,
    api_key: str,
    poll_interval: int,
    max_wait: int,
) -> Path:
    """提交一个音频 URL 到火山 ASR,轮询,写 transcript.md。"""
    request_id = str(uuid.uuid4())
    log(f"submitting audio_url={audio_url} request_id={request_id}")

    submit_task(api_key, audio_url, request_id=request_id)
    log(f"submitted, polling every {poll_interval}s (max {max_wait}s) ...")
    data = poll_until_done(
        api_key, request_id,
        poll_interval=poll_interval, max_wait=max_wait,
    )

    out = write_transcript(ep_dir, data, audio_url=audio_url, request_id=request_id)
    log(f"transcript written: {out} ({out.stat().st_size} bytes)")
    return out


def default_episode_dir(audio_url: str) -> Path:
    parsed = urlparse(audio_url)
    filename = unquote(Path(parsed.path).stem) or "audio"
    slug = re.sub(r"[^\w\u3400-\u4dbf\u4e00-\u9fff.-]+", "-", filename).strip("-.")
    slug = (slug or "audio")[:64]
    digest = hashlib.sha256(audio_url.encode("utf-8")).hexdigest()[:10]
    root = Path(os.environ.get("PODCAST_OUTPUT_DIR", PROJECT_ROOT / "audios"))
    return (root / "cloud" / f"{slug}-{digest}").expanduser().resolve()


def ensure_readme(ep_dir: Path, audio_url: str, title: str | None) -> None:
    readme = ep_dir / "README.md"
    if readme.exists():
        return
    readme.write_text(
        f"# {title or unquote(Path(urlparse(audio_url).path).name) or 'Audio'}\n\n"
        f"> Audio URL: {audio_url}\n"
        f"> Source: direct public audio URL\n",
        encoding="utf-8",
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Transcribe audio URL via Volcengine AUC bigmodel ASR.",
    )
    ap.add_argument("--audio-url", help="音频公网 URL (如未指定则从 episode-dir/README.md 解析)")
    ap.add_argument("--episode-dir", help="输出目录；直接传 audio URL 时可省略")
    ap.add_argument("--title", help="直接 audio URL 模式下写入 README 的标题")
    ap.add_argument("--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL, help=f"轮询间隔秒 (默认 {DEFAULT_POLL_INTERVAL})")
    ap.add_argument("--max-wait", type=int, default=DEFAULT_MAX_WAIT, help=f"最大等待秒 (默认 {DEFAULT_MAX_WAIT})")
    args = ap.parse_args()

    api_key = os.environ.get("VOLC_ASR_API_KEY")
    if not api_key:
        err("VOLC_ASR_API_KEY environment variable is not set")
        return 1

    if not args.audio_url and not args.episode_dir:
        err("provide --audio-url or --episode-dir")
        return 1

    ep_dir = (
        Path(args.episode_dir).expanduser().resolve()
        if args.episode_dir else default_episode_dir(args.audio_url)
    )
    ep_dir.mkdir(parents=True, exist_ok=True)

    # 幂等: 已有非空 transcript.md 则跳过
    transcript_path = ep_dir / "transcript.md"
    if transcript_path.is_file() and transcript_path.stat().st_size >= MIN_TRANSCRIPT_BYTES:
        log(f"transcript.md already exists ({transcript_path.stat().st_size} bytes), skipping")
        print(f"TRANSCRIPT={transcript_path}")
        return 0

    # 解析 audio URL
    audio_url = args.audio_url or parse_audio_url_from_readme(ep_dir)
    if not audio_url:
        err("no --audio-url given and no '> Audio URL:' line found in README.md")
        return 1

    # 基本校验
    parsed = urlparse(audio_url)
    if parsed.scheme not in ("http", "https"):
        err(f"audio_url must be http/https, got: {audio_url}")
        return 1
    ensure_readme(ep_dir, audio_url, args.title)

    try:
        out = transcribe_one(
            audio_url, ep_dir,
            api_key=api_key,
            poll_interval=args.poll_interval,
            max_wait=args.max_wait,
        )
    except TimeoutError as e:
        err(str(e))
        return 3
    except RuntimeError as e:
        err(str(e))
        return 2
    except Exception as e:
        err(f"unexpected: {e}")
        return 2

    # 校验输出
    if not out.is_file() or out.stat().st_size < MIN_TRANSCRIPT_BYTES:
        err(f"transcript suspiciously small: {out}")
        return 2

    print(f"TRANSCRIPT={out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
