#!/usr/bin/env python3
"""
rss_download.py — RSS-based podcast episode downloader (library module).

Called by topic_pipeline.py. No CLI entry point.
"""

import sys
from pathlib import Path

import feedparser
import httpx

_SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPTS_DIR))

from xiaoyuzhou_download import (  # noqa: E402
    audio_filename,
    download_audio,
    file_hash,
    sanitize_filename,
    shorten_title,
)
from podcast_transcript_probe import (  # noqa: E402
    parse_transcript_urls,
    probe_official_transcript,
)


def _ext_from_url(url: str) -> str:
    """Return audio extension ('mp3', 'm4a', or 'ogg') inferred from URL path."""
    path = url.split("?")[0].lower()
    if path.endswith(".m4a"):
        return "m4a"
    if path.endswith(".ogg"):
        return "ogg"
    return "mp3"


# ── Podcast list parsing ──────────────────────────────────────────────────────

def parse_podcast_list_rss(list_path: Path) -> list[dict]:
    """Parse english-podcast-list.md → list of {name, feed_url}.

    Format: "Podcast Name - https://rss.feed/url"
    Lines starting with # are comments and are skipped.
    """
    podcasts = []
    for line in list_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        idx = line.rfind(" - http")
        if idx == -1:
            continue
        name = line[:idx].strip()
        url = line[idx + 3:].strip()   # skip " - "
        if name and url.startswith("http"):
            podcasts.append({"name": name, "feed_url": url})
    return podcasts


# ── RSS parsing ───────────────────────────────────────────────────────────────

def _find_audio_enclosure(entry) -> tuple[str | None, str | None]:
    """Return (audio_url, ext) from RSS entry enclosures, or (None, None).

    Uses dict.get bypass for feedparser's FeedParserDict which overrides
    __getitem__ to dynamically reconstruct 'enclosures' from links. If the
    raw dict has 'enclosures' set explicitly (e.g. = []), that takes
    precedence; otherwise fall back to getattr which uses the reconstruction.
    """
    if isinstance(entry, dict):
        # dict.get bypasses FeedParserDict.__getitem__ override
        raw = dict.get(entry, "enclosures")
        enclosures = raw if raw is not None else getattr(entry, "enclosures", [])
    else:
        enclosures = getattr(entry, "enclosures", [])
    for enc in enclosures:
        url = enc.get("url", "")
        mime = enc.get("type", "")
        if not url:
            continue
        if "audio" in mime or url.lower().endswith((".mp3", ".m4a", ".ogg")):
            return url, _ext_from_url(url)
    return None, None


def _normalize_episode(entry, podcast_name: str, feed_url: str) -> dict:
    """Normalize a feedparser entry into the episode dict shape used by topic_pipeline."""
    audio_url, _ext = _find_audio_enclosure(entry)
    eid = (getattr(entry, "id", "") or "").strip()
    if not eid:
        eid = (getattr(entry, "link", "") or feed_url)

    description = (
        getattr(entry, "summary", "")
        or getattr(entry, "description", "")
        or ""
    )
    content = ""
    if getattr(entry, "content", None):
        content = entry.content[0].get("value", "") if entry.content else ""
    duration = getattr(entry, "itunes_duration", "") or ""

    return {
        "eid": eid,
        "title": getattr(entry, "title", ""),
        "podcast_name": podcast_name,
        "episode_url": getattr(entry, "link", "") or feed_url,
        "audio_url": audio_url or "",
        "description": description,
        "content": content,
        "duration": duration,
        "transcript_urls": [],
        "pubDate": getattr(entry, "published", ""),
        "playCount": 0,   # RSS has no play counts; always 0 for shape parity
        "_description": description,   # duplicate for keyword-match compat with topic_pipeline
    }


def fetch_feed_episodes(feed_url: str, podcast_name: str) -> list[dict]:
    """Fetch RSS feed and return list of normalized episode dicts.

    Uses httpx to fetch raw XML (feedparser.parse on URL hides the raw bytes we
    need to extract <podcast:transcript> tags), then feedparser parses the text.
    Episodes with no audio enclosure are skipped.
    """
    try:
        resp = httpx.get(feed_url, timeout=30.0, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        xml_text = resp.text
    except Exception as exc:
        print(f"  [warn] Feed fetch failed for {feed_url}: {exc}")
        return []

    transcript_map = parse_transcript_urls(xml_text)
    feed = feedparser.parse(xml_text)
    if feed.bozo:
        print(f"  [warn] Feed parse issue for {feed_url}: {feed.bozo_exception}")
    episodes = []
    for entry in feed.entries:
        ep = _normalize_episode(entry, podcast_name=podcast_name, feed_url=feed_url)
        if not ep["audio_url"]:
            continue
        # 关联 RSS <podcast:transcript> 标签（按 guid/link/title 任一命中）
        turls = []
        for key in (ep["eid"], ep["episode_url"], ep["title"]):
            turls.extend(transcript_map.get(key, []))
        ep["transcript_urls"] = list(dict.fromkeys(turls))
        episodes.append(ep)
    return episodes


# ── Download ──────────────────────────────────────────────────────────────────

def download_rss_episode(episode: dict, output_dir: Path) -> Path | None:
    """Download one RSS episode audio + write README.md.

    Official transcript is probed first (RSS <podcast:transcript> tag /
    description link / YouTube caption / episode page). On hit, writes
    transcript.md and skips audio download entirely.

    Returns output directory path on success, None on failure.
    Skips download if audio file already exists (dedup by prefix-hash glob).
    Prints: ✓ Episode complete: {output_dir}
    """
    title = episode["title"]
    podcast_name = sanitize_filename(episode["podcast_name"])
    short_title = shorten_title(title, podcast_name=episode["podcast_name"])
    audio_url = episode.get("audio_url", "")
    description = episode.get("description", "")

    ep_dir = output_dir / podcast_name / short_title
    ep_dir.mkdir(parents=True, exist_ok=True)

    # Write README (保留 audio_url 供 volcengine-asr 云端转录使用)
    readme_lines = [f"# {title}\n"]
    if audio_url:
        readme_lines.append(f"\n> Audio URL: {audio_url}\n")
    readme_lines.append(f"\n{description}")
    readme_path = ep_dir / "README.md"
    readme_path.write_text("".join(readme_lines), encoding="utf-8")

    # 官方 transcript 优先（省音频下载 + ASR）— transcript 拉取第 1 级
    transcript, src = probe_official_transcript(episode)
    if transcript:
        (ep_dir / "transcript.md").write_text(transcript, encoding="utf-8")
        print(f"  ✓ official transcript ({len(transcript)} chars, source={src}) — audio skipped")
        print(f"✓ Episode complete: {ep_dir}")
        return ep_dir

    if not audio_url:
        print(f"  [skip] No audio URL and no official transcript for: {title}")
        return None

    ext = _ext_from_url(audio_url)

    # Dedup: skip if audio already present
    prefix = audio_filename(short_title, ext)
    existing = list(ep_dir.glob(f"{prefix}-*.{ext}"))
    if existing:
        audio_path = existing[0]
        print(f"Audio already exists: {audio_path.name}")
        print(f"✓ Episode complete: {ep_dir}")
        return ep_dir

    # Download
    tmp_path = None
    try:
        tmp_path = ep_dir / f"audio.{ext}"
        download_audio(audio_url, tmp_path)
        h = file_hash(tmp_path)
        final_name = f"{prefix}-{h}.{ext}"
        audio_path = ep_dir / final_name
        tmp_path.rename(audio_path)
        print(f"  Renamed → {final_name}")
    except Exception as exc:
        print(f"  [error] Download failed: {exc}")
        if tmp_path and tmp_path.exists():
            tmp_path.unlink()
        return None

    print(f"✓ Episode complete: {ep_dir}")
    return ep_dir
