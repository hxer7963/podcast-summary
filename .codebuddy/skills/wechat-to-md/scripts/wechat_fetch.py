#!/usr/bin/env python3
"""Fetch one public WeChat article into article.md and README.md."""
from __future__ import annotations

import argparse
import html
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

UA = "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://mp.weixin.qq.com/",
}
BLOCKS = {"p", "div", "section", "blockquote", "table", "tr", "ul", "ol"}
SKIP_TAGS = {"script", "style", "noscript", "svg"}


def sanitize_filename(value: str, max_len: int = 80) -> str:
    value = re.sub(r"\s+", "-", value.strip())
    value = re.sub(r"[^\w\u3400-\u9fff-]", "", value)
    return re.sub(r"-{2,}", "-", value).strip("-")[:max_len] or "untitled"


def validate_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname != "mp.weixin.qq.com":
        raise ValueError("只支持 http(s)://mp.weixin.qq.com/... 的公开文章链接")


def fetch_html(url: str, timeout: float) -> str:
    request = urllib.request.Request(url, headers=HEADERS)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                text = response.read().decode(charset, "replace")
            if "访问过于频繁" in text or "环境异常" in text:
                raise RuntimeError("微信返回访问频繁或环境异常页面")
            return text
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1 + attempt * 2)
    raise RuntimeError(f"下载微信文章失败：{last_error}")


class WeChatParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.capture: str | None = None
        self.capture_depth = 0
        self.capture_parts: list[str] = []
        self.content_depth = 0
        self.content_seen = False
        self.skip_depth = 0
        self.parts: list[str] = []
        self.images: list[str] = []
        self.link_stack: list[str] = []

    @staticmethod
    def attrs(items: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key: value or "" for key, value in items}

    def handle_starttag(self, tag: str, items: list[tuple[str, str | None]]) -> None:
        attrs = self.attrs(items)
        classes = set(attrs.get("class", "").split())
        if tag == "meta":
            key = attrs.get("property") or attrs.get("name")
            if key and attrs.get("content"):
                self.meta[key.lower()] = attrs["content"].strip()

        if self.capture is not None:
            self.capture_depth += 1
        elif (
            (tag == "h1" and (attrs.get("id") == "activity-name" or "rich_media_title" in classes))
            or attrs.get("id") in {"js_name", "js_author_name", "js_biz_name", "publish_time"}
        ):
            target = attrs.get("id", "title")
            if target == "activity-name" or "rich_media_title" in classes:
                target = "title"
            self.capture, self.capture_depth, self.capture_parts = target, 1, []

        if self.content_depth == 0 and tag == "div" and (
            attrs.get("id") == "js_content" or "rich_media_content" in classes
        ):
            self.content_depth, self.content_seen = 1, True
            return
        if self.content_depth == 0:
            return
        self.content_depth += 1
        if tag in SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in BLOCKS:
            self.parts.append("\n\n")
        elif tag == "br":
            self.parts.append("\n")
        elif re.fullmatch(r"h[1-6]", tag):
            self.parts.append(f"\n\n{'#' * int(tag[1])} ")
        elif tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("*")
        elif tag == "code":
            self.parts.append("'")
        elif tag == "pre":
            self.parts.append("\n\n~~~\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag == "a":
            self.link_stack.append(attrs.get("href", ""))
            self.parts.append("[")
        elif tag == "img":
            src = attrs.get("data-src") or attrs.get("data-original") or attrs.get("src")
            if src and src.startswith(("http://", "https://")):
                index = len(self.images)
                self.images.append(html.unescape(src))
                self.parts.append(f"\n\n![]({{{{IMAGE_{index:03d}}}}})\n\n")

    def handle_endtag(self, tag: str) -> None:
        if self.capture is not None:
            self.capture_depth -= 1
            if self.capture_depth == 0:
                value = re.sub(r"\s+", " ", "".join(self.capture_parts)).strip()
                if value:
                    self.meta[self.capture] = value
                self.capture, self.capture_parts = None, []
        if self.content_depth == 0:
            return
        if self.content_depth == 1:
            self.content_depth = 0
            return
        if tag in SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
        elif not self.skip_depth:
            if tag in {"strong", "b"}:
                self.parts.append("**")
            elif tag in {"em", "i"}:
                self.parts.append("*")
            elif tag == "code":
                self.parts.append("'")
            elif tag == "pre":
                self.parts.append("\n~~~\n\n")
            elif tag == "a":
                href = self.link_stack.pop() if self.link_stack else ""
                self.parts.append(f"]({href})" if href.startswith(("http://", "https://")) else "]")
            elif tag in BLOCKS or re.fullmatch(r"h[1-6]", tag):
                self.parts.append("\n\n")
        self.content_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.capture is not None:
            self.capture_parts.append(data)
        if self.content_depth and not self.skip_depth:
            self.parts.append(data)

    def markdown(self) -> str:
        text = "".join(self.parts).replace("\xa0", " ")
        text = re.sub(r"[ \t]+\n", "\n", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_date(source: str, meta: dict[str, str]) -> str:
    match = re.search(r"(?:var\s+ct\s*=|\"create_time\"\s*:)\s*[\"']?(\d{9,11})", source)
    if match:
        try:
            return datetime.fromtimestamp(int(match.group(1)), tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            pass
    return meta.get("publish_time") or meta.get("article:published_time", "")[:10]


def extension(content_type: str, url: str) -> str:
    for needle, ext in (("jpeg", ".jpg"), ("png", ".png"), ("gif", ".gif"), ("webp", ".webp")):
        if needle in content_type.lower():
            return ext
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp"} else ".img"


def download_image(index: int, url: str, directory: Path, timeout: float) -> tuple[int, str | None]:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": HEADERS["Referer"]})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
            suffix = extension(response.headers.get_content_type(), url)
        target = directory / f"img_{index + 1:03d}{suffix}"
        target.write_bytes(data)
        return index, f"images/{target.name}"
    except (OSError, urllib.error.URLError, TimeoutError):
        return index, None


def localize(markdown: str, urls: list[str], directory: Path, timeout: float) -> tuple[str, int]:
    if not urls:
        return markdown, 0
    directory.mkdir(parents=True, exist_ok=True)
    replacements: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=min(5, len(urls))) as pool:
        futures = [pool.submit(download_image, i, url, directory, timeout) for i, url in enumerate(urls)]
        for future in as_completed(futures):
            index, path = future.result()
            replacements[index] = path or urls[index]
    for index, url in enumerate(urls):
        markdown = markdown.replace(f"{{{{IMAGE_{index:03d}}}}}", replacements.get(index, url))
    return markdown, sum(path.startswith("images/") for path in replacements.values())


def write_article(url: str, source: str, output: Path, category: str | None,
                  no_images: bool, force: bool, timeout: float) -> Path:
    parser = WeChatParser()
    parser.feed(source)
    body = parser.markdown()
    if not parser.content_seen:
        raise RuntimeError("页面中找不到微信公众号正文 #js_content")
    if len(re.sub(r"\s+", "", body)) < 20:
        raise RuntimeError("微信公众号正文为空或过短")
    title = parser.meta.get("title") or parser.meta.get("og:title") or "untitled"
    account = parser.meta.get("js_name", "")
    author = parser.meta.get("js_author_name") or parser.meta.get("js_biz_name", "")
    published = parse_date(source, parser.meta)
    article_dir = (output / sanitize_filename(category or account or "unknown-account", 50)
                   / sanitize_filename(title)).resolve()
    article_path = article_dir / "article.md"
    if article_path.is_file() and article_path.stat().st_size and not force:
        print(f"↷ Article exists: {article_path}")
        print(f"✓ Article complete: {article_dir}")
        return article_dir
    article_dir.mkdir(parents=True, exist_ok=True)
    if no_images:
        for index, remote in enumerate(parser.images):
            body = body.replace(f"{{{{IMAGE_{index:03d}}}}}", remote)
        downloaded = 0
    else:
        body, downloaded = localize(body, parser.images, article_dir / "images", timeout)
    article = [
        f"# {title}", "", f"> 公众号：{account or '未知'}", f"> 作者：{author or '未知'}",
        f"> 发布时间：{published or '未知'}", f"> 原文链接：{url}", "", "---", "", body, "",
    ]
    article_path.write_text("\n".join(article), encoding="utf-8")
    readme = [
        f"# {title}", "", "- 内容类型: 微信公众号文章", f"- 公众号: {account or '未知'}",
        f"- 作者: {author or '未知'}", f"- 发布日期: {published or '未知'}",
        f"- original_url: {url}", f"- images: {downloaded}/{len(parser.images)} downloaded", "",
    ]
    (article_dir / "README.md").write_text("\n".join(readme), encoding="utf-8")
    print(f"✓ Article complete: {article_dir}")
    return article_dir


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch one public WeChat article into article_dir")
    ap.add_argument("url")
    ap.add_argument("--output-dir", default="articles/wechat")
    ap.add_argument("--category")
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--timeout", type=float, default=20.0)
    args = ap.parse_args()
    try:
        validate_url(args.url)
        write_article(args.url, fetch_html(args.url, args.timeout), Path(args.output_dir),
                      args.category, args.no_images, args.force, args.timeout)
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
