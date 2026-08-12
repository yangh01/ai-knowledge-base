# skill: github-trending · 需求设计

> 依据 writing-for-agents 原则编写。本文件是生成 `.opencode/skills/github-trending/SKILL.md` 的唯一来源（Single Source of Truth）：改规则只改本文件，skill 重新生成，不允许直接改 skill。
> 阶段状态：Specify 完成 → 待生成 SKILL.md。

## 一句话目标

一个**模型自动触发**（model-invoked）的采集技能：抓取 GitHub Trending 当日热门仓库，过滤 AI/LLM/Agent/大模型相关，输出结构化 JSON，供 collector Agent 写入 `knowledge/raw/`，下游 analyzer 消费。

## 核心设计：description 字段（最关键）

### 为什么它决定成败

description 是技能最顶层的 **context pointer**，常驻上下文、每轮被模型读取，自动触发对错全由它决定。历史教训：泛写 `当前采集GitHub 热门开源项目时使用此技能`，用户说「帮我抓今天的热门 repo」时「热门」未命中触发条件，skill 不被启用，正确率仅约 40%。

### 写法原则

- **前置引导词**：以「热门 / 趋势」开头，锚定中文表达；同句携带 `trending`，锚定英文表达。
- **一个分支一个触发点**：按「表达家族」列分支，每个家族覆盖一组同义表达，不逐词穷举（穷举会膨胀 description）。
- **同义词折叠**：`热门/热榜/趋势/trending/Top` 同属「榜单家族」，只写一次。
- **无元描述**：不用「这是用于……的技能」式引导句，直接列出触发条件。
- **正面对照**：description 内不写「不要触发」类反例，反例只进验证表。

### 触发分支（N 种表达 → 4 个家族）

| 分支 | 表达家族 | 覆盖的用户表达示例 |
|------|----------|--------------------|
| B1 榜单 | GitHub + 热门/趋势/热榜/trending/Top/Star 最多 | 「抓 GitHub 今天的热门项目」「GitHub 趋势榜」「今日热榜 repo」「Top 仓库」 |
| B2 新项目 | 今日/最近/新 + 开源项目/仓库/repo/项目/框架 | 「今天新出的开源 repo」「最近有什么值得关注的项目」 |
| B3 主题 | AI/LLM/Agent/大模型/智能体/机器学习 + 项目/仓库 | 「有哪些新出的 AI 开源项目」「LLM/Agent 框架」「大模型仓库情报」 |
| B4 任务 | 采集/抓取/生成 + 采集数据/原始数据/情报（collector 流水线上下文） | 「生成今天的采集原始数据」「跑采集任务」 |

任一表达命中任一家族即触发。同一条表达可命中多分支（如「GitHub 今日 AI 热榜」命中 B1+B3）。

### 最终 description（SKILL.md 原样采用，禁止改动）

```yaml
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
```

### 触发测试表（生成 skill 后必须逐条自测）

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

验收标准：测试表命中率 ≥ 90%（7 个正例应全中，3 个反例应全不中）。

## 要做什么（步骤 + 完成标准）

### 步骤 1 · 抓取 Trending 页面
抓取 `https://github.com/trending?since=daily`，HTTP 请求带超时（≤10s）与重试（≤2 次，指数退避）。
- 完成标准：成功取得页面且含仓库条目；重试后仍失败 → 终止，输出空 `items`，不抛异常。

### 步骤 2 · 解析仓库条目
从 HTML 解析每条仓库：`name`（owner/repo）、`url`、`stars`、`topics`、`description`。
- 完成标准：每条保留条目 5 个字段全部非空；拒绝任何字段缺失的条目。

### 步骤 3 · 过滤 AI/LLM/Agent 相关
- 纳入：`name`/`topics`/`description` 命中关键词 `ai`、`llm`、`agent`、`ml`、`machine learning`、`deep learning`、`rag`、`gpt`、`大模型`、`智能体` 之一。
- 排除：`Awesome-*`/`awesome-*` 列表类、纯文档/课程搬运、营销/招聘性质。
- 完成标准：每条条目都能明确归入「纳入」或「排除」，无遗漏判定；仅纳入项进入下一步。

### 步骤 4 · 排序取 Top 15
按 `stars` 降序排列。
- 完成标准：输出 ≤ 15 条，且 `stars` 非递增。

### 步骤 5 · 输出 JSON
组装 `source` / `fetched_at` / `items`，写入 `knowledge/raw/github-trending-YYYY-MM-DD.json`（日期为采集当日）。
- 完成标准：文件可被 `json.load` 解析，且通过下述 jsonschema 校验。

**失败行为总则**：任一必做步骤失败 → 记录 `ERROR`/`WARNING` 日志（含失败原因），输出空 `items`，不抛异常。

## 不做什么

- 不调 GitHub API（rate limit 太紧）→ 走 HTML 解析。
- 不写数据库 → 只输出一个 JSON 文件。
- 不做去重 → 由 caller（organizer/analyzer 层）处理。
- 不撰写中文摘要 → 摘要属于 analyzer 职责（AGENTS.md 分层）。
- 不采集频率低于 30 分钟/次（AGENTS.md 红线）。

## 边界 & 验收

- 单次执行 < 10s。
- 失败时返回空 `items`，不抛异常。
- 输出必须通过 jsonschema 校验：

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

- 采集间隔 ≥ 30 分钟；遵守 github.com `robots.txt`。

## 怎么验证

1. **触发测试**：把触发测试表的每条表达问模型「该用哪个技能」，期望命中 github-trending，正例全中、反例全不中（≥ 90%）。
2. **运行测试**：生成 skill 后执行一次采集，检查输出为 JSON、字段完整、通过 jsonschema。
3. **失败注入**：模拟网络超时 → 输出空 `items` 且不抛异常，日志含原因。
4. **边界测试**：单次执行耗时 < 10s。
