#!/usr/bin/env python3
"""spotify_fetch.py — Spotify URL → iTunes RSS → delegate to rss_download.

Spotify is a discoverability layer for almost all non-exclusive podcasts —
the actual audio lives on Megaphone / Libsyn / Anchor / etc. and is
exposed via the same RSS feed that iTunes uses. So:

    open.spotify.com/{episode,show,playlist}
        → scrape embed page __NEXT_DATA__   (no auth)
        → get show name + episode title(s)
        → iTunes Search API → RSS feedUrl   (no auth)
        → feedparser → match episode by title
        → rss_download.download_rss_episode (existing path)

Supports:
  - Single episode:  open.spotify.com/episode/<eid>
  - Whole show:      open.spotify.com/show/<sid>      (use --latest / --episode-index / --all)
  - Playlist:        open.spotify.com/playlist/<pid>  (downloads every podcast track)

Usage:
  python3 scripts/spotify_fetch.py <spotify_url> --output-dir audios
  python3 scripts/spotify_fetch.py <spotify_url> --resolve-only
  python3 scripts/spotify_fetch.py <show_url> --latest --output-dir audios
  python3 scripts/spotify_fetch.py <show_url> --episode-index 0 --output-dir audios

Output:
  Prints "✓ Episode complete: <ep_dir>" per successful download
  (matching xiaoyuzhou_download.py / rss_fetch.py convention).
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen

_SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPTS_DIR))

from rss_download import (  # noqa: E402  (must come after sys.path tweak)
    download_rss_episode,
    fetch_feed_episodes,
)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_EMBED_TPL = "https://open.spotify.com/embed/{kind}/{sid}"
_ITUNES_SEARCH = "https://itunes.apple.com/search?term={q}&entity=podcast&limit=10"

log = logging.getLogger("spotify_fetch")


# ─── Spotify scraping ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SpotifyTrack:
    """One podcast episode discovered on Spotify (any URL form)."""
    show_name: str         # entity.subtitle on Spotify
    episode_title: str     # entity.name (or trackList[].title)
    spotify_uri: str       # spotify:episode:<id>


def _parse_spotify_url(url: str) -> tuple[str, str]:
    """Return (kind, id) where kind ∈ {episode, show, playlist}.

    Accepts open.spotify.com URLs (with or without country prefix) and
    spotify: URIs.
    """
    if url.startswith("spotify:"):
        parts = url.split(":")
        if len(parts) == 3 and parts[1] in {"episode", "show", "playlist"}:
            return parts[1], parts[2]
        raise ValueError(f"Unsupported Spotify URI: {url}")
    parsed = urlparse(url)
    if parsed.hostname not in {"open.spotify.com", "spotify.link"}:
        raise ValueError(f"Not a Spotify URL (host={parsed.hostname!r}): {url}")
    # Strip optional /intl-xx prefix and split.
    segs = [s for s in parsed.path.split("/") if s]
    if segs and segs[0].startswith("intl-"):
        segs = segs[1:]
    if len(segs) >= 2 and segs[0] in {"episode", "show", "playlist"}:
        return segs[0], segs[1]
    raise ValueError(f"Unsupported Spotify URL path: {parsed.path}")


def _http_get(url: str) -> str:
    req = Request(url, headers={"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    with urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _extract_next_data(html_text: str) -> dict:
    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html_text,
        re.S,
    )
    if not m:
        raise RuntimeError("No __NEXT_DATA__ in Spotify embed page (layout changed?)")
    return json.loads(html_mod.unescape(m.group(1)))


def _entity(next_data: dict) -> dict:
    return next_data["props"]["pageProps"]["state"]["data"]["entity"]


def fetch_spotify_tracks(url: str) -> list[SpotifyTrack]:
    """Resolve a Spotify URL to the list of podcast tracks it contains.

    - episode → 1 track
    - show    → 1 track (the latest episode the embed surfaces; for the full
                catalog we rely on the iTunes RSS we delegate to next)
    - playlist→ N tracks (every entry the embed exposes — Spotify limits this
                to ~30 in the embed payload, sufficient for our use)
    """
    kind, sid = _parse_spotify_url(url)
    embed_url = _EMBED_TPL.format(kind=kind, sid=sid)
    log.info("Fetching Spotify embed: %s", embed_url)
    page = _http_get(embed_url)
    data = _extract_next_data(page)
    entity = _entity(data)
    etype = entity.get("type")

    if etype == "episode":
        return [SpotifyTrack(
            show_name=entity.get("subtitle", "") or "",
            episode_title=entity.get("name", "") or entity.get("title", ""),
            spotify_uri=entity.get("uri", f"spotify:episode:{sid}"),
        )]

    if etype == "playlist":
        out: list[SpotifyTrack] = []
        for t in entity.get("trackList", []) or []:
            uri = t.get("uri", "")
            if not uri.startswith("spotify:episode:"):
                # Skip music tracks; the playlist could be mixed.
                continue
            out.append(SpotifyTrack(
                show_name=t.get("subtitle", "") or "",
                episode_title=t.get("title", "") or "",
                spotify_uri=uri,
            ))
        if not out:
            raise RuntimeError("Playlist has no podcast episodes (only music tracks?)")
        return out

    if etype == "show":
        # Some show embeds do return a real show entity; others surface the
        # latest episode (with type=episode). Either way, subtitle == show name.
        return [SpotifyTrack(
            show_name=entity.get("subtitle", "") or entity.get("name", ""),
            episode_title="",  # caller will treat as "fetch whole show"
            spotify_uri=f"spotify:show:{sid}",
        )]

    raise RuntimeError(f"Unsupported Spotify entity type: {etype!r}")


# ─── iTunes Search → RSS feedUrl ─────────────────────────────────────────────

def itunes_lookup_show_rss(show_name: str) -> str:
    """Return the iTunes feedUrl for a podcast by name (best match)."""
    if not show_name:
        raise ValueError("Empty show name; cannot look up on iTunes")
    api = _ITUNES_SEARCH.format(q=quote_plus(show_name))
    log.info("iTunes Search: %s", show_name)
    req = Request(api, headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(req, timeout=20) as resp:
            payload = json.load(resp)
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"iTunes Search failed: {exc}") from exc

    results = payload.get("results", []) or []
    if not results:
        raise RuntimeError(f"iTunes Search: no podcast matches for {show_name!r}")

    # Prefer exact-name match (case-insensitive, em/en-dash normalized).
    target = _normalize_for_match(show_name)
    exact = [r for r in results if _normalize_for_match(r.get("collectionName", "")) == target]
    chosen = exact[0] if exact else results[0]
    feed_url = chosen.get("feedUrl")
    if not feed_url:
        raise RuntimeError(f"iTunes match has no feedUrl: {chosen.get('collectionName')!r}")
    log.info("iTunes match: %s → %s", chosen.get("collectionName"), feed_url)
    return feed_url


# ─── Episode-title fuzzy matching ────────────────────────────────────────────

def _normalize_for_match(s: str) -> str:
    """Lowercase, strip punctuation/whitespace, normalize dashes."""
    s = s.lower()
    s = s.replace("–", "-").replace("—", "-").replace("‐", "-")
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


_EP_NUM_RE = re.compile(r"(?:ep[.\s]*|episode\s*|e\s*)(\d{1,4})", re.I)


def _ep_number(title: str) -> str | None:
    m = _EP_NUM_RE.search(title)
    return m.group(1) if m else None


def find_matching_episode(rss_episodes: list[dict], spotify_title: str) -> dict:
    """Locate the RSS episode whose title best matches a Spotify title.

    Strategy:
      1. Exact (normalized) match on full title.
      2. Episode number match (EP.328 / EP 328 / Episode 328).
      3. Substring containment (normalized) — Spotify often appends "(EP.xxx)"
         that the RSS title doesn't have, or vice versa.
      4. First token (guest name) prefix match as last resort.
    Raises RuntimeError if nothing reasonable matches.
    """
    if not rss_episodes:
        raise RuntimeError("No episodes in RSS feed")

    norm_target = _normalize_for_match(spotify_title)
    target_epnum = _ep_number(spotify_title)

    # Stage 1: exact normalized match
    for ep in rss_episodes:
        if _normalize_for_match(ep["title"]) == norm_target:
            return ep

    # Stage 2: episode number match
    if target_epnum:
        for ep in rss_episodes:
            n = _ep_number(ep["title"])
            if n and n == target_epnum:
                return ep

    # Stage 3: substring containment
    candidates = []
    for ep in rss_episodes:
        n = _normalize_for_match(ep["title"])
        if not n or not norm_target:
            continue
        if norm_target in n or n in norm_target:
            # Score by overlap length (longer = better)
            overlap = min(len(n), len(norm_target))
            candidates.append((overlap, ep))
    if candidates:
        candidates.sort(key=lambda x: -x[0])
        return candidates[0][1]

    # Stage 4: guest-name prefix (first 8 chars of normalized target)
    if len(norm_target) >= 8:
        prefix = norm_target[:8]
        for ep in rss_episodes:
            if prefix in _normalize_for_match(ep["title"]):
                return ep

    raise RuntimeError(
        f"No RSS episode matched Spotify title {spotify_title!r} "
        f"(searched {len(rss_episodes)} episodes; first 3: "
        f"{[e['title'][:60] for e in rss_episodes[:3]]})"
    )


# ─── Orchestration ───────────────────────────────────────────────────────────

def _resolve_show_to_rss(show_name: str, cache: dict[str, str]) -> str:
    if show_name not in cache:
        cache[show_name] = itunes_lookup_show_rss(show_name)
    return cache[show_name]


def _download_one(track: SpotifyTrack, output_dir: Path, podcast_name_override: str | None,
                  rss_cache: dict[str, str], rss_eps_cache: dict[str, list[dict]],
                  *, metadata_only: bool = False) -> Path | None:
    feed_url = _resolve_show_to_rss(track.show_name, rss_cache)
    if feed_url not in rss_eps_cache:
        # Use override if given (so directory name stays stable across sources),
        # else use the actual show name from Spotify.
        name = podcast_name_override or track.show_name
        rss_eps_cache[feed_url] = fetch_feed_episodes(feed_url, podcast_name=name)
    eps = rss_eps_cache[feed_url]

    if not track.episode_title:
        log.error("No episode title on track (show-only URL?): %s", track.spotify_uri)
        return None

    rss_ep = find_matching_episode(eps, track.episode_title)
    log.info("Matched: %r → RSS title %r", track.episode_title, rss_ep["title"])
    return download_rss_episode(rss_ep, output_dir, metadata_only=metadata_only)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Spotify URL → iTunes RSS → audio download",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("spotify_url", help="open.spotify.com/{episode,show,playlist}/<id>")
    parser.add_argument(
        "--output-dir",
        default="audios",
        help="Base output directory (default: audios). "
             "Episodes land in <output-dir>/<podcast_name>/<short_title>/",
    )
    parser.add_argument(
        "--podcast-name",
        default=None,
        help="Override directory name for the podcast (slug). "
             "Default: derived from Spotify show name. "
             "Use this to keep stable cross-source slugs (e.g. 'capital-allocators').",
    )
    parser.add_argument(
        "--resolve-only",
        action="store_true",
        help="Print resolved show + RSS URL + matched episode title, then exit",
    )
    parser.add_argument(
        "--no-transcribe",
        action="store_true",
        help="(no-op) Accepted for podcast-fetch contract parity",
    )
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Write README/official transcript but do not download audio",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="(show URLs only) Download the latest episode from RSS instead of "
             "the one Spotify happens to surface in the embed",
    )
    parser.add_argument(
        "--episode-index",
        type=int,
        default=None,
        help="(show URLs only) RSS index (0=newest)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="(show URLs only) Download every episode in the RSS feed",
    )
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s [%(name)s] %(message)s",
    )

    try:
        tracks = fetch_spotify_tracks(args.spotify_url)
    except (ValueError, RuntimeError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    rss_cache: dict[str, str] = {}
    rss_eps_cache: dict[str, list[dict]] = {}

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    kind, sid = _parse_spotify_url(args.spotify_url)

    # Show URL: use RSS-side selectors instead of fuzzy-matching the embed's
    # surfaced episode (--latest / --episode-index / --all).
    if kind == "show" and (args.latest or args.episode_index is not None or args.all):
        show_name = tracks[0].show_name
        feed_url = _resolve_show_to_rss(show_name, rss_cache)
        name = args.podcast_name or show_name
        eps = fetch_feed_episodes(feed_url, podcast_name=name)
        if not eps:
            print(f"[error] RSS feed has no episodes: {feed_url}", file=sys.stderr)
            return 1
        if args.resolve_only:
            print(f"show: {show_name}")
            print(f"feed_url: {feed_url}")
            print(f"episodes_in_feed: {len(eps)}")
            return 0
        if args.all:
            targets: Iterable[dict] = eps
        elif args.episode_index is not None:
            if not (0 <= args.episode_index < len(eps)):
                print(f"[error] episode-index out of range (0..{len(eps)-1})", file=sys.stderr)
                return 1
            targets = [eps[args.episode_index]]
        else:
            targets = [eps[0]]
        failed = 0
        for ep in targets:
            if download_rss_episode(ep, output_dir, metadata_only=args.metadata_only) is None:
                failed += 1
        return 1 if failed else 0

    # episode / playlist / show-without-RSS-selectors → iterate Spotify tracks
    if args.resolve_only:
        for t in tracks:
            try:
                feed_url = _resolve_show_to_rss(t.show_name, rss_cache)
            except RuntimeError as exc:
                print(f"[warn] {exc}")
                continue
            print(f"track: {t.episode_title}")
            print(f"  show: {t.show_name}")
            print(f"  feed_url: {feed_url}")
        return 0

    failed = 0
    for t in tracks:
        try:
            result = _download_one(
                t, output_dir, args.podcast_name, rss_cache, rss_eps_cache,
                metadata_only=args.metadata_only,
            )
        except RuntimeError as exc:
            print(f"[error] {t.episode_title!r}: {exc}", file=sys.stderr)
            failed += 1
            continue
        if result is None:
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
