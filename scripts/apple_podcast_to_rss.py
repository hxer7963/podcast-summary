#!/usr/bin/env python3
"""apple_podcast_to_rss.py — Apple Podcasts URL → RSS feed → delegate to rss_fetch.

Apple Podcasts is just a discoverability layer over standard RSS. iTunes Lookup
API exposes the underlying feedUrl, which we hand to rss_fetch.py.

Usage:
  # Resolve only (print feed URL and exit)
  python3 scripts/apple_podcast_to_rss.py <apple_url> --resolve-only

  # Resolve + fetch latest episode (delegates to rss_fetch.py)
  python3 scripts/apple_podcast_to_rss.py <apple_url> --latest \\
      --podcast-name acquired --output-dir audios

  # Resolve + fetch a specific episode (passes through --episode-id / --episode-index)
  python3 scripts/apple_podcast_to_rss.py <apple_url> --episode-index 0 \\
      --output-dir audios

Accepted URL forms:
  https://podcasts.apple.com/<country>/podcast/<slug>/id<id>
  https://podcasts.apple.com/podcast/id<id>
  Apple share URLs that contain "id<digits>" anywhere

Output:
  Same convention as rss_fetch: prints "✓ Episode complete: <ep_dir>" on success.
  Returns exit code 0 on success.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

_SCRIPTS_DIR = Path(__file__).parent
_LOOKUP_URL = "https://itunes.apple.com/lookup?id={pid}&entity=podcast"
_USER_AGENT = "Mozilla/5.0 (compatible; podcast-fetch/1.0)"


def extract_podcast_id(url: str) -> str:
    """Extract the numeric podcast id from any Apple Podcasts URL form.

    Raises ValueError if no id can be found.
    """
    parsed = urlparse(url)
    if parsed.hostname not in ("podcasts.apple.com", "music.apple.com"):
        raise ValueError(
            f"Not an Apple Podcasts URL (host={parsed.hostname!r}): {url}"
        )
    # The id appears as "id<digits>" in the path or query.
    match = re.search(r"id(\d+)", url)
    if not match:
        raise ValueError(f"Could not find podcast id in URL: {url}")
    return match.group(1)


def lookup_feed_url(podcast_id: str) -> str:
    """Call iTunes Lookup API and return the feedUrl field."""
    api_url = _LOOKUP_URL.format(pid=podcast_id)
    req = Request(api_url, headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(req, timeout=15) as resp:
            payload = json.load(resp)
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"iTunes Lookup API failed: {exc}") from exc

    results = payload.get("results", [])
    if not results:
        raise RuntimeError(
            f"iTunes Lookup returned no results for podcast id={podcast_id}"
        )
    feed_url = results[0].get("feedUrl")
    if not feed_url:
        raise RuntimeError(
            f"iTunes Lookup result has no feedUrl for podcast id={podcast_id}: "
            f"{results[0]}"
        )
    return feed_url


def delegate_to_rss_fetch(feed_url: str, passthrough_args: list[str]) -> int:
    """Spawn `python3 scripts/rss_fetch.py <feed_url> <args>` and stream stdout."""
    cmd = [
        sys.executable,
        str(_SCRIPTS_DIR / "rss_fetch.py"),
        feed_url,
        *passthrough_args,
    ]
    proc = subprocess.run(cmd, check=False)
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apple Podcasts URL → RSS feed → rss_fetch.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("apple_url", help="Apple Podcasts URL")
    parser.add_argument(
        "--resolve-only",
        action="store_true",
        help="Print resolved feed URL and exit (don't download)",
    )

    # Pass-through options forwarded to rss_fetch.py verbatim.
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--podcast-name", default=None)
    parser.add_argument("--list-only", action="store_true")
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--episode-id", default=None)
    parser.add_argument("--episode-index", type=int, default=None)
    parser.add_argument("--all", action="store_true")
    # Accept-but-ignore (parity with podcast-fetch contract).
    parser.add_argument(
        "--no-transcribe",
        action="store_true",
        help="(no-op) Accepted for podcast-fetch contract parity",
    )

    args = parser.parse_args()

    try:
        podcast_id = extract_podcast_id(args.apple_url)
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    try:
        feed_url = lookup_feed_url(podcast_id)
    except RuntimeError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    print(f"[info] Apple Podcasts id={podcast_id} → feed_url={feed_url}")

    if args.resolve_only:
        # Machine-readable line so callers can parse:
        print(f"feed_url: {feed_url}")
        return 0

    # Build pass-through args for rss_fetch.
    passthrough: list[str] = []
    if args.output_dir:
        passthrough += ["--output-dir", args.output_dir]
    if args.podcast_name:
        passthrough += ["--podcast-name", args.podcast_name]
    if args.list_only:
        passthrough += ["--list-only"]
    if args.latest:
        passthrough += ["--latest"]
    if args.episode_id:
        passthrough += ["--episode-id", args.episode_id]
    if args.episode_index is not None:
        passthrough += ["--episode-index", str(args.episode_index)]
    if args.all:
        passthrough += ["--all"]

    return delegate_to_rss_fetch(feed_url, passthrough)


if __name__ == "__main__":
    sys.exit(main())
