---
name: github-trending
description: 当前采集GitHub 热门开源项目时使用此技能
allowed-tools:
  - Read
  - Grep
  - Glob
  - WebFetch
---

# github-trending — GitHub 热门开源项目采集技能

## 使用场景

当需要从 GitHub 采集当日热门开源项目（AI/LLM/Agent 相关）并整理为结构化数据时，使用此技能。适用于采集 Agent 的 GitHub Trending 采集任务，输出可被下游分析 Agent 直接消费的 JSON 文件。

---

## 执行步骤

### 第 1 步：搜索热门仓库

通过 GitHub 官方 API 搜索当日热门仓库：

```
https://api.github.com/search/repositories?q=created:>YYYY-MM-DD&sort=stars&order=desc&per_page=100
```

- `YYYY-MM-DD` 使用**前一天**日期，确保覆盖完整采集窗口
- 若 API 受限或失败，可降级使用 `WebFetch` 抓取 `https://github.com/trending?since=daily` 页面作为兜底

### 第 2 步：提取信息

从搜索结果中提取每个仓库的以下字段：

- **name**：仓库全名（格式 `owner/repo`）
- **url**：仓库完整地址
- **stars**：Star 数
- **language**：主要编程语言
- **topics**：仓库主题标签列表

### 第 3 步：过滤

仅保留满足以下条件的仓库：

- **纳入**：主题与 AI、LLM、Agent、大模型、机器学习、深度学习、RAG、Agent 框架等相关
- **排除**：名称为 `Awesome-*` / `awesome-*` 列表类仓库，以及纯文档、纯课程搬运等低信息密度项目
- **排除**：广告、招聘、营销性质项目

### 第 4 步：去重

- 与 `knowledge/raw/` 中已有日期文件中同 `url` 或同 `name` 的条目做比对
- 已采集过的仓库跳过，不重复纳入

### 第 5 步：撰写中文摘要

为每条保留的仓库撰写中文摘要，遵循固定公式：

```
项目名 + 做什么 + 为什么值得关注
```

- **做什么**：基于 README 与 topics 提炼项目核心功能，禁止臆测
- **为什么值得关注**：点出项目在 AI/LLM/Agent 领域的定位、创新点或典型应用场景
- 摘要使用流畅中文撰写，不直接翻译英文原文，控制在 100-200 字

### 第 6 步：排序取 Top15

- 按 `stars` 从高到低降序排列
- 截取前 **15** 个仓库作为最终结果

### 第 7 步：输出 JSON

将结果按指定 JSON 结构写入 `knowledge/raw/github-trending-YYYY-MM-DD.json`，日期使用采集当日日期。

---

## 注意事项

- **遵守频率限制**：两次采集间隔不得低于 30 分钟，遵守 GitHub API 速率限制与使用条款
- **不编造数据**：`name`、`url`、`stars`、`language`、`topics` 必须来自实际搜索结果，禁止虚构
- **禁止硬编码密钥**：若需 GitHub Token，从环境变量注入，不得写入技能文件或代码
- **写入路径约定**：输出文件必须位于 `knowledge/raw/` 目录下，命名格式 `github-trending-YYYY-MM-DD.json`，不得更改
- **源标识**：输出中 `source` 固定为 `github_trending`，`skill` 固定为 `github-trending`，便于下游追溯

---

## 输出格式

```json
{
  "source": "github_trending",
  "skill": "github-trending",
  "collected_at": "2026-07-19T08:30:00Z",
  "items": [
    {
      "name": "owner/repo",
      "url": "https://github.com/owner/repo",
      "summary": "xxx：基于 ... 构建的 ...，在 ... 场景下表现突出，值得关注。",
      "stars": 1234,
      "language": "Python",
      "topics": ["llm", "agent", "rag"]
    }
  ]
}
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `source` | `str` | ✅ | 信息来源，固定为 `github_trending` |
| `skill` | `str` | ✅ | 使用的技能名称，固定为 `github-trending` |
| `collected_at` | `str` | ✅ | ISO 8601 采集时间 |
| `items` | `list` | ✅ | 仓库列表，最多 15 条 |
| `items[].name` | `str` | ✅ | 仓库全名（`owner/repo`） |
| `items[].url` | `str` | ✅ | 仓库完整地址 |
| `items[].summary` | `str` | ✅ | 中文摘要（100-200 字） |
| `items[].stars` | `int` | ✅ | Star 数 |
| `items[].language` | `str` | ❌ | 主要编程语言 |
| `items[].topics` | `list[str]` | ❌ | 仓库主题标签列表 |
