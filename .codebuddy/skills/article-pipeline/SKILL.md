---
name: article-pipeline
description: 在用户明确要求“抓取并总结文章”“处理这篇文章并生成纪要”或等价端到端任务时，薄编排 article-fetch/wechat-to-md 与 podcast-summary。普通抓取、保存知识库或只给文章 URL 时不要触发本 skill，应只调用对应 fetch skill；本流水线不做标签、归档或发布。
---

# article-pipeline

只在用户同时明确要求抓取和总结时执行：

```text
公开文章 URL ─┬─ mp.weixin.qq.com → wechat-to-md ─┐
              └─ 其他公开网页/RSS → article-fetch ─┤
                                                   └→ podcast-summary
```

## 执行规则

1. 根据 URL 调用一个 fetch skill，取得 stdout 中的绝对 `article_dir`。
2. 验证 `article_dir/article.md` 与 `article_dir/README.md` 均为非空文件。
3. 调用 `podcast-summary`，让它读取 `article.md + README.md`。
4. 输出中文深度纪要后停止；不生成标签、不归档、不发布。

若用户只说“抓取”“保存”“构建知识库”，第 1 步完成后停止。不得擅自把抓取请求升级成总结任务。

保持薄编排：不要复制 fetch 逻辑，不安装当前 URL 不需要的能力，不创建 `transcript.md` 镜像。
