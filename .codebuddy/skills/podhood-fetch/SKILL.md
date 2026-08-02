---
name: podhood-fetch
description: 从 PodHood 公开 REST API 搜索、筛选并下载完整播客转录，输出 transcript.md、README.md 和 source.json；不自动总结。支持频道、话题、人物、公司/产品、年份、日期与语义搜索。当用户说“抓取十分吸引转录”“下载 PodHood 文稿”“搜索 PodHood 话题”“fetch podhood transcript”，或提供 *.podhood.com 链接/频道 slug 要求拿完整文稿时使用。
---

# podhood-fetch

只负责公开文稿与元数据抓取。脚本使用 Python 3.10+ 标准库，无需 API key、虚拟环境或第三方包。

## 执行

先把 `<skill_dir>` 解析为本 `SKILL.md` 所在目录。

```bash
python3 <skill_dir>/scripts/podhood_fetch.py \
  --channel shifenxiyin --limit 5
```

筛选参数包括 `--topic`、`--person`、`--entity`、`--year`、`--since`、`--query`、`--list-only`、`--list-facets`、`--with-timestamps`、`--save-json`、`--refresh` 和 `--output-dir`。

## 输出契约

每集成功时打印 `✓ Episode complete: <absolute_episode_dir>`：

```text
episode_dir/
├── README.md
├── transcript.md
├── source.json
└── transcript.json     # 仅 --save-json
```

已有 `transcript.md` 时默认跳过，适合增量运行。只允许小写字母、数字和连字符组成频道 slug；不要把任意主机名拼入请求。

用户只要求文稿时到此结束；明确要求总结时才调用 `podcast-summary`。
