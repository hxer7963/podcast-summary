---
name: article-fetch
description: 从公开网页或 RSS/Atom 条目抓取正文，构建含 article.md 与 README.md 的本地 article_dir；不自动总结。用于“抓取文章”“保存网页正文”“拉取 newsletter/RSS 最新文章”“fetch this article”，或用户提供非微信公众号的公开文章 URL 要求建立本地知识库时。微信公众号 URL 必须改用 wechat-to-md；只有用户另外明确要求总结时才单独调用 podcast-summary。
---

# article-fetch

只抓取公开文章和元数据。不要自动总结、打标签、归档或发布。

## 路由

- `mp.weixin.qq.com`：停止并调用 `wechat-to-md`。
- 普通公开文章 URL：直接抓正文。
- RSS/Atom feed：使用 `--feed`，默认取最新条目，也可用 `--index` 选择。
- 登录墙、付费墙或需要浏览器执行 JavaScript：报告当前轻量路径无法取得正文，不安装浏览器绕过。

## 执行

先把 `<skill_dir>` 解析为本 `SKILL.md` 所在目录。

```bash
python3 <skill_dir>/scripts/article_fetch.py '<article_url>'
python3 <skill_dir>/scripts/article_fetch.py '<feed_url>' --feed
python3 <skill_dir>/scripts/article_fetch.py '<feed_url>' --feed --list-only
```

常用参数：`--source-name`、`--output-dir`、`--index`、`--force`、`--timeout`。

脚本以 Python 3.10+ 标准库为完整可运行基线。若环境已经有 `trafilatura`，普通网页会自动用它提高正文抽取质量；不要仅为一次抓取主动安装它。

## 输出契约

成功时打印 `✓ Article complete: <absolute_article_dir>`：

```text
article_dir/
├── article.md
└── README.md
```

正文只保存一份，不生成 `transcript.md` 镜像。已有非空 `article.md` 时保持幂等，除非用户明确要求 `--force`。

## 与 summary 组合

抓取请求默认到此结束。用户明确要求生成中文纪要时，再把 `article_dir` 交给 `podcast-summary`。
