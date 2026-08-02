#!/usr/bin/env python3
"""Fetch transcripts from a PodHood channel (e.g. 十分吸引 -> shifenxiyin.podhood.com).

Read-only, no API key required (PodHood serves published content anonymously).
Documented surface: /api/v1/*  (the unversioned /api/* are aliases).

Why REST and not the /mcp server?
  The PodHood MCP server exposes only a `search` tool (semantic discovery with
  timestamped citations). It has NO tool to download a full word-level transcript.
  The full transcript lives at  GET /api/v1/episodes/{id}/transcript
  which is what this script uses, so it works head-less in cron / automations.

Endpoints used
--------------
  GET /api/v1/channels/{slug}/episodes      -> paginated catalog (supports facet filters)
  GET /api/v1/channels/{slug}/search        -> hybrid semantic+keyword search
  GET /api/v1/episodes/{id}/transcript      -> word-level transcript (segments)
  GET /api/v1/episodes/{id}/chapters        -> chapters & key moments (optional)

Facet ids (topics / people / companies / products / years) are scraped from the
channel's HTML shell, because PodHood does not expose a facet-list endpoint.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DEFAULT_CHANNEL = "shifenxiyin"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0.0.0 Safari/537.36")

CJK = r"[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]"


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def http_get_json(url: str, params: dict | None = None) -> dict:
    if params:
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        if qs:
            url += ("&" if "?" in url else "?") + qs
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def http_get_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


# --------------------------------------------------------------------------- #
# Filesystem / text helpers
# --------------------------------------------------------------------------- #
def sanitize_filename(name: str, max_len: int = 80) -> str:
    """Keep alphanumerics (incl. CJK), dashes, and underscores; replace the rest."""
    name = re.sub(r"\s+", "-", name.strip())
    name = re.sub(r"[^\w\u3400-\u9fff\u4e00-\u9fff-]", "", name)
    name = re.sub(r"-{2,}", "-", name).strip("-")
    return name[:max_len] or "untitled"


def ms_to_hms(ms: int | None) -> str:
    if not ms:
        return ""
    s = round(ms / 1000)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def clean_text(text: str) -> str:
    """Collapse word-segmentation spaces between CJK / CJK-punctuation characters."""
    if not text:
        return ""
    return re.sub(rf"(?<={CJK})\s+(?={CJK})", "", text).strip()


# --------------------------------------------------------------------------- #
# Facet parsing (topics / people / companies / products / years)
# --------------------------------------------------------------------------- #
def parse_facets(html: str) -> dict:
    """Extract facet arrays from the channel HTML shell.

    The RSC payload stores them as doubly-escaped JSON, e.g.
    \\\"topics\\\":[{\\\"id\\\":\\\"...\\\",\\\"name\\\":\\\"人工智能\\\",...}].
    Normalising the double backslash turns it into clean JSON we can slice.
    """
    bs = chr(92)
    norm = html.replace(bs + '"', '"')
    cands = ["years", "topics", "people", "companies", "products", "collections"]
    found = {}
    for k in cands:
        idx = norm.find('"' + k + '":[')
        if idx >= 0:
            found[k] = idx
    ordered = sorted(found.items(), key=lambda x: x[1])
    out = {"topics": [], "people": [], "companies": [], "products": [], "years": []}
    for i, (key, arrstart) in enumerate(ordered):
        cstart = arrstart + len('"' + key + '":[')
        nxt = ordered[i + 1][1] if i + 1 < len(ordered) else len(norm)
        close = norm.rfind("]", cstart, nxt)
        if close < cstart:
            continue
        content = norm[cstart:close]
        try:
            arr = json.loads("[" + content + "]")
        except Exception:
            continue
        if key == "years":
            out["years"] = [int(y) for y in arr]
        else:
            out[key] = [(str(x.get("id")), str(x.get("name", ""))) for x in arr]
    return out


def get_show_name(html: str) -> str:
    m = re.search(r'"name"\s*:\s*"([^"]+)","avatar"', html)
    if m:
        return m.group(1)
    m = re.search(r"<title>([^<]+)</title>", html)
    if m:
        return m.group(1).split("|")[0].split("-")[0].strip()
    return ""


# --------------------------------------------------------------------------- #
# Name -> id resolution
# --------------------------------------------------------------------------- #
def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def resolve(names, facet_list, kind: str):
    """Resolve user-supplied display names to facet ids (fuzzy)."""
    if not names:
        return []
    # facet_list items are (id, name)
    ids, misses = [], []
    for name in names:
        key = _norm(name)
        matches = [fid for fid, nm in facet_list if _norm(nm) == key]
        if not matches:
            matches = [fid for fid, nm in facet_list if key and key in _norm(nm)]
        if not matches:
            misses.append(name)
            continue
        if len(matches) > 1:
            print(f"  ⚠  {kind} “{name}” 命中多个，取第一个；--list-facets 可查看全部",
                  file=sys.stderr)
        ids.append(matches[0])
    for m in misses:
        print(f"  ⚠  未找到 {kind}：“{m}”（忽略该过滤）。--list-facets 可列出全部",
              file=sys.stderr)
    return ids


# --------------------------------------------------------------------------- #
# Episode discovery
# --------------------------------------------------------------------------- #
def list_episodes(channel: str, filters: dict, sort: str, limit: int,
                  since_ts: float | None):
    """Collect matching episodes via the paginated catalog endpoint."""
    base = f"https://{channel}.podhood.com/api/v1/channels/{channel}/episodes"
    out, cursor, seen, pages = [], None, set(), 0
    while len(out) < limit:
        params = {"sort": sort, "limit": 50}
        params.update({k: v for k, v in filters.items() if v})
        if cursor:
            params["cursor"] = cursor
        data = http_get_json(base, params)
        eps = data.get("episodes", [])
        if not eps:
            break
        for ep in eps:
            eid = ep.get("id")
            if not eid or eid in seen:
                continue
            seen.add(eid)
            if since_ts is not None and ep.get("publishedAt"):
                try:
                    ts = datetime.fromisoformat(
                        ep["publishedAt"].replace("Z", "+00:00")).timestamp()
                    if ts < since_ts:
                        continue
                except ValueError:
                    pass
            out.append(ep)
            if len(out) >= limit:
                break
        pages += 1
        cursor = data.get("nextCursor")
        if not cursor or pages >= 50:
            break
    return out


def search_episodes(channel: str, query: str, filters: dict, limit: int):
    """Hybrid semantic + keyword search; returns normalized episode dicts.

    The API's own `limit` is not always honoured, so we also truncate locally.
    """
    base = f"https://{channel}.podhood.com/api/v1/channels/{channel}/search"
    params = {"query": query, "limit": 100}
    params.update({k: v for k, v in filters.items() if v})
    data = http_get_json(base, params)
    eps = []
    for e in data.get("episodes", []):
        eps.append({
            "id": e.get("episodeId"),
            "title": e.get("title"),
            "link": e.get("url"),
            "publishedAt": e.get("publishedAt"),
            "durationMs": e.get("durationMs"),
            "summary": e.get("summary"),
        })
    if limit and limit < 10 ** 8:
        eps = eps[:limit]
    return eps


# --------------------------------------------------------------------------- #
# Transcript fetching + rendering
# --------------------------------------------------------------------------- #
def fetch_transcript(channel: str, ep_id: str) -> dict | None:
    url = f"https://{channel}.podhood.com/api/v1/episodes/{ep_id}/transcript"
    try:
        return http_get_json(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def render_transcript_md(ep: dict, transcript: dict, with_ts: bool) -> str:
    lines = [f"# {ep.get('title', 'Untitled')}", ""]
    dur = ms_to_hms(ep.get("durationMs"))
    meta = []
    if ep.get("publishedAt"):
        meta.append(f"发布：{ep['publishedAt'][:10]}")
    if dur:
        meta.append(f"时长：{dur}")
    if ep.get("link"):
        meta.append(f"来源：{ep['link']}")
    if meta:
        lines.append("> " + " · ".join(meta))
        lines.append("")
    lines.append("（以下为逐句转录文稿，按说话人分段；由 PodHood 转录，未经人工校对）")
    lines.append("")

    segs = transcript.get("segments", [])
    if not segs:
        lines.append("_（该集暂无转录文稿）_")
        return "\n".join(lines) + "\n"

    speaker, buf, buf_ts = None, [], None

    def flush():
        nonlocal speaker, buf, buf_ts
        if speaker is None or not buf:
            return
        head = f"**{speaker}**"
        if with_ts and buf_ts is not None:
            head += f" · {ms_to_hms(buf_ts)}"
        lines.append(head)
        lines.append("".join(buf))
        lines.append("")

    for s in segs:
        sp = s.get("speakerLabel") or "未知"
        text = clean_text(s.get("text", ""))
        if not text:
            continue
        if sp != speaker:
            flush()
            speaker, buf, buf_ts = sp, [text], s.get("startMs")
        else:
            buf.append(text)
    flush()
    return "\n".join(lines) + "\n"


def render_readme(ep: dict, transcript: dict | None, filters: dict,
                  channel: str, show: str) -> str:
    lines = [f"# {ep.get('title', 'Untitled')}", ""]
    dur = ms_to_hms(ep.get("durationMs"))
    if ep.get("publishedAt"):
        lines.append(f"> 发布日期：{ep['publishedAt'][:10]}")
    if dur:
        lines.append(f"> 时长：{dur}")
    if ep.get("link"):
        lines.append(f"> 节目链接：{ep['link']}")
    lines.append(f"> 播客：{show or channel}（PodHood: {channel}）")
    applied = []
    if filters.get("topicIds"):
        applied.append(f"话题={filters['topicIds']}")
    if filters.get("personIds"):
        applied.append(f"人物={filters['personIds']}")
    if filters.get("entityIds"):
        applied.append(f"公司/产品={filters['entityIds']}")
    if filters.get("year"):
        applied.append(f"年份={filters['year']}")
    if applied:
        lines.append(f"> 抓取过滤：{', '.join(applied)}")
    lines.append("")
    if transcript:
        segs = transcript.get("segments", [])
        speakers = []
        for s in segs:
            sp = s.get("speakerLabel") or "未知"
            if sp not in speakers:
                speakers.append(sp)
        lines.append(f"转录段落：{len(segs)} 段 · 说话人：{', '.join(speakers)}")
        lines.append("")
    if ep.get("summary"):
        lines.append("## AI 摘要")
        lines.append(ep["summary"])
        lines.append("")
    if ep.get("description"):
        lines.append("## 节目介绍")
        lines.append(ep["description"])
        lines.append("")
    return "\n".join(lines) + "\n"


def save_episode(ep: dict, transcript: dict | None, out_dir: str, channel: str,
                 show: str, filters: dict, save_json: bool, refresh: bool,
                 with_ts: bool) -> str | None:
    title = sanitize_filename(ep.get("title", "untitled"))
    ep_dir = os.path.abspath(os.path.join(out_dir, channel, title))
    os.makedirs(ep_dir, exist_ok=True)
    tpath = os.path.join(ep_dir, "transcript.md")
    if os.path.exists(tpath) and not refresh:
        return None  # already fetched (incremental)
    t = transcript if transcript is not None else {"segments": []}
    with open(tpath, "w", encoding="utf-8") as f:
        f.write(render_transcript_md(ep, t, with_ts))
    with open(os.path.join(ep_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write(render_readme(ep, t, filters, channel, show))
    if save_json:
        with open(os.path.join(ep_dir, "transcript.json"), "w", encoding="utf-8") as f:
            json.dump(t, f, ensure_ascii=False, indent=1)
    with open(os.path.join(ep_dir, "source.json"), "w", encoding="utf-8") as f:
        json.dump({
            "channel": channel,
            "episodeId": ep.get("id"),
            "title": ep.get("title"),
            "link": ep.get("link"),
            "publishedAt": ep.get("publishedAt"),
            "fetchedAt": datetime.now(timezone.utc).isoformat(),
            "filters": filters,
        }, f, ensure_ascii=False, indent=1)
    return ep_dir


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(
        description="Fetch PodHood podcast transcripts (e.g. 十分吸引).")
    ap.add_argument("--channel", default=DEFAULT_CHANNEL,
                    help="PodHood channel slug / subdomain "
                         "(default: shifenxiyin = 十分吸引)")
    ap.add_argument("--channel-name", default=None,
                    help="Display name override for the output directory")
    ap.add_argument("--topic", action="append", default=[],
                    help="话题名过滤（可重复，如 --topic 宏观经济）")
    ap.add_argument("--person", action="append", default=[],
                    help="人物名过滤（主持人/嘉宾，可重复）")
    ap.add_argument("--entity", action="append", default=[],
                    help="公司/产品名过滤（可重复）")
    ap.add_argument("--year", type=int, default=None, help="按发布年份过滤")
    ap.add_argument("--query", default=None,
                    help="语义/关键词搜索（启用 search 接口）")
    ap.add_argument("--sort", default="newest",
                    choices=["newest", "popular", "longest", "oldest"])
    ap.add_argument("--limit", type=int, default=20,
                    help="最多处理多少集（0 = 不限）")
    ap.add_argument("--since", default=None,
                    help="只抓取该日期之后发布的单集（YYYY-MM-DD）")
    ap.add_argument("--output-dir", default="audios/transcripts/podhood")
    ap.add_argument("--list-facets", action="store_true",
                    help="列出该频道全部话题/人物/公司/产品/年份后退出")
    ap.add_argument("--list-only", action="store_true",
                    help="只列出匹配的的单集，不下载转录")
    ap.add_argument("--no-transcript", action="store_true",
                    help="只写 README/元数据，不下载转录文稿")
    ap.add_argument("--save-json", action="store_true",
                    help="同时保存原始 transcript.json")
    ap.add_argument("--with-timestamps", action="store_true",
                    help="转录每段前标注起始时间戳")
    ap.add_argument("--refresh", action="store_true",
                    help="已存在也重新下载")
    args = ap.parse_args()

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", args.channel):
        ap.error("--channel 只允许小写字母、数字和连字符")

    if args.limit == 0:
        args.limit = 10 ** 9

    # --- load facets (topics/people/companies/products/years) ---
    print(f"→ 加载频道 {args.channel}.podhood.com 的筛选维度…", file=sys.stderr)
    html = http_get_text(f"https://{args.channel}.podhood.com/")
    facets = parse_facets(html)
    show = args.channel_name or get_show_name(html) or args.channel

    if args.list_facets:
        print(f"\n频道：{show}（{args.channel}）")
        print("年份：", facets["years"])
        for kind, key in [("话题", "topics"), ("人物", "people"),
                          ("公司", "companies"), ("产品", "products")]:
            items = facets[key]
            print(f"\n{kind}（{len(items)}）:")
            for fid, name in items:
                print(f"  {name}  <{fid}>")
        return

    # --- resolve names -> ids ---
    filters: dict = {}
    topic_ids = resolve(args.topic, facets["topics"], "话题")
    person_ids = resolve(args.person, facets["people"], "人物")
    entity_ids = resolve(args.entity, facets["companies"] + facets["products"],
                         "公司/产品")
    if topic_ids:
        filters["topicIds"] = ",".join(topic_ids)
    if person_ids:
        filters["personIds"] = ",".join(person_ids)
    if entity_ids:
        filters["entityIds"] = ",".join(entity_ids)
    if args.year:
        filters["year"] = args.year

    # --- discover episodes ---
    since_ts = None
    if args.since:
        since_ts = datetime.fromisoformat(args.since + "T00:00:00").timestamp()

    if args.query:
        print(f"→ 语义搜索：“{args.query}”", file=sys.stderr)
        episodes = search_episodes(args.channel, args.query, filters, args.limit)
    else:
        print("→ 浏览频道目录…", file=sys.stderr)
        episodes = list_episodes(args.channel, filters, args.sort,
                                 args.limit, since_ts)

    print(f"匹配到 {len(episodes)} 集", file=sys.stderr)
    if args.list_only:
        for ep in episodes:
            print(f"  {ep.get('publishedAt', '')[:10]:10}  {ep.get('title', '')}")
            print(f"      id={ep.get('id')}  {ep.get('link', '')}")
        return

    # --- fetch transcripts ---
    saved = skipped = 0
    for ep in episodes:
        eid = ep.get("id")
        if not eid:
            continue
        transcript = None
        if not args.no_transcript:
            transcript = fetch_transcript(args.channel, eid)
        ep_dir = save_episode(ep, transcript, args.output_dir, args.channel,
                              show, filters, args.save_json, args.refresh,
                              args.with_timestamps)
        if ep_dir is None:
            skipped += 1
            print(f"↷ 已存在，跳过：{ep.get('title', '')[:40]}")
        else:
            saved += 1
            print(f"✓ Episode complete: {ep_dir}")

    print(f"\n完成：新增 {saved} 集，跳过 {skipped} 集（已存在）。"
          f"输出目录：{os.path.join(args.output_dir, args.channel)}")


if __name__ == "__main__":
    main()
