import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


wechat = load_module(
    "wechat_fetch_skill",
    ".codebuddy/skills/wechat-to-md/scripts/wechat_fetch.py",
)
article = load_module(
    "article_fetch_skill",
    ".codebuddy/skills/article-fetch/scripts/article_fetch.py",
)


class WeChatFetchTests(unittest.TestCase):
    def test_parser_and_article_contract(self):
        source = """
        <html><head><meta property="og:title" content="备用标题"></head><body>
        <h1 id="activity-name">测试文章</h1>
        <a id="js_name">测试公众号</a>
        <span id="js_author_name">作者甲</span>
        <script>var ct = "1767225600";</script>
        <div id="js_content">
          <p>这是用于验证微信公众号正文抽取的一段完整文字，长度足够通过基本质量检查。</p>
          <p><strong>重要判断</strong>以及<a href="https://example.com/source">来源链接</a>。</p>
          <img data-src="https://example.com/image.png">
        </div></body></html>
        """
        with tempfile.TemporaryDirectory() as directory:
            result = wechat.write_article(
                "https://mp.weixin.qq.com/s/test",
                source,
                Path(directory),
                None,
                True,
                False,
                1.0,
            )
            self.assertTrue(result.is_absolute())
            self.assertTrue((result / "article.md").is_file())
            self.assertTrue((result / "README.md").is_file())
            self.assertFalse((result / "transcript.md").exists())
            body = (result / "article.md").read_text(encoding="utf-8")
            self.assertIn("# 测试文章", body)
            self.assertIn("测试公众号", body)
            self.assertIn("https://example.com/image.png", body)


class ArticleFetchTests(unittest.TestCase):
    def test_html_parser_prefers_article_body(self):
        source = """
        <html><head><title>公开文章标题</title>
        <meta name="author" content="Author A">
        <meta property="article:published_time" content="2026-08-01T10:00:00Z">
        </head><body><nav>导航噪音</nav><article>
        <h1>公开文章标题</h1>
        <p>这是文章正文，包含足够的信息用于验证轻量解析器会优先读取 article 标签。</p>
        <p>第二段包含 <strong>关键结论</strong> 和更多上下文。</p>
        </article><footer>页脚噪音</footer></body></html>
        """
        title, author, date, body = article.html_to_markdown(source)
        self.assertEqual(title, "公开文章标题")
        self.assertEqual(author, "Author A")
        self.assertEqual(date, "2026-08-01")
        self.assertIn("关键结论", body)
        self.assertNotIn("导航噪音", body)
        self.assertNotIn("页脚噪音", body)

    def test_rss_parser_reads_entries(self):
        data = b"""<?xml version="1.0" encoding="utf-8"?>
        <rss version="2.0"><channel><item>
          <title>RSS Article</title>
          <link>https://example.com/rss-article</link>
          <author>Writer</author>
          <pubDate>Sat, 01 Aug 2026 10:00:00 GMT</pubDate>
          <description><![CDATA[<p>This is the complete RSS article body with enough useful content.</p>]]></description>
        </item></channel></rss>"""
        entries = article.feed_entries(data)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].title, "RSS Article")
        self.assertEqual(entries[0].link, "https://example.com/rss-article")
        self.assertIn("complete RSS article", entries[0].body_html)


class PortabilityTests(unittest.TestCase):
    def test_podhood_channel_rejects_host_injection(self):
        script = ROOT / ".codebuddy/skills/podhood-fetch/scripts/podhood_fetch.py"
        completed = subprocess.run(
            [sys.executable, str(script), "--channel", "evil.example.com", "--list-facets"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("--channel", completed.stderr)

    def test_three_agent_discovery_paths_resolve_to_same_skill(self):
        canonical = (ROOT / ".codebuddy/skills/wechat-to-md/SKILL.md").resolve()
        self.assertEqual((ROOT / ".agents/skills/wechat-to-md/SKILL.md").resolve(), canonical)
        self.assertEqual((ROOT / ".claude/skills/wechat-to-md/SKILL.md").resolve(), canonical)


if __name__ == "__main__":
    unittest.main()
