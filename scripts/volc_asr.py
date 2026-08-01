#!/usr/bin/env python3
"""volc_asr.py — 调用火山引擎大模型 ASR,把音频 URL 转成纯文本 transcript.md。

火山引擎录音文件识别 2.0 (大模型版本) 异步转录:
- API: https://openspeech.bytedance.com/api/v3/auc/bigmodel/{submit,query}
- 鉴权: 环境变量 VOLC_ASR_API_KEY (HTTP 头 X-Api-Key)
- 模式: 异步 submit + 轮询 query
- 输出: 纯文本 (无 speaker 标签)

与 vibevoice-asr (podcast-transcribe) 的区别:
- 不需要本地音频文件,直接传公网 audio URL 给火山云
- 不需要 GPU
- 输出无 speaker 标签 (纯文本,与 yt-dlp 字幕一致)
- 不需要走 podcast-transcript-fix,可直接进 podcast-summary

Usage:
  # 直接传 audio URL
  python3 scripts/volc_asr.py --audio-url https://example.com/ep.mp3 --episode-dir audios/foo/ep1

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
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx

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


def log(msg: str) -> None:
    print(f"[volc-asr] {msg}", file=sys.stderr)


def err(msg: str) -> None:
    print(f"[volc-asr] ERROR: {msg}", file=sys.stderr)


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


def submit_task(
    client: httpx.Client,
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
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,        # 逆文本归一化
            "enable_punc": True,        # 标点恢复
            "enable_ddc": False,
            "enable_speaker_info": False,   # 不做说话人分离 (纯文本)
            "enable_channel_split": False,
            "show_utterances": False,
            "vad_segment": False,
            "sensitive_words_filter": "",
        },
    }
    resp = client.post(
        SUBMIT_URL,
        headers=_headers(api_key, request_id, is_submit=True),
        json=payload,
    )
    resp.raise_for_status()
    # 火山 v3: 成功标志在响应头 X-Api-Status-Code, body 可能为空 {}
    code = resp.headers.get("X-Api-Status-Code", "")
    if code != CODE_SUCCESS:
        body_preview = resp.text[:500] if resp.text else "(empty)"
        raise RuntimeError(
            f"submit failed: status={code or '(missing)'} "
            f"message={resp.headers.get('X-Api-Status-Message', '(none)')} "
            f"body={body_preview}"
        )
    return request_id


def query_task(
    client: httpx.Client,
    api_key: str,
    request_id: str,
) -> dict:
    """查询任务状态,返回原始 JSON。"""
    resp = client.post(
        QUERY_URL,
        headers=_headers(api_key, request_id, is_submit=False),
        json={},
    )
    resp.raise_for_status()
    # 火山 v3: 状态码在响应头, body 可能为空 (运行中)
    code = resp.headers.get("X-Api-Status-Code", "")
    if code and code != CODE_SUCCESS and code not in (CODE_RUNNING_1, CODE_RUNNING_2, CODE_SILENT):
        body_preview = resp.text[:500] if resp.text else "(empty)"
        raise RuntimeError(
            f"query failed: status={code} "
            f"message={resp.headers.get('X-Api-Status-Message', '(none)')} "
            f"body={body_preview}"
        )
    # 尝试解析 body; 运行中时 body 可能为空
    try:
        data = resp.json()
    except Exception:
        return {"code": code, "_raw_text": resp.text}
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
    client: httpx.Client,
    api_key: str,
    request_id: str,
    *,
    poll_interval: int,
    max_wait: int,
) -> str:
    """轮询 query 接口直到完成,返回转录文本。"""
    deadline = time.monotonic() + max_wait
    last_code = ""
    while time.monotonic() < deadline:
        data = query_task(client, api_key, request_id)
        code = str(data.get("code", ""))
        last_code = code
        if code == CODE_SUCCESS:
            text = extract_text(data)
            if not text:
                raise RuntimeError(
                    f"query success but no text extracted: {json.dumps(data, ensure_ascii=False)[:500]}"
                )
            return text
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

def write_transcript(ep_dir: Path, text: str, *, audio_url: str, request_id: str) -> Path:
    """按 episode_dir 契约写 transcript.md (与 transcribe.sh 格式对齐,但无 speaker 标签)。"""
    out = ep_dir / "transcript.md"
    header = (
        "# Transcription\n"
        f"> Audio URL: {audio_url}\n"
        f"> ASR: volcengine bigmodel (request_id={request_id})\n"
        f"> Generated by volcengine-asr\n\n"
    )
    out.write_text(header + text + "\n", encoding="utf-8")
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

    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        # submit 可能因为 request_id 重复而返回错误,这里用新生成的 id 理论上不会冲突
        submit_task(client, api_key, audio_url, request_id=request_id)
        log(f"submitted, polling every {poll_interval}s (max {max_wait}s) ...")
        text = poll_until_done(
            client, api_key, request_id,
            poll_interval=poll_interval, max_wait=max_wait,
        )

    out = write_transcript(ep_dir, text, audio_url=audio_url, request_id=request_id)
    log(f"transcript written: {out} ({len(text)} chars)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Transcribe audio URL via Volcengine AUC bigmodel ASR.",
    )
    ap.add_argument("--audio-url", help="音频公网 URL (如未指定则从 episode-dir/README.md 解析)")
    ap.add_argument("--episode-dir", required=True, help="输出 transcript.md 的目录")
    ap.add_argument("--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL, help=f"轮询间隔秒 (默认 {DEFAULT_POLL_INTERVAL})")
    ap.add_argument("--max-wait", type=int, default=DEFAULT_MAX_WAIT, help=f"最大等待秒 (默认 {DEFAULT_MAX_WAIT})")
    args = ap.parse_args()

    api_key = os.environ.get("VOLC_ASR_API_KEY")
    if not api_key:
        err("VOLC_ASR_API_KEY environment variable is not set")
        return 1

    ep_dir = Path(args.episode_dir).expanduser().resolve()
    if not ep_dir.is_dir():
        err(f"episode_dir does not exist: {ep_dir}")
        return 1

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
