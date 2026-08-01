#!/usr/bin/env python3
"""podcast_transcript_probe.py — 官方 transcript 发现瀑布（transcript 拉取第 1 级）。

在音频下载前优先探测官方/公开 transcript，命中即写 transcript.md 跳过音频下载 + ASR。
顺序（用户指定：官方 transcript 最优先）：
  1. RSS `<podcast:transcript>` 标签（Podcast Index namespace，transistor/megaphone 常见）
  2. description / content:encoded 里的 transcript 链接（Lex Fridman 就是这条命中）
  3. entry.link 是 YouTube URL → youtube_transcript_api 拉官方/自动字幕
  4. sources.yaml 配的 youtube_transcript_rss（YouTube 频道 Atom feed）→ 按标题规范化匹配拉字幕
  5. episode 页面抓取（找 transcript 链接或启发式提正文）

未命中返回 (None, None)，调用方 fallback 到 ai-signal（第 2 级）或 vibevoice-asr（第 3 级）。

质量守卫（移植自 ai-signal generate_feed.py）：
  - looks_like_transcript: 最小 600 字符 + 含 "transcript" 或 ≥3 个 speaker 标记
  - transcript_too_sparse: 已知时长时 <150 chars/min 视为 show notes 而非 transcript
  - clean_transcript_text: 处理 VTT/JSON/HTML 格式、去时间码
"""

from __future__ import annotations

import html as _html
import ipaddress
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urlparse

import warnings

import httpx
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

_ROOT = Path(__file__).resolve().parent.parent
MIN_TRANSCRIPT_CHARS = 600
MAX_TRANSCRIPT_CHARS = 500_000
MIN_CHARS_PER_MIN = 150
HTTP_TIMEOUT = httpx.Timeout(30.0, connect=8.0)

# podcast_name(slug) → youtube_transcript_rss，启动时从 sources.yaml 读一次
_YT_RSS_MAP: dict[str, str] = {}


def _load_yt_rss_map() -> dict[str, str]:
    try:
        import yaml
        path = _ROOT / "feeds" / "sources.yaml"
        data = yaml.safe_load(path.read_text("utf-8"))
    except Exception:
        return {}
    out = {}
    for entry in (data or {}).get("podcasts", []) or []:
        yt = entry.get("youtube_transcript_rss")
        if yt and entry.get("slug"):
            out[entry["slug"]] = yt
    return out


def _yt_rss_for(podcast_name: str) -> str:
    if not _YT_RSS_MAP:
        _YT_RSS_MAP.update(_load_yt_rss_map())
    return _YT_RSS_MAP.get(podcast_name, "")


# ── 安全 ──────────────────────────────────────────────────────────────────────

def _is_safe_url(url: str) -> bool:
    """拒绝非 http(s) 和内网 IP（SSRF 防护）。域名放行。"""
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False
        host = p.hostname
        if not host:
            return False
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        except ValueError:
            pass  # 域名，放行
        return True
    except Exception:
        return False


# ── 文本清洗（移植 ai-signal）─────────────────────────────────────────────────

def html_to_text(html_str: str) -> str:
    return BeautifulSoup(html_str or "", "html.parser").get_text(separator=" ", strip=True)


def clean_text(text: str) -> str:
    return (text or "").strip()


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clean_transcript_text(text: str) -> str:
    text = clean_text(text)
    stripped = text.lstrip()
    # JSON 字幕（如 podcastindex transcript 可能是 json）
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            payload = json.loads(stripped)
            parts: list[str] = []

            def collect(value):
                if isinstance(value, str):
                    if len(value.strip()) > 2:
                        parts.append(value.strip())
                elif isinstance(value, list):
                    for item in value:
                        collect(item)
                elif isinstance(value, dict):
                    for key in ("text", "transcript", "body", "content", "utterance"):
                        if key in value:
                            collect(value[key])
                    if not any(k in value for k in ("text", "transcript", "body", "content", "utterance")):
                        for item in value.values():
                            collect(item)

            collect(payload)
            text = "\n".join(parts)
        except Exception:
            pass
    elif "<" in text:
        text = html_to_text(text)
    text = _html.unescape(text)
    # 去 VTT/WebVTT 头与时间码
    text = re.sub(r"(?m)^(WEBVTT|Kind:.*|Language:.*)$", "", text)
    text = re.sub(r"(?m)^\d+$", "", text)
    text = re.sub(r"\d{2}:\d{2}:\d{2}[.,]\d{3}\s+-->\s+\d{2}:\d{2}:\d{2}[.,]\d{3}.*", "", text)
    text = re.sub(r"\n?\s*(Share|Subscribe|Listen to this episode|Download|Open in Apple Podcasts)\s*\n?",
                  "\n", text, flags=re.I)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if len(text) > MAX_TRANSCRIPT_CHARS:
        text = text[:MAX_TRANSCRIPT_CHARS].rstrip() + "\n\n[Transcript truncated]"
    return text


def looks_like_transcript(text: str) -> bool:
    text = normalize_text(text)
    if len(text) < MIN_TRANSCRIPT_CHARS:
        return False
    lower = text.lower()
    if "access the full transcript" in lower or "log in to view episode transcripts" in lower:
        return False
    speaker_marks = len(re.findall(r"\b[A-Z][A-Za-z .'-]{1,40}:\s", text))
    return "transcript" in lower or speaker_marks >= 3


def _duration_minutes(duration: str) -> int:
    parts = str(duration or "").strip().split(":")
    try:
        parts = [int(float(p)) for p in parts if p != ""]
    except ValueError:
        return 0
    if len(parts) == 3:
        return parts[0] * 60 + parts[1]
    if len(parts) == 2:
        return parts[0]
    if len(parts) == 1:
        return parts[0] // 60
    return 0


def transcript_too_sparse(text: str, duration: str) -> bool:
    minutes = _duration_minutes(duration)
    if not text or minutes < 10:
        return False
    return len(text) / minutes < MIN_CHARS_PER_MIN


# ── RSS <podcast:transcript> 标签提取（feedparser 不解析，需 ElementTree）────

def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def parse_transcript_urls(xml_text: str) -> dict[str, list[str]]:
    """解析 RSS XML，返回 {guid_or_link_or_title: [transcript_urls]}。

    namespace-agnostic：按 localname=='transcript' 匹配，兼容 podcast: / itunes: 等前缀。
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}
    result: dict[str, list[str]] = {}
    for item in root.iter():
        if _localname(item.tag) != "item":
            continue
        key_parts: dict[str, str] = {}
        urls: list[str] = []
        for child in item:
            ln = _localname(child.tag)
            if ln in ("guid", "link", "title") and child.text:
                key_parts[ln] = child.text.strip()
            if ln == "transcript":
                url = child.get("url") or child.get("href") or (child.text or "").strip()
                if url:
                    urls.append(url)
        if urls:
            for k in ("guid", "link", "title"):
                if k in key_parts:
                    result.setdefault(key_parts[k], []).extend(urls)
    return result


# ── 链接发现 ──────────────────────────────────────────────────────────────────

def find_transcript_links(html_str: str, base_url: str = "") -> list[str]:
    candidates: list[str] = []
    for match in re.finditer(r"""<a\b[^>]*?href=["']([^"']+)["'][^>]*>(.*?)</a>""",
                             html_str or "", re.I | re.S):
        href = _html.unescape(match.group(1)).strip()
        label = html_to_text(match.group(2))
        if not href or href.startswith(("#", "mailto:", "tel:")):
            continue
        joined = f"{label} {href}".lower()
        if "transcript" in joined or "full-text" in joined or "full text" in joined:
            candidates.append(urljoin(base_url, href))
    # 纯文本里裸 transcript URL（Lex 的 description 里就是裸链接，无 <a> 标签）
    for m in re.finditer(r"https?://[^\s\"<>]+transcript[^\s\"<>]*", html_str or "", re.I):
        candidates.append(m.group(0).rstrip(".,);]"))
    return list(dict.fromkeys(candidates))


def _fetch_text(url: str, hard_timeout: int = 25) -> str | None:
    """httpx 超时对 SSL 握手卡死可能失效，用 signal.alarm 硬超时兜底。"""
    if not _is_safe_url(url):
        return None
    import signal

    def _alarm(signum, frame):
        raise TimeoutError("hard timeout")

    old = signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(hard_timeout)
    try:
        resp = httpx.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        return resp.content.decode("utf-8", errors="replace")
    except Exception:
        return None
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def transcript_from_url(url: str) -> str | None:
    body = _fetch_text(url)
    if not body:
        return None
    text = clean_transcript_text(body)
    if looks_like_transcript(text):
        return text
    return None


# ── YouTube 字幕 ──────────────────────────────────────────────────────────────

def _youtube_video_id(link: str) -> str | None:
    if not link:
        return None
    parsed = urlparse(link)
    if "youtube.com" in parsed.netloc:
        m = re.search(r"[?&]v=([a-zA-Z0-9_-]{11})", link)
        if m:
            return m.group(1)
        m = re.search(r"/(?:shorts|embed|live)/([a-zA-Z0-9_-]{11})", parsed.path)
        return m.group(1) if m else None
    if "youtu.be" in parsed.netloc:
        vid = parsed.path.strip("/")[:11]
        return vid if len(vid) == 11 else None
    return None


def get_youtube_transcript(link: str) -> str | None:
    vid = _youtube_video_id(link)
    if not vid:
        return None
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        segs = YouTubeTranscriptApi().fetch(vid)
        text = " ".join(s.text for s in segs)
        text = clean_transcript_text(text)
        return text if looks_like_transcript(text) else None
    except Exception:
        return None


def _canonical_title(value: str) -> str:
    title = str(value or "").split(" | ", 1)[0]
    return re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()


def fetch_youtube_channel_match(yt_rss_url: str, target_title: str) -> str | None:
    """拉 YouTube 频道 Atom feed，按标题规范化匹配同期视频，拉字幕。"""
    body = _fetch_text(yt_rss_url)
    if not body:
        return None
    target = _canonical_title(target_title)
    if not target:
        return None
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    atom = "http://www.w3.org/2005/Atom"
    yt = "http://www.youtube.com/xml/schemas/2015"
    for entry in root.iter(f"{{{atom}}}entry"):
        title_el = entry.find(f"{{{atom}}}title")
        if title_el is None or not title_el.text:
            continue
        if _canonical_title(title_el.text) != target:
            continue
        link = ""
        for link_el in entry.findall(f"{{{atom}}}link"):
            if link_el.get("rel") == "alternate":
                link = link_el.get("href", "")
                break
        if link:
            return get_youtube_transcript(link)
    return None


# ── episode 页面抓取 ──────────────────────────────────────────────────────────

def _extract_probable_transcript_text(html_str: str) -> str | None:
    patterns = [
        r"""<article\b[^>]*>(.*?)</article>""",
        r"""<div\b[^>]*(?:class|id)=["'][^"']*(?:transcript|entry-content|post-content|article|body)[^"']*["'][^>]*>(.*?)</div>""",
    ]
    candidates: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, html_str or "", re.I | re.S):
            text = html_to_text(match.group(1))
            if len(text) > 500:
                candidates.append(text)
    full = html_to_text(html_str)
    lower = full.lower()
    idx = lower.find("transcript")
    if idx >= 0:
        candidates.append(full[idx:])
    candidates.append(full)
    candidates.sort(key=len, reverse=True)
    for text in candidates:
        cleaned = clean_transcript_text(text)
        if looks_like_transcript(cleaned):
            return cleaned
    return None


def transcript_from_episode_page(url: str) -> str | None:
    if not url or not _is_safe_url(url):
        return None
    host = urlparse(url).netloc.lower()
    # Spotify/Anchor 目录页只是 show notes，明确跳过
    if host in {"anchor.fm", "podcasters.spotify.com", "open.spotify.com"}:
        return None
    body = _fetch_text(url)
    if not body:
        return None
    for candidate in find_transcript_links(body, url):
        text = transcript_from_url(candidate)
        if text:
            return text
    return _extract_probable_transcript_text(body)


# ── 主入口 ────────────────────────────────────────────────────────────────────

def probe_official_transcript(episode: dict) -> tuple[str | None, str | None]:
    """探测官方/公开 transcript。返回 (text, source) 或 (None, None)。

    episode dict 需含：title, episode_url, description, content(可选),
    transcript_urls(可选，来自 RSS 标签), duration(可选), podcast_name(slug)。
    """
    duration = episode.get("duration", "")

    def usable(text: str | None) -> bool:
        if not text:
            return False
        if not looks_like_transcript(text):
            return False
        if transcript_too_sparse(text, duration):
            return False
        return True

    # 1. RSS <podcast:transcript> 标签
    for url in episode.get("transcript_urls", []) or []:
        text = transcript_from_url(url)
        if usable(text):
            return text, "rss_transcript"

    # 2. description / content 里的 transcript 链接
    for html_src in (episode.get("content", ""), episode.get("description", "")):
        for url in find_transcript_links(html_src, episode.get("episode_url", "")):
            text = transcript_from_url(url)
            if usable(text):
                return text, "description_transcript_link"

    # 3. entry.link 是 YouTube URL → 字幕
    link = episode.get("episode_url", "")
    if _youtube_video_id(link):
        text = get_youtube_transcript(link)
        if usable(text):
            return text, "youtube_caption"

    # 4. youtube_transcript_rss 配置 → 频道匹配
    yt_rss = _yt_rss_for(episode.get("podcast_name", ""))
    if yt_rss:
        text = fetch_youtube_channel_match(yt_rss, episode.get("title", ""))
        if usable(text):
            return text, "youtube_channel_match"

    # 5. episode 页面
    text = transcript_from_episode_page(link)
    if usable(text):
        return text, "episode_page"

    return None, None


if __name__ == "__main__":
    # 自测：对某 feed 跑探测
    import feedparser
    feed_url = sys.argv[1] if len(sys.argv) > 1 else "https://feeds.transistor.fm/acquired"
    podcast_name = sys.argv[2] if len(sys.argv) > 2 else "acquired"
    resp = httpx.get(feed_url, timeout=HTTP_TIMEOUT, follow_redirects=True)
    tmap = parse_transcript_urls(resp.text)
    feed = feedparser.parse(resp.text)
    for entry in feed.entries[:3]:
        ep = {
            "title": entry.title,
            "episode_url": entry.get("link", ""),
            "description": entry.get("summary", ""),
            "content": (entry.get("content", [{}])[0].get("value", "")
                        if entry.get("content") else entry.get("summary", "")),
            "duration": getattr(entry, "itunes_duration", "") or "",
            "transcript_urls": [],
            "podcast_name": podcast_name,
        }
        for key in (entry.get("id", ""), entry.get("link", ""), entry.title):
            ep["transcript_urls"].extend(tmap.get(key, []))
        ep["transcript_urls"] = list(dict.fromkeys(ep["transcript_urls"]))
        text, src = probe_official_transcript(ep)
        print(f"[{podcast_name}] {entry.title[:50]}")
        print(f"  transcript_urls: {ep['transcript_urls']}")
        print(f"  probe: source={src} chars={len(text) if text else 0}")
        print()
