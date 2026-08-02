#!/usr/bin/env python3
"""Fetch a public webpage or RSS/Atom entry into article.md and README.md."""
from __future__ import annotations

import argparse
import email.utils
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

UA = "Mozilla/5.0 (compatible; podcast-summary-article-fetch/1.0)"
SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside"}


def sanitize(value: str, max_len: int = 80) -> str:
    value = re.sub(r"\s+", "-", value.strip())
    value = re.sub(r"[^\w\u3400-\u9fff-]", "", value)
    return re.sub(r"-{2,}", "-", value).strip("-")[:max_len] or "untitled"


def validate_public_url(url: str) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("需要完整的公开 http(s) URL")
    if parsed.hostname == "mp.weixin.qq.com":
        raise ValueError("微信公众号链接必须使用 wechat-to-md")
    return parsed


def fetch(url: str, timeout: float) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(), response.headers.get_content_type()


class MarkdownParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.in_title = False
        self.meta: dict[str, str] = {}
        self.parts: list[str] = []
        self.body_parts: list[str] = []
        self.content_depth = 0
        self.body_depth = 0
        self.skip_depth = 0
        self.link_stack: list[str] = []
        self.found_primary = False

    @staticmethod
    def attrs(items: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key: value or "" for key, value in items}

    def emit(self, value: str) -> None:
        if self.body_depth and not self.skip_depth:
            self.body_parts.append(value)
        if self.content_depth and not self.skip_depth:
            self.parts.append(value)

    def handle_starttag(self, tag: str, items: list[tuple[str, str | None]]) -> None:
        attrs = self.attrs(items)
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            if key and attrs.get("content"):
                self.meta[key] = attrs["content"].strip()
        if tag == "body":
            self.body_depth = 1
            return
        if self.body_depth:
            self.body_depth += 1
        if self.content_depth == 0 and tag in {"article", "main"}:
            self.content_depth = 1
            self.found_primary = True
        elif self.content_depth:
            self.content_depth += 1
        if tag in SKIP and (self.body_depth or self.content_depth):
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in {"p", "div", "section", "blockquote", "table", "tr", "ul", "ol"}:
            self.emit("\n\n")
        elif tag == "br":
            self.emit("\n")
        elif re.fullmatch(r"h[1-6]", tag):
            self.emit(f"\n\n{'#' * int(tag[1])} ")
        elif tag == "li":
            self.emit("\n- ")
        elif tag in {"strong", "b"}:
            self.emit("**")
        elif tag in {"em", "i"}:
            self.emit("*")
        elif tag == "pre":
            self.emit("\n\n~~~\n")
        elif tag == "a":
            self.link_stack.append(attrs.get("href", ""))
            self.emit("[")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        if tag in SKIP and self.skip_depth:
            self.skip_depth -= 1
        elif not self.skip_depth:
            if tag in {"strong", "b"}:
                self.emit("**")
            elif tag in {"em", "i"}:
                self.emit("*")
            elif tag == "pre":
                self.emit("\n~~~\n")
            elif tag == "a":
                href = self.link_stack.pop() if self.link_stack else ""
                self.emit(f"]({href})" if href.startswith(("http://", "https://")) else "]")
            elif tag in {"p", "div", "section", "blockquote", "table", "tr", "ul", "ol"} or re.fullmatch(r"h[1-6]", tag):
                self.emit("\n\n")
        if self.content_depth:
            self.content_depth -= 1
        if self.body_depth:
            self.body_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        self.emit(data)

    @staticmethod
    def clean(parts: list[str]) -> str:
        text = "".join(parts).replace("\xa0", " ")
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def result(self) -> tuple[str, str, str, str]:
        title = self.meta.get("og:title") or self.clean(self.title_parts)
        author = self.meta.get("author") or self.meta.get("article:author", "")
        date = self.meta.get("article:published_time", "")[:10] or self.meta.get("date", "")[:10]
        body = self.clean(self.parts if self.found_primary else self.body_parts)
        return title, author, date, body


@dataclass
class FeedEntry:
    title: str
    link: str
    author: str
    date: str
    body_html: str


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def child_text(node: ET.Element, names: set[str]) -> str:
    for child in node:
        if local_name(child.tag) in names:
            if local_name(child.tag) == "link" and child.attrib.get("href"):
                return child.attrib["href"].strip()
            return "".join(child.itertext()).strip()
    return ""


def feed_entries(data: bytes) -> list[FeedEntry]:
    root = ET.fromstring(data)
    candidates = [node for node in root.iter() if local_name(node.tag) in {"item", "entry"}]
    entries: list[FeedEntry] = []
    for node in candidates:
        title = child_text(node, {"title"}) or "untitled"
        link = child_text(node, {"link"})
        author = child_text(node, {"author", "creator"})
        date = child_text(node, {"published", "updated", "pubdate"})
        body = child_text(node, {"encoded", "content", "summary", "description"})
        entries.append(FeedEntry(title, link, author, date, body))
    return entries


def html_to_markdown(source: str) -> tuple[str, str, str, str]:
    parser = MarkdownParser()
    parser.feed(source)
    return parser.result()


def optional_trafilatura(source: str) -> tuple[str, str, str, str] | None:
    try:
        import trafilatura  # type: ignore
    except ImportError:
        return None
    body = trafilatura.extract(
        source, output_format="markdown", include_links=True,
        include_images=False, favor_recall=True, with_metadata=False,
    )
    if not body:
        return None
    metadata = trafilatura.extract_metadata(source)
    return (
        getattr(metadata, "title", "") if metadata else "",
        getattr(metadata, "author", "") if metadata else "",
        getattr(metadata, "date", "") if metadata else "",
        body.strip(),
    )


def normalized_date(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        return parsed.date().isoformat()
    except (TypeError, ValueError, OverflowError):
        match = re.search(r"\d{4}-\d{2}-\d{2}", value)
        return match.group(0) if match else value[:30]


def write_article(url: str, title: str, author: str, date: str, body: str,
                  source_name: str, output_dir: Path, force: bool) -> Path:
    if len(re.sub(r"\s+", "", body)) < 80:
        raise RuntimeError("抽取正文为空或过短；页面可能需要登录、付费或浏览器执行 JavaScript")
    article_dir = (output_dir / sanitize(source_name, 50) / sanitize(title)).resolve()
    article_path = article_dir / "article.md"
    if article_path.is_file() and article_path.stat().st_size and not force:
        print(f"↷ Article exists: {article_path}")
        print(f"✓ Article complete: {article_dir}")
        return article_dir
    article_dir.mkdir(parents=True, exist_ok=True)
    article = [
        f"# {title}", "", f"> 作者：{author or '未知'}", f"> 发布日期：{date or '未知'}",
        f"> 原文链接：{url}", "", "---", "", body.strip(), "",
    ]
    article_path.write_text("\n".join(article), encoding="utf-8")
    readme = [
        f"# {title}", "", "- 内容类型: 公开文章", f"- 作者: {author or '未知'}",
        f"- 发布日期: {date or '未知'}", f"- source: {source_name}",
        f"- original_url: {url}", "",
    ]
    (article_dir / "README.md").write_text("\n".join(readme), encoding="utf-8")
    print(f"✓ Article complete: {article_dir}")
    return article_dir


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch a public webpage or RSS/Atom entry")
    ap.add_argument("url")
    ap.add_argument("--feed", action="store_true")
    ap.add_argument("--list-only", action="store_true")
    ap.add_argument("--index", type=int, default=0)
    ap.add_argument("--source-name")
    ap.add_argument("--output-dir", default="articles")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--timeout", type=float, default=20.0)
    args = ap.parse_args()
    try:
        parsed = validate_public_url(args.url)
        data, _ = fetch(args.url, args.timeout)
        source_name = args.source_name or parsed.hostname.removeprefix("www.")
        if args.feed:
            entries = feed_entries(data)
            if not entries:
                raise RuntimeError("RSS/Atom 中没有可用条目")
            if args.list_only:
                for index, entry in enumerate(entries):
                    print(f"[{index}] {entry.title}\n    {entry.link}")
                return 0
            if args.index < 0 or args.index >= len(entries):
                raise ValueError(f"--index 超出范围：0..{len(entries) - 1}")
            entry = entries[args.index]
            body = html_to_markdown(entry.body_html)[3] if entry.body_html else ""
            if len(re.sub(r"\s+", "", body)) < 80 and entry.link:
                validate_public_url(entry.link)
                page, _ = fetch(entry.link, args.timeout)
                source = page.decode("utf-8", "replace")
                title, author, date, body = optional_trafilatura(source) or html_to_markdown(source)
                entry = FeedEntry(title or entry.title, entry.link, author or entry.author, date or entry.date, body)
            write_article(entry.link or args.url, entry.title, entry.author,
                          normalized_date(entry.date), body, source_name,
                          Path(args.output_dir), args.force)
        else:
            source = data.decode("utf-8", "replace")
            title, author, date, body = optional_trafilatura(source) or html_to_markdown(source)
            write_article(args.url, title or parsed.path.rsplit("/", 1)[-1] or parsed.hostname,
                          author, normalized_date(date), body, source_name,
                          Path(args.output_dir), args.force)
        return 0
    except (ValueError, RuntimeError, OSError, ET.ParseError, urllib.error.URLError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
