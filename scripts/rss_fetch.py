#!/usr/bin/env python3
"""rss_fetch.py — CLI wrapper around rss_download.py for the podcast-fetch route table.

Provides a uniform "URL in → episode_dir out" interface matching xiaoyuzhou_download.py.

Usage:
  # List episodes from a feed (no download)
  python3 scripts/rss_fetch.py <feed_url> --list-only

  # Download the latest episode
  python3 scripts/rss_fetch.py <feed_url> --latest \
      --output-dir audios/<source-or-language-bucket>

  # Download a specific episode by id (entry.id from feed) or by index
  python3 scripts/rss_fetch.py <feed_url> --episode-id <eid> --output-dir <dir>
  python3 scripts/rss_fetch.py <feed_url> --episode-index 0  --output-dir <dir>

  # Override podcast directory name (for stable cross-source slugs)
  python3 scripts/rss_fetch.py <feed_url> --latest --podcast-name acquired \
      --output-dir audios

  # Download all episodes (rare; use sparingly)
  python3 scripts/rss_fetch.py <feed_url> --all --output-dir <dir>

Output:
  Prints "✓ Episode complete: <ep_dir>" per successful download (matching
  xiaoyuzhou_download.py convention). Returns exit code 0 on success.

Note: episode-id is the GUID/link reported by the feed; use --list-only first
      to discover it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPTS_DIR))

from rss_download import (  # noqa: E402
    download_rss_episode,
    fetch_feed_episodes,
)


def _derive_podcast_name(feed_url: str) -> str:
    """Best-effort name derivation for episodes that lack a podcast_name."""
    from urllib.parse import urlparse

    host = urlparse(feed_url).hostname or "rss"
    return host.replace("www.", "")


def _load_episodes(feed_url: str, podcast_name: str | None = None) -> list[dict]:
    name = podcast_name or _derive_podcast_name(feed_url)
    eps = fetch_feed_episodes(feed_url, podcast_name=name)
    if not eps:
        print(f"[error] No audio episodes found in feed: {feed_url}", file=sys.stderr)
        sys.exit(1)
    return eps


def _print_list(episodes: list[dict]) -> None:
    print(f"# Episodes ({len(episodes)})\n")
    for i, ep in enumerate(episodes):
        print(f"[{i}] {ep['title']}")
        print(f"    id:       {ep['eid']}")
        print(f"    pubDate:  {ep['pubDate']}")
        print(f"    audio:    {ep['audio_url']}")
        print()


def _select(episodes: list[dict], *, latest: bool, eid: str | None, index: int | None) -> list[dict]:
    if latest:
        return episodes[:1]
    if eid is not None:
        match = [ep for ep in episodes if ep["eid"] == eid]
        if not match:
            print(f"[error] No episode with id={eid!r} in feed", file=sys.stderr)
            sys.exit(1)
        return match
    if index is not None:
        if not (0 <= index < len(episodes)):
            print(f"[error] index {index} out of range (0..{len(episodes)-1})", file=sys.stderr)
            sys.exit(1)
        return [episodes[index]]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(
        description="RSS podcast fetcher (CLI wrapper around rss_download.py)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("feed_url", help="RSS feed URL")
    parser.add_argument(
        "--output-dir",
        default="audios/rss",
        help="Base output directory (default: audios/rss). "
             "Episodes land in <output-dir>/<podcast_name>/<short_title>/.",
    )
    parser.add_argument(
        "--podcast-name",
        default=None,
        help="Override the directory name for this podcast. "
             "Default: derived from feed URL hostname (e.g. feeds.transistor.fm). "
             "Set this for stable cross-source slugs (e.g. 'acquired').",
    )
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Write README/official transcript but do not download audio",
    )

    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--list-only", action="store_true", help="Print episode list and exit")
    selector.add_argument("--latest", action="store_true", help="Download only the most recent episode")
    selector.add_argument("--episode-id", help="Download episode with this id (GUID/link)")
    selector.add_argument("--episode-index", type=int, help="Download episode at this index (0=newest)")
    selector.add_argument("--all", action="store_true", help="Download every episode in the feed")

    args = parser.parse_args()

    episodes = _load_episodes(args.feed_url, podcast_name=args.podcast_name)

    if args.list_only or not (args.latest or args.episode_id or args.all or args.episode_index is not None):
        _print_list(episodes)
        # Default behavior with no selector: list (mirrors xiaoyuzhou_download.py podcast-URL behavior)
        return 0

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    targets = episodes if args.all else _select(
        episodes, latest=args.latest, eid=args.episode_id, index=args.episode_index,
    )

    failed = 0
    for ep in targets:
        result = download_rss_episode(ep, output_dir, metadata_only=args.metadata_only)
        if result is None:
            failed += 1

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
