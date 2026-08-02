---
name: wechat-to-md
description: 抓取单篇公开微信公众号文章并构建本地 Markdown 知识库条目，保存 article.md、README.md 和可选本地图片；不自动生成总结。当用户说“抓公众号文章”“保存微信文章”“拉取微信公众号正文”“fetch wechat article”，或提供 mp.weixin.qq.com 链接要求保存到本地知识库时使用。只有用户另外明确要求总结时，才在抓取完成后单独调用 podcast-summary。
---

# wechat-to-md

只执行微信公众号文章抓取。不要自动总结、打标签、归档、发布，也不要为了兼容播客而复制 `transcript.md`。

## 执行

使用 skill 自带的纯标准库脚本：先把 `<skill_dir>` 解析为本 `SKILL.md` 所在目录：

```bash
python3 <skill_dir>/scripts/wechat_fetch.py '<mp.weixin.qq.com URL>'
```

常用参数：

- `--output-dir DIR`：知识库根目录，默认 `articles/wechat`。
- `--category NAME`：把文章放入指定集合；默认使用公众号名称。
- `--no-images`：保留远程图片 URL，不下载图片。
- `--force`：覆盖已存在的条目。
- `--timeout SECONDS`：请求超时，默认 20 秒。

脚本只要求 Python 3.10+ 标准库，不激活虚拟环境，不安装包，不使用浏览器或 Cookie。

## 输出契约

成功时打印 `✓ Article complete: <absolute_article_dir>`，目录结构为：

```text
article_dir/
├── article.md
├── README.md
└── images/          # 仅文章含图片且未使用 --no-images
```

- `article.md` 是唯一正文真相源。
- `README.md` 保存标题、公众号、作者、发布日期、原始链接和图片状态。
- 已有非空 `article.md` 时保持幂等并直接返回；只有用户明确要求刷新才使用 `--force`。
- 页面要求登录、出现访问频繁/环境异常提示、找不到 `#js_content` 或正文为空时，报告失败，不伪造正文。

## 与 summary 组合

抓取请求默认到此结束。只有用户明确说“总结”“生成纪要”或等价表达时，才把返回的 `article_dir` 交给 `podcast-summary`；summary 直接读取 `article.md + README.md`。
