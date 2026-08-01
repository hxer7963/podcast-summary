#!/usr/bin/env python3
"""Fetch YouTube/Bilibili subtitles and create a pipeline-ready transcript.

The script downloads subtitle tracks only. When a video has no usable track it
creates ``asr-required.json`` so a Linux GPU host can resume via video_fetch.py.
"""

from __future__ import annotations

import argparse
import html
import http.cookiejar
import json
import os
import platform as platform_module
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "audios" / "subtitles"

EXIT_OK = 0
EXIT_ASR_REQUIRED = 2
EXIT_AUTH = 3
EXIT_RUNTIME = 4
EXIT_PARTIAL = 5

SUBTITLE_EXTENSIONS = ("srt", "vtt", "json3")
AUTH_MARKERS = (
    "sign in to confirm",
    "subtitles are only available when logged in",
    "cookies are no longer valid",
)
RUNTIME_MARKERS = (
    "no supported javascript runtime",
    "challenge solving failed",
    "javascript challenge",
    "js runtime",
)


class FetchFailure(RuntimeError):
    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


def detect_platform(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if hostname == "youtu.be" or hostname == "youtube.com" or hostname.endswith(".youtube.com"):
        return "youtube"
    if hostname == "b23.tv" or hostname.endswith(".b23.tv"):
        return "bilibili"
    if hostname == "bilibili.com" or hostname.endswith(".bilibili.com"):
        if parsed.path.startswith("/video/"):
            return "bilibili"
    return "unknown"


def sanitize_filename(name: str, max_len: int = 80) -> str:
    name = re.sub(r"\s+", "-", (name or "").strip())
    name = re.sub(r"[^\w\u4e00-\u9fff\u3400-\u4dbf.-]", "", name)
    name = re.sub(r"-{2,}", "-", name).strip("-.")
    return (name or "unknown")[:max_len]


def _version_tuple(text: str) -> tuple[int, ...]:
    match = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", text)
    if not match:
        return ()
    return tuple(int(part or 0) for part in match.groups())


def _runtime_supported(kind: str, executable: str) -> bool:
    try:
        result = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    version = _version_tuple(result.stdout or result.stderr)
    minimum = (2, 3, 0) if kind == "deno" else (22, 0, 0)
    return bool(version) and version >= minimum


def resolve_js_runtime(kind: str = "auto", explicit_path: str | None = None) -> str:
    """Return yt-dlp's RUNTIME:PATH value or raise FetchFailure."""
    if explicit_path:
        path = str(Path(explicit_path).expanduser())
        inferred = kind
        if inferred == "auto":
            inferred = "node" if "node" in Path(path).name.lower() else "deno"
        if not _runtime_supported(inferred, path):
            minimum = "2.3" if inferred == "deno" else "22"
            raise FetchFailure(
                f"{inferred} runtime is missing or older than {minimum}: {path}",
                EXIT_RUNTIME,
            )
        return f"{inferred}:{path}"

    candidates: list[tuple[str, str]] = []
    if kind in ("auto", "deno"):
        deno = shutil.which("deno")
        if deno:
            candidates.append(("deno", deno))
        home_deno = Path.home() / ".deno" / "bin" / "deno"
        if home_deno.exists() and str(home_deno) not in {p for _, p in candidates}:
            candidates.append(("deno", str(home_deno)))
    if kind in ("auto", "node"):
        node = shutil.which("node")
        if node:
            candidates.append(("node", node))

    for runtime_kind, path in candidates:
        if _runtime_supported(runtime_kind, path):
            return f"{runtime_kind}:{path}"

    wanted = "Deno >=2.3 or Node >=22" if kind == "auto" else kind
    raise FetchFailure(f"No supported JavaScript runtime found ({wanted})", EXIT_RUNTIME)


def _convert_json_cookie_file(cookie_path: Path) -> Path | None:
    """Convert Cookie-Editor JSON to a private temporary Netscape file."""
    text = cookie_path.read_text(encoding="utf-8").strip()
    if text.startswith("#"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None

    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix="yt-dlp-cookies-", suffix=".txt", delete=False
    )
    try:
        os.chmod(handle.name, 0o600)
        handle.write("# Netscape HTTP Cookie File\n")
        for cookie in data:
            domain = str(cookie.get("domain", ""))
            fields = (
                domain,
                "TRUE" if domain.startswith(".") else "FALSE",
                str(cookie.get("path", "/")),
                "TRUE" if cookie.get("secure", False) else "FALSE",
                str(int(cookie.get("expirationDate", 0) or 0)),
                str(cookie.get("name", "")),
                str(cookie.get("value", "")),
            )
            handle.write("\t".join(fields) + "\n")
    finally:
        handle.close()
    return Path(handle.name)


def _standard_cookie_paths(platform_name: str) -> list[Path]:
    """Return platform cookie paths in XDG then ~/.config priority order."""
    filename = f"{platform_name}.txt"
    paths: list[Path] = []
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        paths.append(
            Path(xdg_config_home).expanduser()
            / "podcast-pipeline"
            / "cookies"
            / filename
        )
    home_default = Path.home() / ".config" / "podcast-pipeline" / "cookies" / filename
    if home_default not in paths:
        paths.append(home_default)
    return paths


def _validate_standard_cookie_file(path: Path) -> Path:
    """Validate a persistent auto-discovered Netscape cookie file."""
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise FetchFailure(f"Cannot access cookies file {path}: {exc}", EXIT_AUTH) from exc

    if path.is_symlink() or not stat.S_ISREG(file_stat.st_mode):
        raise FetchFailure(f"Cookies path must be a regular file: {path}", EXIT_AUTH)

    mode = stat.S_IMODE(file_stat.st_mode)
    if mode & ~0o600:
        raise FetchFailure(
            f"Cookies file permissions are too broad ({mode:04o}); expected 0600 or stricter: {path}",
            EXIT_AUTH,
        )

    try:
        with path.open(encoding="utf-8-sig") as handle:
            first_line = handle.readline().strip()
    except OSError as exc:
        raise FetchFailure(f"Cannot read cookies file {path}: {exc}", EXIT_AUTH) from exc
    if first_line not in {"# Netscape HTTP Cookie File", "# HTTP Cookie File"}:
        raise FetchFailure(f"Cookies file is not in Netscape format: {path}", EXIT_AUTH)

    try:
        cookie_jar = http.cookiejar.MozillaCookieJar(str(path))
        cookie_jar.load(ignore_discard=True, ignore_expires=True)
    except (OSError, http.cookiejar.LoadError) as exc:
        raise FetchFailure(f"Cookies file is not in Netscape format: {path}", EXIT_AUTH) from exc
    if not list(cookie_jar):
        raise FetchFailure(f"Cookies file contains no cookies: {path}", EXIT_AUTH)
    return path.resolve()


def prepare_cookie_args(
    platform_name: str,
    cookie_file: str | None = None,
    browser: str | None = None,
) -> tuple[list[str], Path | None]:
    """Build cookie arguments using explicit, env, config, then OS defaults."""
    temporary: Path | None = None
    if cookie_file:
        path = Path(cookie_file).expanduser().resolve()
        if not path.exists():
            raise FetchFailure(f"Cookies file not found: {path}", EXIT_AUTH)
        temporary = _convert_json_cookie_file(path)
        return ["--cookies", str(temporary or path)], temporary

    if browser:
        return ["--cookies-from-browser", browser], None

    env_name = "YOUTUBE_COOKIES_FILE" if platform_name == "youtube" else "BILIBILI_COOKIES_FILE"
    env_cookie = os.environ.get(env_name)
    if env_cookie:
        return prepare_cookie_args(platform_name, cookie_file=env_cookie)

    for standard_path in _standard_cookie_paths(platform_name):
        if standard_path.exists() or standard_path.is_symlink():
            path = _validate_standard_cookie_file(standard_path)
            return ["--cookies", str(path)], None

    if platform_module.system() == "Darwin":
        return ["--cookies-from-browser", "chrome"], None
    return [], None


def run_yt_dlp(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist", *args]
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


def common_yt_dlp_args(
    js_runtime: str | None,
    cookies_args: list[str],
    proxy: str | None,
) -> list[str]:
    args: list[str] = []
    if js_runtime:
        args += ["--js-runtimes", js_runtime]
    args += cookies_args
    if proxy:
        args += ["--proxy", proxy]
    return args


def classify_failure(stderr: str) -> int:
    lowered = (stderr or "").lower()
    if any(marker in lowered for marker in AUTH_MARKERS):
        return EXIT_AUTH
    if any(marker in lowered for marker in RUNTIME_MARKERS):
        return EXIT_RUNTIME
    return 1


def _parse_json_output(stdout: str) -> dict[str, Any] | None:
    for line in reversed((stdout or "").splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def fetch_metadata(
    url: str,
    js_runtime: str | None,
    cookies_args: list[str],
    proxy: str | None,
    load_subtitles: bool = True,
) -> dict[str, Any]:
    subtitle_args = ["--write-subs", "--write-auto-subs"] if load_subtitles else []
    result = run_yt_dlp(
        [
            *common_yt_dlp_args(js_runtime, cookies_args, proxy),
            "--dump-single-json",
            "--skip-download",
            *subtitle_args,
            url,
        ]
    )
    metadata = _parse_json_output(result.stdout)
    if result.returncode != 0 or metadata is None:
        message = (result.stderr or "metadata extraction failed").strip().splitlines()[-1]
        raise FetchFailure(message, classify_failure(result.stderr))
    lowered_stderr = (result.stderr or "").lower()
    if load_subtitles and any(marker in lowered_stderr for marker in AUTH_MARKERS):
        raise FetchFailure("Platform login cookies are missing or invalid", EXIT_AUTH)
    if js_runtime and "challenge solving failed" in (result.stderr or "").lower():
        raise FetchFailure("YouTube JavaScript challenge solving failed", EXIT_RUNTIME)
    return metadata


def _language_matches(candidate: str, requested: str) -> bool:
    return candidate == requested or candidate.lower().startswith(requested.lower() + "-")


def select_subtitle_track(
    metadata: dict[str, Any],
    requested_languages: list[str],
    allow_manual: bool = True,
    allow_auto: bool = True,
) -> tuple[str, str] | None:
    manual = metadata.get("subtitles") or {}
    automatic = metadata.get("automatic_captions") or {}

    def usable(source: dict[str, Any]) -> list[str]:
        return [key for key in source if key not in {"danmaku", "live_chat", "comments"}]

    language_order = requested_languages or ["zh-Hans", "zh-Hant", "zh", "ai-zh", "en"]
    if language_order == ["all"]:
        language_order = usable(manual) + usable(automatic)

    for requested in language_order:
        if allow_manual:
            for candidate in usable(manual):
                if _language_matches(candidate, requested):
                    track_type = "ai" if candidate.lower().startswith("ai-") else "manual"
                    return candidate, track_type
        if allow_auto:
            for candidate in usable(automatic):
                if _language_matches(candidate, requested):
                    return candidate, "automatic"
    return None


def _clean_caption_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _timestamp_seconds(parts: tuple[str, str, str, str]) -> float:
    hours, minutes, seconds, millis = (int(value or 0) for value in parts)
    scale = 1000 if len(parts[3]) == 3 else 10 ** len(parts[3])
    return hours * 3600 + minutes * 60 + seconds + millis / scale


TIMESTAMP_RE = re.compile(
    r"(?:(\d+):)?(\d{2}):(\d{2})[,.](\d{1,3})\s+-->\s+"
    r"(?:(\d+):)?(\d{2}):(\d{2})[,.](\d{1,3})"
)


def parse_timed_text(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json3":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        cues = []
        for event in data.get("events", []):
            text = _clean_caption_text("".join(seg.get("utf8", "") for seg in event.get("segs", [])))
            if not text:
                continue
            start = float(event.get("tStartMs", 0)) / 1000
            end = start + float(event.get("dDurationMs", 0)) / 1000
            cues.append({"start": start, "end": end, "text": text})
        return collapse_consecutive_duplicates(cues)

    text = path.read_text(encoding="utf-8-sig", errors="replace")
    cues: list[dict[str, Any]] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timestamp_index = next((index for index, line in enumerate(lines) if TIMESTAMP_RE.search(line)), None)
        if timestamp_index is None:
            continue
        match = TIMESTAMP_RE.search(lines[timestamp_index])
        assert match is not None
        values = match.groups()
        start = _timestamp_seconds((values[0] or "0", values[1], values[2], values[3]))
        end = _timestamp_seconds((values[4] or "0", values[5], values[6], values[7]))
        caption = _clean_caption_text(" ".join(lines[timestamp_index + 1 :]))
        if caption:
            cues.append({"start": start, "end": end, "text": caption})
    return collapse_consecutive_duplicates(cues)


def collapse_consecutive_duplicates(cues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop only adjacent exact duplicates; preserve later legitimate repeats."""
    collapsed: list[dict[str, Any]] = []
    for cue in cues:
        if collapsed and cue["text"] == collapsed[-1]["text"]:
            collapsed[-1]["end"] = max(collapsed[-1]["end"], cue["end"])
            continue
        collapsed.append(dict(cue))
    return collapsed


def analyze_completeness(cues: list[dict[str, Any]], duration: float | None) -> dict[str, Any]:
    text_chars = sum(len(cue["text"]) for cue in cues)
    first_start = cues[0]["start"] if cues else None
    last_end = cues[-1]["end"] if cues else None
    result: dict[str, Any] = {
        "cue_count": len(cues),
        "text_chars": text_chars,
        "first_start_seconds": first_start,
        "last_end_seconds": last_end,
        "duration_seconds": duration,
        "coverage_percent": None,
        "head_gap_seconds": first_start,
        "tail_gap_seconds": None,
        "complete": False,
    }
    if not cues or not duration or duration <= 0:
        return result
    tail_gap = max(0.0, duration - float(last_end))
    coverage = max(0.0, float(last_end) - float(first_start)) / duration * 100
    allowed_gap = max(60.0, duration * 0.05)
    result.update(
        {
            "coverage_percent": round(coverage, 2),
            "tail_gap_seconds": round(tail_gap, 3),
            "allowed_gap_seconds": round(allowed_gap, 3),
            "complete": (
                text_chars >= 100
                and coverage >= 90.0
                and float(first_start) <= allowed_gap
                and tail_gap <= allowed_gap
            ),
        }
    )
    return result


def _write_readme(output_dir: Path, metadata: dict[str, Any], platform_name: str, url: str) -> None:
    title = metadata.get("title") or "untitled"
    channel = metadata.get("channel") or metadata.get("uploader") or "unknown"
    upload_date = str(metadata.get("upload_date") or "")
    lines = [f"# {title}", "", f"> 来源：{platform_name} | 频道：{channel}"]
    if len(upload_date) == 8:
        lines.append(f"> 日期：{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}")
    if metadata.get("duration"):
        lines.append(f"> 时长：{float(metadata['duration']):.2f} 秒")
    lines.extend([f"> 链接：{url}", ""])
    output_dir.joinpath("README.md").write_text("\n".join(lines), encoding="utf-8")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _download_track(
    url: str,
    output_dir: Path,
    language: str,
    track_type: str,
    sub_format: str,
    convert_to: str | None,
    common_args: list[str],
) -> Path:
    args = [*common_args, "--skip-download", "--sub-langs", language, "--sub-format", sub_format]
    args.append("--write-auto-subs" if track_type == "automatic" else "--write-subs")
    if convert_to and convert_to != "txt":
        args += ["--convert-subs", convert_to]
    args += ["-o", str(output_dir / "%(id)s.%(ext)s"), url]
    result = run_yt_dlp(args)
    if result.returncode != 0:
        message = (result.stderr or "subtitle download failed").strip().splitlines()[-1]
        raise FetchFailure(message, classify_failure(result.stderr))

    preference = [convert_to] if convert_to in SUBTITLE_EXTENSIONS else []
    preference += [ext for ext in SUBTITLE_EXTENSIONS if ext not in preference]
    for extension in preference:
        matches = sorted(output_dir.glob(f"*.{language}.{extension}"))
        if matches:
            return matches[-1]
    raise FetchFailure(f"yt-dlp reported success but no subtitle file was created for {language}")


def _download_danmaku(url: str, output_dir: Path, common_args: list[str]) -> int:
    result = run_yt_dlp(
        [*common_args, "--skip-download", "--write-subs", "--sub-langs", "danmaku", "-o", str(output_dir / "%(id)s.%(ext)s"), url]
    )
    if result.returncode != 0:
        raise FetchFailure((result.stderr or "danmaku download failed").strip().splitlines()[-1])
    print(f"✓ Episode complete: {output_dir}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch a pipeline-ready YouTube/Bilibili transcript")
    parser.add_argument("url")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--langs", default="zh-Hans,zh-Hant,zh,ai-zh,en")
    parser.add_argument("--sub-format", default="srt/vtt/json3/best")
    parser.add_argument("--convert-to", choices=["srt", "vtt", "txt"], default=None)
    parser.add_argument("--no-auto-subs", action="store_true")
    parser.add_argument("--no-subs", action="store_true")
    parser.add_argument("--list-only", action="store_true")
    cookie_group = parser.add_mutually_exclusive_group()
    cookie_group.add_argument("--cookies")
    cookie_group.add_argument("--cookies-from-browser")
    parser.add_argument("--proxy")
    parser.add_argument("--no-proxy", action="store_true")
    parser.add_argument("--danmaku-only", action="store_true")
    parser.add_argument("--txt", action="store_true")
    parser.add_argument("--js-runtime", choices=["auto", "deno", "node"], default="auto")
    parser.add_argument("--js-runtime-path")
    parser.add_argument("--allow-partial", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    platform_name = detect_platform(args.url)
    if platform_name == "unknown":
        print(f"ERROR: unsupported video URL: {args.url}", file=sys.stderr)
        return 1
    if args.proxy and args.no_proxy:
        print("ERROR: --proxy and --no-proxy are mutually exclusive", file=sys.stderr)
        return 1

    temporary_cookie: Path | None = None
    try:
        js_runtime = None
        if platform_name == "youtube":
            js_runtime = resolve_js_runtime(args.js_runtime, args.js_runtime_path)
        cookie_args, temporary_cookie = prepare_cookie_args(
            platform_name, args.cookies, args.cookies_from_browser
        )
        proxy = None if args.no_proxy else args.proxy
        common_args = common_yt_dlp_args(js_runtime, cookie_args, proxy)

        if args.list_only:
            result = run_yt_dlp([*common_args, "--list-subs", args.url])
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
            return result.returncode if result.returncode == 0 else classify_failure(result.stderr)

        metadata = fetch_metadata(
            args.url, js_runtime, cookie_args, proxy, load_subtitles=not args.danmaku_only
        )
        title = metadata.get("title") or metadata.get("id") or "untitled"
        channel = metadata.get("channel") or metadata.get("uploader") or "unknown"
        output_dir = Path(args.output_dir) / platform_name / sanitize_filename(channel) / sanitize_filename(title)
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_readme(output_dir, metadata, platform_name, args.url)

        if args.danmaku_only:
            return _download_danmaku(args.url, output_dir, common_args)

        languages = [value.strip() for value in args.langs.split(",") if value.strip()]
        selected = select_subtitle_track(
            metadata,
            languages,
            allow_manual=not args.no_subs,
            allow_auto=not args.no_auto_subs,
        )
        base_status = {
            "source_url": args.url,
            "platform": platform_name,
            "video_id": metadata.get("id"),
            "title": title,
            "channel": channel,
        }
        if not selected:
            status = {
                **base_status,
                "result": "no_subtitles",
                "available_manual_languages": sorted((metadata.get("subtitles") or {}).keys()),
                "available_automatic_languages": sorted((metadata.get("automatic_captions") or {}).keys()),
            }
            _write_json(output_dir / "subtitle_status.json", status)
            _write_json(output_dir / "asr-required.json", {**status, "result": "asr_required", "reason": "no_subtitles"})
            print(f"⚠ ASR required: {output_dir}")
            return EXIT_ASR_REQUIRED

        language, track_type = selected
        subtitle_path = _download_track(
            args.url,
            output_dir,
            language,
            track_type,
            args.sub_format,
            args.convert_to,
            common_args,
        )
        cues = parse_timed_text(subtitle_path)
        analysis = analyze_completeness(cues, metadata.get("duration"))
        transcript_text = "\n".join(cue["text"] for cue in cues).strip() + "\n"
        output_dir.joinpath("transcript.md").write_text(transcript_text, encoding="utf-8")
        if args.txt or args.convert_to == "txt":
            output_dir.joinpath(f"{metadata.get('id', 'subtitle')}.{language}.txt").write_text(
                transcript_text, encoding="utf-8"
            )

        result_name = "complete" if analysis["complete"] else "partial"
        if result_name == "partial" and args.allow_partial:
            result_name = "partial_allowed"
        status = {
            **base_status,
            "result": result_name,
            "language": language,
            "track_type": track_type,
            "subtitle_file": subtitle_path.name,
            **analysis,
        }
        _write_json(output_dir / "subtitle_status.json", status)

        if not analysis["complete"] and not args.allow_partial:
            print(f"ERROR: subtitle coverage is incomplete: {output_dir}", file=sys.stderr)
            return EXIT_PARTIAL
        print(f"✓ Episode complete: {output_dir}")
        return EXIT_OK
    except FetchFailure as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return exc.exit_code
    except subprocess.TimeoutExpired:
        print("ERROR: yt-dlp timed out", file=sys.stderr)
        return 1
    finally:
        if temporary_cookie:
            temporary_cookie.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
