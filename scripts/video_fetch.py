#!/usr/bin/env python3
"""Download YouTube/Bilibili audio for the Linux GPU ASR fallback path."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

try:
    from subtitle_fetch import (
        EXIT_AUTH,
        EXIT_RUNTIME,
        FetchFailure,
        common_yt_dlp_args,
        detect_platform,
        fetch_metadata,
        prepare_cookie_args,
        resolve_js_runtime,
        run_yt_dlp,
        sanitize_filename,
    )
except ImportError:  # imported as scripts.video_fetch in tests
    from scripts.subtitle_fetch import (
        EXIT_AUTH,
        EXIT_RUNTIME,
        FetchFailure,
        common_yt_dlp_args,
        detect_platform,
        fetch_metadata,
        prepare_cookie_args,
        resolve_js_runtime,
        run_yt_dlp,
        sanitize_filename,
    )


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read_handoff(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FetchFailure(f"Cannot read ASR handoff {path}: {exc}") from exc
    if not data.get("source_url"):
        raise FetchFailure(f"ASR handoff has no source_url: {path}")
    return data


def _write_readme(output_dir: Path, metadata: dict, platform_name: str, url: str) -> None:
    title = metadata.get("title") or "untitled"
    channel = metadata.get("channel") or metadata.get("uploader") or "unknown"
    upload_date = str(metadata.get("upload_date") or "")
    lines = [f"# {title}", "", f"> 来源：{platform_name} | 频道：{channel}"]
    if len(upload_date) == 8:
        lines.append(f"> 发布日期：{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}")
    if metadata.get("duration"):
        lines.append(f"> 时长：{float(metadata['duration']):.2f} 秒")
    lines.extend([f"> 链接：{url}", "", str(metadata.get("description") or "").strip()])
    output_dir.joinpath("README.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download video audio for GPU ASR")
    parser.add_argument("url", nargs="?")
    parser.add_argument("--handoff", help="Path to asr-required.json generated on macOS")
    parser.add_argument("--output-dir")
    parser.add_argument("--podcast-name")
    parser.add_argument("--no-transcribe", action="store_true", help="Compatibility flag")
    cookie_group = parser.add_mutually_exclusive_group()
    cookie_group.add_argument("--cookies")
    cookie_group.add_argument("--cookies-from-browser")
    parser.add_argument("--proxy")
    parser.add_argument("--no-proxy", action="store_true")
    parser.add_argument("--js-runtime", choices=["auto", "deno", "node"], default="auto")
    parser.add_argument("--js-runtime-path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.proxy and args.no_proxy:
        print("ERROR: --proxy and --no-proxy are mutually exclusive", file=sys.stderr)
        return 1

    handoff_path = Path(args.handoff).expanduser().resolve() if args.handoff else None
    handoff = _read_handoff(handoff_path) if handoff_path else {}
    url = args.url or handoff.get("source_url")
    if not url:
        print("ERROR: provide a URL or --handoff", file=sys.stderr)
        return 1
    platform_name = detect_platform(url)
    if platform_name == "unknown":
        print(f"ERROR: unsupported video URL: {url}", file=sys.stderr)
        return 1
    if not shutil.which("ffmpeg"):
        print("ERROR: ffmpeg is required for the audio ASR fallback", file=sys.stderr)
        return 1

    temporary_cookie = None
    try:
        js_runtime = None
        if platform_name == "youtube":
            js_runtime = resolve_js_runtime(args.js_runtime, args.js_runtime_path)
        cookie_args, temporary_cookie = prepare_cookie_args(
            platform_name, args.cookies, args.cookies_from_browser
        )
        proxy = None if args.no_proxy else args.proxy
        common_args = common_yt_dlp_args(js_runtime, cookie_args, proxy)
        metadata = fetch_metadata(url, js_runtime, cookie_args, proxy, load_subtitles=False)

        title = metadata.get("title") or metadata.get("id") or "untitled"
        channel = args.podcast_name or metadata.get("channel") or metadata.get("uploader") or "unknown"
        if handoff_path and not args.output_dir:
            episode_dir = handoff_path.parent
        else:
            base = Path(args.output_dir) if args.output_dir else PROJECT_ROOT / "audios" / "video"
            episode_dir = base / platform_name / sanitize_filename(channel) / sanitize_filename(title)
        episode_dir.mkdir(parents=True, exist_ok=True)
        _write_readme(episode_dir, metadata, platform_name, url)

        existing = sorted(episode_dir.glob("audio-*.m4a"))
        if not existing:
            result = run_yt_dlp(
                [
                    *common_args,
                    "--extract-audio",
                    "--audio-format",
                    "m4a",
                    "--audio-quality",
                    "0",
                    "--format",
                    "bestaudio/best",
                    "-o",
                    str(episode_dir / "audio-%(id)s.%(ext)s"),
                    url,
                ],
                timeout=3600,
            )
            if result.returncode != 0:
                message = (result.stderr or "audio download failed").strip().splitlines()[-1]
                raise FetchFailure(message)

        print(f"✓ Episode complete: {episode_dir}")
        return 0
    except FetchFailure as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return exc.exit_code
    finally:
        if temporary_cookie:
            temporary_cookie.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
