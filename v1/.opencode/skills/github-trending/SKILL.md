---
name: github-trending
description: 当需要采集 GitHub 当日热门/趋势开源项目（trending、热榜、Top 仓库、新开源项目），过滤出 AI、LLM、Agent、大模型相关的仓库，并输出结构化 JSON 采集数据时使用此技能。触发表达示例："抓 GitHub 今天的热门项目"、"GitHub 趋势榜上有什么"、"有哪些新出的 AI 开源 repo"、"生成今日采集原始数据"。
allowed-tools:
  - Read
  - Write
  - Glob
  - WebFetch
  - Bash
---

# github-trending — GitHub 热门开源项目采集技能

采集 GitHub Trending 当日热门仓库，过滤 AI/LLM/Agent/大模型相关，输出结构化 JSON 到 `knowledge/raw/`。本技能由 `specs/github-trending-skill.md` 生成，改规则改 spec、不动本文件。

## 执行步骤

### 第 1 步：抓取 Trending 页面

用 `WebFetch` 抓取 `https://github.com/trending?since=daily`，请求带超时（≤10s）与重试（≤2 次，指数退避）。

- 检查完成：页面成功取得且含仓库条目。
- 重试后仍失败 → 终止，输出空 `items`，不抛异常。

### 第 2 步：解析仓库条目

从 HTML 解析每条仓库，提取 5 个字段：

- **name**：仓库全名（格式 `owner/repo`）
- **url**：仓库完整地址
- **stars**：Star 数
- **topics**：仓库主题标签列表
- **description**：项目简介

检查完成：每条保留条目 5 个字段全部非空；拒绝任何字段缺失的条目。

### 第 3 步：过滤 AI/LLM/Agent 相关

- **纳入**：`name`/`topics`/`description` 命中关键词 `ai`、`llm`、`agent`、`ml`、`machine learning`、`deep learning`、`rag`、`gpt`、`大模型`、`智能体` 之一。
- **排除**：`Awesome-*` / `awesome-*` 列表类、纯文档/课程搬运、营销/招聘性质项目。

检查完成：每条条目都能明确归入「纳入」或「排除」，无遗漏判定；仅纳入项进入下一步。

### 第 4 步：排序取 Top 15

按 `stars` 降序排列，截取前 15 条。

检查完成：输出 ≤ 15 条，且 `stars` 非递增。

### 第 5 步：输出 JSON

组装 `source` / `fetched_at` / `items`，写入 `knowledge/raw/github-trending-YYYY-MM-DD.json`（日期为采集当日）。

检查完成：文件可被 `json.load` 解析，且通过下方 jsonschema 校验。

**失败行为总则**：任一必做步骤失败 → 记录 `ERROR`/`WARNING` 日志（含失败原因），输出空 `items`，不抛异常。

## 注意事项

- **遵守频率限制**：两次采集间隔不得低于 30 分钟，遵守 `github.com/robots.txt` 与使用条款。
- **走 HTML 解析**：不调 GitHub API（rate limit 太紧）。
- **不编造数据**：`name`、`url`、`stars`、`topics`、`description` 必须来自实际解析结果，禁止虚构。
- **不撰写中文摘要**：摘要是 analyzer 职责，本技能只输出原始字段。
- **不做去重**：由 caller（organizer/analyzer 层）处理。
- **禁止硬编码密钥**：如需凭证，从环境变量注入，不得写入技能文件。
- **源标识**：输出中 `source` 固定为 `github_trending`，便于下游追溯。

## 输出格式

```json
{
  "source": "github_trending",
  "fetched_at": "2026-08-11T08:30:00Z",
  "items": [
    {
      "name": "owner/repo",
      "url": "https://github.com/owner/repo",
      "stars": 1234,
      "topics": ["llm", "agent", "rag"],
      "description": "项目简介"
    }
  ]
}
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `source` | `str` | ✅ | 信息来源，固定为 `github_trending` |
| `fetched_at` | `str` | ✅ | ISO 8601 采集时间 |
| `items` | `list` | ✅ | 仓库列表，最多 15 条 |
| `items[].name` | `str` | ✅ | 仓库全名（`owner/repo`） |
| `items[].url` | `str` | ✅ | 仓库完整地址 |
| `items[].stars` | `int` | ✅ | Star 数 |
| `items[].topics` | `list[str]` | ✅ | 仓库主题标签列表 |
| `items[].description` | `str` | ✅ | 项目简介 |

### jsonschema 校验

```json
{
  "type": "object",
  "required": ["source", "fetched_at", "items"],
  "properties": {
    "source": { "const": "github_trending" },
    "fetched_at": { "type": "string", "format": "date-time" },
    "items": {
      "type": "array",
      "maxItems": 15,
      "items": {
        "type": "object",
        "required": ["name", "url", "stars", "topics", "description"],
        "properties": {
          "name": { "type": "string" },
          "url": { "type": "string" },
          "stars": { "type": "integer", "minimum": 0 },
          "topics": { "type": "array", "items": { "type": "string" } },
          "description": { "type": "string" }
        }
      }
    }
  }
}
```

### 触发测试表（生成后自测）

| 用户表达 | 期望触发 | 命中分支 |
|----------|----------|----------|
| 帮我抓一下 GitHub 今天的热门项目 | ✅ | B1 |
| GitHub 趋势榜上有什么 AI 项目 | ✅ | B1+B3 |
| 看看今天新出的开源 repo | ✅ | B2 |
| 有哪些值得关注的 LLM/Agent 框架 | ✅ | B3 |
| 生成今天的采集原始数据 | ✅ | B4 |
| What's trending on GitHub today | ✅ | B1（英文） |
| 帮我找找某库的用法 | ❌ | — |
| 抓一下微信公众号的技术文章 | ❌ → weixin-tech | — |
| 总结一下这份技术文档 | ❌ → tech-summary | — |
