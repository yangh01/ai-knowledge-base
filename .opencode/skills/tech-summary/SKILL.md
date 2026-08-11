---
name: tech-summary
description: 当需要对采集的技术内容进行深度分析总结时使用此技能
allowed-tools:
  - Read
  - Grep
  - Glob
  - WebFetch
---

# tech-summary — 技术内容深度分析总结技能

## 使用场景

当需要对已采集的技术内容（如 GitHub Trending 仓库、Hacker News 热帖）进行深度分析、总结和评分时，使用此技能。适用于分析 Agent 的整理任务，将原始采集数据转化为带有摘要、评分、标签和趋势洞察的结构化分析结论。

---

## 执行步骤

### 第 1 步：读取最新采集文件

- 使用 `Glob` / `Read` 读取 `knowledge/raw/` 目录下日期最新的采集文件
- 文件命名格式为 `YYYY-MM-DD.json` 或 `github-trending-YYYY-MM-DD.json` 等，优先选择日期最大（最新）的文件
- 若存在多个来源文件（GitHub Trending、Hacker News），需全部纳入分析

### 第 2 步：逐条深度分析

对每条技术内容独立完成以下四项分析：

- **摘要**：使用流畅中文概括核心内容，不超过 50 字，禁止臆测
- **技术亮点**：提炼 2-3 个关键事实性亮点，必须基于原文内容，禁止编造
- **评分**：按"评分标准"给出 1-10 分，并附上评分理由
- **标签建议**：给出 2-5 个简洁标签，覆盖技术领域、公司/组织、核心概念

### 第 3 步：趋势发现

- 汇总所有条目的共性与共性模式，识别本次采集内容的**共同主题**
- 标注值得关注的新概念、新方向或新名词，简要说明其含义与潜在影响

### 第 4 步：评分标准

| 分数 | 含义 | 说明 |
|------|------|------|
| 9-10 | 改变格局 | 可能显著影响 AI/LLM/Agent 领域技术路线或行业生态 |
| 7-8 | 直接有帮助 | 对当前开发或研究有直接实用价值 |
| 5-6 | 值得了解 | 有一定参考价值，值得跟踪 |
| 1-4 | 可略过 | 信息密度低或与主题关联弱 |

---

## 注意事项

- **用事实说话**：摘要与亮点必须严格基于原文，禁止虚构、夸大或推测
- **约束**：同一批次的 15 个项目中，评分 9-10 的条目**不得超过 2 个**，确保评分有区分度
- **不编造数据**：所有引用的数字、名称、链接均来自原始采集文件
- **只读操作**：本技能仅做分析与总结，不写入 `knowledge/` 目录

---

## 输出格式

```json
{
  "source": "tech_summary",
  "skill": "tech-summary",
  "analyzed_at": "2026-07-19T08:35:00Z",
  "source_files": ["2026-07-19.json"],
  "items": [
    {
      "title": "xxx",
      "summary": "不超过 50 字的中文摘要",
      "highlights": ["亮点 1（事实）", "亮点 2（事实）", "亮点 3（事实）"],
      "score": 8,
      "score_reason": "评分理由，说明为何给出该分数",
      "tags": ["LLM", "OpenAI", "模型发布"]
    }
  ],
  "trends": {
    "common_themes": ["本次内容共同主题 1", "本次内容共同主题 2"],
    "new_concepts": [
      {"name": "新概念名", "description": "简要说明"}
    ]
  }
}
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `source` | `str` | ✅ | 信息来源，固定为 `tech_summary` |
| `skill` | `str` | ✅ | 使用的技能名称，固定为 `tech-summary` |
| `analyzed_at` | `str` | ✅ | ISO 8601 分析完成时间 |
| `source_files` | `list[str]` | ✅ | 本次分析引用的原始采集文件名列表 |
| `items` | `list` | ✅ | 分析条目列表 |
| `items[].title` | `str` | ✅ | 条目标题 |
| `items[].summary` | `str` | ✅ | 中文摘要（不超过 50 字） |
| `items[].highlights` | `list[str]` | ✅ | 技术亮点（2-3 个，基于事实） |
| `items[].score` | `int` | ✅ | 评分（1-10 分） |
| `items[].score_reason` | `str` | ✅ | 评分理由 |
| `items[].tags` | `list[str]` | ❌ | 标签建议（2-5 个） |
| `trends` | `dict` | ❌ | 趋势发现 |
| `trends.common_themes` | `list[str]` | ❌ | 共同主题 |
| `trends.new_concepts` | `list[dict]` | ❌ | 新概念及简要说明 |
