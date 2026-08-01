#!/usr/bin/env python3
"""Download podcast from xiaoyuzhoufm.com.

Transcription is handled externally by vibevoice-asr via podcast_transcribe.sh.
"""

import argparse
import hashlib
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import requests
from bs4 import BeautifulSoup


class NextDataParser(HTMLParser):
    """Extract __NEXT_DATA__ JSON from HTML."""
    def __init__(self):
        super().__init__()
        self.capture = False
        self.data = ''

    def handle_starttag(self, tag, attrs):
        if tag == 'script' and dict(attrs).get('id') == '__NEXT_DATA__':
            self.capture = True

    def handle_data(self, data):
        if self.capture:
            self.data = data

    def handle_endtag(self, tag):
        if self.capture and tag == 'script':
            self.capture = False


def sanitize_filename(name):
    """Remove or replace invalid filename characters.
    
    Removes characters that cause problems in shell paths, git, or cross-platform
    filesystems: brackets, parens, braces, ampersands, punctuation, etc.
    Only keeps alphanumeric (incl. CJK), dashes, underscores, and dots.
    """
    # Replace whitespace runs with a single dash
    name = re.sub(r'\s+', '-', name.strip())
    # Remove all characters that are NOT: letters, digits, CJK, dash, underscore, dot
    name = re.sub(r'[^\w\u4e00-\u9fff\u3400-\u4dbf.-]', '', name)
    # Collapse consecutive dashes
    name = re.sub(r'-{2,}', '-', name)
    # Strip leading/trailing dashes and dots
    name = name.strip('-.')
    return name


def shorten_title(title, max_len=80, podcast_name=None):
    """Build a readable episode slug without cutting through the final word."""
    prefix = ''
    m = re.search(
        r'(?<![A-Za-z0-9])([Ee][Pp]?|[Vv][Oo][Ll])\s*\.?\s*(\d+)(?!\d)',
        title,
    )
    if m:
        letters = m.group(1).upper()
        digits = m.group(2)
        prefix = f"{letters}{digits}-"
        title = f"{title[:m.start()]} {title[m.end():]}"

    if podcast_name:
        title = re.sub(re.escape(podcast_name), " ", title, flags=re.IGNORECASE)

    body = sanitize_filename(title).strip("-._")
    slug = f"{prefix}{body}".strip("-")
    if len(slug) <= max_len:
        return slug

    shortened = slug[:max_len].rstrip("-")
    if "-" in shortened:
        shortened = shortened.rsplit("-", 1)[0]
    return shortened or slug[:max_len]


def extract_episode_data(html_content):
    """Extract episode metadata from __NEXT_DATA__ JSON on an episode page."""
    parser = NextDataParser()
    parser.feed(html_content)
    data = json.loads(parser.data)
    return data['props']['pageProps']['episode']


def extract_podcast_data(html_content):
    """Extract podcast metadata + initial episode list from a podcast homepage."""
    parser = NextDataParser()
    parser.feed(html_content)
    data = json.loads(parser.data)
    return data['props']['pageProps']['podcast']


def fetch_html(url, headers=None):
    """Fetch a URL and return response text."""
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text


def get_episode_ids_from_podcast(podcast_url):
    """
    Return a list of episode IDs from a podcast homepage.
    The public page only serves the latest ~15 episodes via SSG.
    We iterate using the ?before=<eid> cursor pattern on the podcast page
    until no new episodes are found.
    """
    seen_eids = set()
    episode_ids = []

    url = podcast_url
    while True:
        html = fetch_html(url)
        podcast = extract_podcast_data(html)
        episodes = podcast.get('episodes', [])
        if not episodes:
            break

        new_found = False
        for ep in episodes:
            eid = ep.get('eid')
            if eid and eid not in seen_eids:
                seen_eids.add(eid)
                episode_ids.append(eid)
                new_found = True

        if not new_found:
            break

        # Use the last episode's eid as cursor for the next page
        last_eid = episodes[-1].get('eid')
        if not last_eid:
            break

        # Try the ?before= pagination query
        base_url = podcast_url.split('?')[0]
        next_url = f"{base_url}?before={last_eid}"
        if next_url == url:
            break
        url = next_url

    return episode_ids


def file_hash(path: Path, length: int = 8) -> str:
    """Return first `length` hex chars of the MD5 of the file's contents."""
    md5 = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            md5.update(chunk)
    return md5.hexdigest()[:length]


def audio_filename(short_title: str, ext: str) -> str:
    """
    Build a human-readable audio filename: {PREFIX}-{hash8}.{ext}

    short_title is already in the form "E230-1万亿收入..." so we just
    extract the leading prefix part (letters+digits before the first '-').
    If no prefix is detected, fall back to 'audio'.

    The hash is computed after download, so this returns only the prefix part.
    The full name is assembled once the file exists.
    """
    m = re.match(r'^([A-Za-z]+\d+)', short_title)
    return m.group(1) if m else "audio"


def download_audio(url: str, output_path: Path) -> None:
    """Download audio file with progress indicator."""
    print(f"Downloading audio from {url}")
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    response = requests.get(url, stream=True, timeout=120, verify=False)
    response.raise_for_status()

    total_size = int(response.headers.get('content-length', 0))
    with open(output_path, 'wb') as f:
        downloaded = 0
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
            downloaded += len(chunk)
            if total_size:
                print(f"\rProgress: {downloaded/total_size*100:.1f}%", end='')
    print("\nDownload complete")




def process_episode(episode, base_output_dir, no_transcribe=False):
    """
    Download audio + save README for one episode dict.
    Returns the output directory path.

    Note: no_transcribe parameter kept for backward compatibility but
    transcription is now always handled externally by vibevoice-asr.
    """
    title = episode['title']
    podcast_name = sanitize_filename(episode['podcast']['title'])
    short_title = shorten_title(title)
    audio_url = episode['media']['source']['url']
    pub_date = episode.get('pubDate', '')
    shownotes = episode.get('shownotes') or episode.get('description', '')

    # Determine audio file extension from URL
    ext = 'm4a'
    url_path = audio_url.split('?')[0]
    if url_path.endswith('.mp3'):
        ext = 'mp3'
    elif url_path.endswith('.m4a'):
        ext = 'm4a'

    # Build output directory: base/{podcast_name}/{short_title}/
    output_dir = base_output_dir / podcast_name / short_title
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save README
    soup = BeautifulSoup(shownotes, 'html.parser')
    # Format publish date (ISO → YYYY-MM-DD)
    pub_date_str = ''
    if pub_date:
        pub_date_str = pub_date[:10]  # "2025-07-06T23:00:00.000Z" → "2025-07-06"
    readme_header = f"# {title}\n\n"
    if pub_date_str:
        readme_header += f"> 发布日期：{pub_date_str}\n"
    readme_header += f"> Audio URL: {audio_url}\n\n"
    readme_content = readme_header + soup.get_text(separator=chr(10))
    readme_path = output_dir / 'README.md'
    readme_path.write_text(readme_content, encoding='utf-8')
    print(f"README saved: {readme_path}")

    # Download audio
    # First download to a temp name, then rename to {prefix}-{hash8}.{ext}
    prefix = audio_filename(short_title, ext)

    # Check if a renamed file already exists (any file matching {prefix}-*.{ext})
    existing = list(output_dir.glob(f"{prefix}-*.{ext}"))
    if existing:
        audio_path = existing[0]
        print(f"Audio already exists: {audio_path.name}")
    else:
        tmp_path = output_dir / f"audio.{ext}"
        download_audio(audio_url, tmp_path)
        # Rename: {prefix}-{hash8}.{ext}
        h = file_hash(tmp_path)
        final_name = f"{prefix}-{h}.{ext}"
        audio_path = output_dir / final_name
        tmp_path.rename(audio_path)
        print(f"  Renamed → {final_name}")

    print(f"✓ Episode complete: {output_dir}")
    return output_dir


def main():
    parser = argparse.ArgumentParser(
        description='Download and transcribe xiaoyuzhoufm podcast episodes',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download a single episode
  python xiaoyuzhou_download.py https://www.xiaoyuzhoufm.com/episode/69c477e9abc3dc8807b8ca2d

  # Download all episodes from a podcast (latest ~15)
  python xiaoyuzhou_download.py https://www.xiaoyuzhoufm.com/podcast/5e5c52c9418a84a04625e6cc

  # Download all episodes matching a keyword
  python xiaoyuzhou_download.py https://www.xiaoyuzhoufm.com/podcast/5e5c52c9418a84a04625e6cc --keyword 英伟达

  # Download without transcribing
  python xiaoyuzhou_download.py https://www.xiaoyuzhoufm.com/episode/... --no-transcribe

  # Use a saved local HTML file
  python xiaoyuzhou_download.py --local page.html
        """
    )
    parser.add_argument('url', nargs='?', help='Episode or podcast URL')
    parser.add_argument('--local', help='Local HTML file path (episode page)')
    parser.add_argument('--keyword', help='Filter episodes by keyword (for podcast URLs)')
    parser.add_argument(
        '--output-dir',
        default='audios/xiaoyuzhou',
        help='Base output directory (default: audios/xiaoyuzhou)',
    )
    parser.add_argument(
        '--no-transcribe',
        action='store_true',
        help='Skip audio transcription',
    )
    parser.add_argument(
        '--list-only',
        action='store_true',
        help='Only list matching episodes, do not download',
    )
    args = parser.parse_args()

    base_output_dir = Path(args.output_dir)

    # ── Single local HTML file ────────────────────────────────────────────────
    if args.local:
        with open(args.local, 'r', encoding='utf-8') as f:
            html_content = f.read()
        episode = extract_episode_data(html_content)
        process_episode(episode, base_output_dir, no_transcribe=args.no_transcribe)
        return

    if not args.url:
        parser.error('Either url or --local must be provided')

    url = args.url.rstrip('/')

    # ── Episode URL ───────────────────────────────────────────────────────────
    if '/episode/' in url:
        html_content = fetch_html(url)
        episode = extract_episode_data(html_content)
        process_episode(episode, base_output_dir, no_transcribe=args.no_transcribe)
        return

    # ── Podcast (channel) URL ─────────────────────────────────────────────────
    if '/podcast/' in url:
        print(f"Fetching episode list from podcast: {url}")
        episode_ids = get_episode_ids_from_podcast(url)
        print(f"Found {len(episode_ids)} episodes")

        matched = []
        keyword = args.keyword.lower() if args.keyword else None

        for i, eid in enumerate(episode_ids, 1):
            ep_url = f"https://www.xiaoyuzhoufm.com/episode/{eid}"
            html = fetch_html(ep_url)
            episode = extract_episode_data(html)
            title = episode.get('title', '')

            if keyword and keyword not in title.lower():
                print(f"  [{i}/{len(episode_ids)}] Skip: {title}")
                continue

            print(f"  [{i}/{len(episode_ids)}] Match: {title}")
            matched.append(episode)

        print(f"\nMatched {len(matched)} episodes")

        if args.list_only:
            for ep in matched:
                print(f"  - {ep['title']}")
            return

        for i, episode in enumerate(matched, 1):
            print(f"\n[{i}/{len(matched)}] Processing: {episode['title']}")
            process_episode(episode, base_output_dir, no_transcribe=args.no_transcribe)

        print(f"\n✓ All done. Output dir: {base_output_dir}")
        return

    parser.error(f'Unrecognized URL format: {url}')


if __name__ == '__main__':
    main()
