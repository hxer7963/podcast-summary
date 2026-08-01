#!/usr/bin/env python3
"""Search and download public transcripts from podcasttranscript.ai.

Read endpoints are public and rate-limited. This client deliberately uses only
the Python standard library so it can run in cron and automation environments.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


API_BASE = "https://backend.podcasttranscript.ai/api/v1"
WEB_BASE = "https://podcasttranscript.ai/library"
DEFAULT_OUTPUT = "audios/transcripts/podcasttranscript"
USER_AGENT = "data-analysis-podcasttranscript-fetch/1.0"


def sanitize_filename(value: str, max_len: int = 100) -> str:
    value = re.sub(r"\s+", "-", value.strip())
    value = re.sub(r"[^\w\u3400-\u9fff.-]", "", value)
    value = re.sub(r"-{2,}", "-", value).strip("-.")
    return value[:max_len] or "untitled"


def format_duration(seconds: Any) -> str:
    try:
        seconds = int(seconds or 0)
    except (TypeError, ValueError):
        return ""
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def format_timestamp(value: Any) -> str:
    try:
        seconds = float(value or 0)
    except (TypeError, ValueError):
        seconds = 0
    # Some transcript providers use milliseconds.
    if seconds > 100_000:
        seconds /= 1000
    return format_duration(round(seconds))


def request_json(path: str, params: dict[str, Any] | None = None,
                 retries: int = 3) -> dict[str, Any]:
    query = ""
    if params:
        clean = {k: v for k, v in params.items() if v is not None and v != ""}
        query = "?" + urllib.parse.urlencode(clean)
    url = f"{API_BASE}{path}{query}"
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
            if not payload.get("ok", False):
                error = payload.get("error") or {}
                raise RuntimeError(error.get("message") or f"API error: {payload}")
            return payload
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt + 1 < retries:
                delay = int(exc.headers.get("Retry-After", "2"))
                time.sleep(max(1, delay))
                continue
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"HTTP {exc.code} for {url}: {detail[:300]}") from exc
        except urllib.error.URLError as exc:
            if attempt + 1 < retries:
                time.sleep(1 + attempt)
                continue
            raise RuntimeError(f"Network error for {url}: {exc.reason}") from exc
    raise RuntimeError(f"Failed to fetch {url}")


def list_transcriptions(query: str | None, category: str | None,
                        language: str | None, sort: str, limit: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    page = 1
    while len(results) < limit:
        page_limit = min(100, limit - len(results))
        payload = request_json("/transcriptions", {
            "page": page,
            "limit": page_limit,
            "q": query,
            "category": category,
            "language": language,
            "sort": sort,
        })
        batch = payload.get("data") or []
        results.extend(item for item in batch if isinstance(item, dict))
        meta = payload.get("meta") or {}
        if not batch or page >= int(meta.get("total_pages") or page):
            break
        page += 1
    return results[:limit]


def slug_from_url(value: str) -> str | None:
    parsed = urllib.parse.urlparse(value)
    if parsed.netloc.lower() not in {"podcasttranscript.ai", "www.podcasttranscript.ai"}:
        return None
    match = re.fullmatch(r"/library/([^/]+)/?", parsed.path)
    return urllib.parse.unquote(match.group(1)) if match else None


def resolve_url(value: str) -> list[dict[str, Any]]:
    slug = slug_from_url(value)
    if not slug:
        raise ValueError("Expected a podcasttranscript.ai/library/<slug> URL")
    words = [word for word in slug.split("-") if len(word) > 2]
    queries = [" ".join(words[:2]), words[0] if words else slug]
    seen_queries: set[str] = set()
    for query in queries:
        if not query or query in seen_queries:
            continue
        seen_queries.add(query)
        matches = list_transcriptions(query, None, None, "newest", 100)
        exact = [item for item in matches if item.get("slug") == slug]
        if exact:
            # The public library occasionally contains duplicate records. Prefer
            # the newest stable record and download only one canonical copy.
            exact.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
            return exact[:1]
    raise RuntimeError(f"No public transcription found for slug: {slug}")


def fetch_transcription(transcription_id: str) -> dict[str, Any]:
    payload = request_json(
        f"/transcriptions/{urllib.parse.quote(transcription_id)}",
        {"include": "segments"},
    )
    data = payload.get("data") or {}
    item = data.get("transcription", data)
    if not isinstance(item, dict) or not item.get("id"):
        raise RuntimeError(f"Unexpected transcription response for {transcription_id}")
    return item


def segment_text(segment: dict[str, Any]) -> str:
    return str(segment.get("text") or segment.get("content") or "").strip()


def render_transcript(item: dict[str, Any], with_timestamps: bool) -> str:
    title = str(item.get("title") or "Untitled")
    lines = [f"# {title}", ""]
    metadata = []
    duration = format_duration(item.get("duration"))
    if duration:
        metadata.append(f"时长：{duration}")
    if item.get("language"):
        metadata.append(f"语言：{item['language']}")
    if item.get("category"):
        metadata.append(f"分类：{item['category']}")
    if metadata:
        lines.extend(["> " + " · ".join(metadata), ""])
    lines.extend([
        "（以下为 PodcastTranscript AI 提供的完整转录，未经人工校对）",
        "",
    ])

    transcript = item.get("segments") or item.get("transcript")
    if isinstance(transcript, str):
        body = transcript.strip()
        lines.append(body if body else "_（该集暂无转录文稿）_")
    elif isinstance(transcript, list):
        for segment in transcript:
            if not isinstance(segment, dict):
                continue
            text = segment_text(segment)
            if not text:
                continue
            speaker = segment.get("speaker") or segment.get("speakerLabel")
            start = segment.get("start") or segment.get("start_time") or segment.get("startMs")
            label = f"**{speaker}**" if speaker else ""
            if with_timestamps and start is not None:
                label = f"{label} · {format_timestamp(start)}" if label else f"**{format_timestamp(start)}**"
            if label:
                lines.extend([label, text, ""])
            else:
                lines.extend([text, ""])
    else:
        lines.append("_（该集暂无转录文稿）_")
    return "\n".join(lines).rstrip() + "\n"


def render_readme(item: dict[str, Any], source_url: str | None,
                  filters: dict[str, Any]) -> str:
    title = str(item.get("title") or "Untitled")
    public_url = f"{WEB_BASE}/{item.get('slug')}" if item.get("slug") else source_url
    lines = [f"# {title}", ""]
    for label, value in [
        ("平台页面", public_url),
        ("原始音频", item.get("audio_url")),
        ("时长", format_duration(item.get("duration"))),
        ("语言", item.get("language")),
        ("分类", item.get("category")),
        ("平台 ID", item.get("id")),
    ]:
        if value:
            lines.append(f"> {label}：{value}")
    applied = ", ".join(f"{key}={value}" for key, value in filters.items() if value)
    if applied:
        lines.append(f"> 抓取条件：{applied}")
    lines.append("")
    if item.get("description"):
        lines.extend(["## 节目介绍", "", str(item["description"]).strip(), ""])
    if item.get("summary"):
        lines.extend(["## 平台摘要", "", str(item["summary"]).strip(), ""])
    return "\n".join(lines).rstrip() + "\n"


def save_episode(item: dict[str, Any], output_dir: str, source_url: str | None,
                 filters: dict[str, Any], refresh: bool, save_json: bool,
                 with_timestamps: bool) -> Path | None:
    category = sanitize_filename(str(item.get("category") or "uncategorized"))
    title = sanitize_filename(str(item.get("title") or item.get("slug") or item["id"]))
    episode_dir = Path(output_dir) / category / title
    transcript_path = episode_dir / "transcript.md"
    if transcript_path.exists() and not refresh:
        return None
    episode_dir.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(render_transcript(item, with_timestamps), encoding="utf-8")
    (episode_dir / "README.md").write_text(
        render_readme(item, source_url, filters), encoding="utf-8")
    source = {
        "platform": "podcasttranscript.ai",
        "transcriptionId": item.get("id"),
        "slug": item.get("slug"),
        "title": item.get("title"),
        "publicUrl": f"{WEB_BASE}/{item.get('slug')}" if item.get("slug") else source_url,
        "audioUrl": item.get("audio_url"),
        "filters": filters,
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
    }
    (episode_dir / "source.json").write_text(
        json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if save_json:
        (episode_dir / "transcript.json").write_text(
            json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return episode_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search and download complete public PodcastTranscript AI transcripts.")
    parser.add_argument("target", nargs="?",
                        help="Library URL, stable transcription ID, or search query")
    parser.add_argument("--query", "--topic", dest="query",
                        help="Keyword/topic search across the public library")
    parser.add_argument("--id", dest="transcription_id", help="Stable transcription ID")
    parser.add_argument("--category", help="Category filter, e.g. technology")
    parser.add_argument("--language", help="Language filter, e.g. English")
    parser.add_argument("--sort", choices=["newest", "popular"], default="newest")
    parser.add_argument("--limit", type=int, default=20, help="Maximum episodes (default: 20)")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT)
    parser.add_argument("--list-only", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--save-json", action="store_true")
    parser.add_argument("--with-timestamps", action="store_true")
    parser.add_argument("--no-transcribe", action="store_true",
                        help="Compatibility flag; this source already provides transcripts")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.limit < 1:
        raise SystemExit("--limit must be at least 1")

    source_url = args.target if args.target and slug_from_url(args.target) else None
    if args.transcription_id:
        candidates = [{"id": args.transcription_id}]
    elif source_url:
        candidates = resolve_url(source_url)
    else:
        query = args.query or args.target
        if not query and not (args.category or args.language):
            raise SystemExit("Provide a library URL, --id, --query/--topic, or a filter")
        candidates = list_transcriptions(
            query, args.category, args.language, args.sort, args.limit)

    if args.list_only:
        for item in candidates:
            print(f"{item.get('id', '')}\t{item.get('category', '')}\t{item.get('title', '')}")
        return 0

    filters = {
        "query": args.query or (None if source_url or args.transcription_id else args.target),
        "category": args.category,
        "language": args.language,
        "sort": args.sort,
    }
    saved = skipped = failed = 0
    seen_ids: set[str] = set()
    for candidate in candidates:
        transcription_id = str(candidate.get("id") or "")
        if not transcription_id or transcription_id in seen_ids:
            continue
        seen_ids.add(transcription_id)
        try:
            item = fetch_transcription(transcription_id)
            episode_dir = save_episode(
                item, args.output_dir, source_url, filters, args.refresh,
                args.save_json, args.with_timestamps)
            if episode_dir is None:
                skipped += 1
                print(f"↷ 已存在，跳过：{item.get('title', transcription_id)}")
            else:
                saved += 1
                print(f"✓ Episode complete: {episode_dir.resolve()}")
        except Exception as exc:  # Continue a topic batch after one bad record.
            failed += 1
            print(f"✗ {transcription_id}: {exc}", file=sys.stderr)

    print(f"完成：新增 {saved} 集，跳过 {skipped} 集，失败 {failed} 集。", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
