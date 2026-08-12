# AGENTS.md — AI 知识库助手

## 项目概述

AI 知识库助手是一个自动化的技术情报聚合与分发系统。它定时从 GitHub Trending 和 Hacker News 等公开信息源抓取 AI/LLM/Agent 相关领域的技术动态，通过基于 LangGraph 构建的多 Agent 流水线进行智能分析、摘要总结和标签分类，最终将结构化知识条目以 JSON 格式存储，并经 Telegram / 飞书等渠道完成多渠道分发。

---

## 技术栈

| 层级 | 技术选型 | 说明 |
|------|----------|------|
| 运行时 | **Python 3.11** | 稳定的 CPython 版本，兼容所有依赖 |
| Agent 编排 | **OpenCode + 国产大模型** | OpenCode 作为开发入口与 Agent 运行时；国产大模型（如 DeepSeek / Qwen）提供推理能力 |
| 工作流引擎 | **LangGraph** | 构建采集→分析→整理→分发的有状态流水线 |
| 多渠道分发 | **OpenClaw** | 统一的 Bot 分发框架，对接 Telegram / 飞书 |

---

## 编码规范

- **语言与工具链**：Python 3.11，本项目纯 Python，不涉及 TypeScript。依赖管理使用 `uv`，提交 `uv.lock` 锁文件。
- **格式化与静态检查**：统一使用 `ruff`（不使用 black）。`ruff format` 格式化，行宽 **88** 字符；`ruff check` 做 lint（含 isort 导入排序）。
- **类型注解**：所有函数签名必须包含完整的类型注解，并通过 `mypy` **strict** 模式检查。
- **命名约定**：变量、函数、方法统一使用 `snake_case`；类名使用 `PascalCase`；常量使用 `UPPER_SNAKE_CASE`；异步函数使用 `async_` 前缀，`asyncio.run()` 仅出现在程序入口，禁止同步/异步混用。
- **枚举管理**：固定值（如 `source` / `status` / `importance` / 渠道名）一律定义 **Python Enum**，禁止裸字符串散落；JSON 序列化时再转为字符串。
- **Docstring**：所有公开函数/类必须编写 **Google 风格** docstring，包含 `Args`、`Returns`、`Raises` 段落。
- **日志规范**：使用标准 `logging` 模块输出日志，**绝对禁止裸 `print()`**。级别分级：`DEBUG` 调试、`INFO` 流水线节点、`WARNING` 可恢复异常、`ERROR` 阻断失败，日志需携带上下文字段。
- **网络请求**：HTTP 请求必须设置超时与重试；遵守目标网站 `robots.txt`，采集间隔不低于 30 分钟。
- **配置与密钥**：使用 `pydantic-settings` 从 `.env` 读取配置，启动时校验必填项，缺失立即报错；禁止硬编码任何密钥。
- **测试**：使用 `pytest`，行覆盖率 **≥ 80%**（pytest-cov），网络请求类代码可配置排除规则。
- **Git 规范**：commit message 遵循 **Conventional Commits**（`feat` / `fix` / `refactor` / `docs` 等前缀）。
- **CI 检查**：GitHub Actions 上执行 `ruff check` + `ruff format --check` + `mypy` + `pytest --cov`；覆盖率低于阈值即失败；**扫描 TODO 关键字，存在即失败**（TODO 禁止合入 main）。

```python
# 正确示例
def fetch_trending_repos(language: str = "python", since: str = "daily") -> list[dict]:
    """从 GitHub Trending 页面抓取热门仓库列表。

    Args:
        language: 编程语言过滤条件，默认为 python。
        since: 时间范围，可选 daily / weekly / monthly。

    Returns:
        仓库信息字典列表，每个字典包含 name、url、stars 等字段。

    Raises:
        RequestException: HTTP 请求失败时抛出。
    """
    ...
```

---

## 项目结构

```
ai-knowledge-base/
├── AGENTS.md                   # 本文件 — 项目级 Agent 行为规范
├── .opencode/
│   ├── agents/                 # Agent 角色定义（Markdown 文件）
│   │   ├── collector.md        #   采集 Agent
│   │   ├── analyzer.md         #   分析 Agent
│   │   └── organizer.md        #   整理 Agent
│   └── skills/                 # 可复用技能定义
│       ├── github-trending.md  #   GitHub Trending 采集技能
│       ├── hacker-news.md      #   Hacker News 采集技能
│       ├── summarizer.md       #   AI 摘要生成技能
│       └── distributor.md      #   多渠道分发技能
├── knowledge/
│   ├── raw/                    # 原始采集数据（JSON）
│   │   └── YYYY-MM-DD.json     #   按日期归档
│   └── articles/               # AI 分析后的结构化知识条目（JSON）
│       └── YYYY-MM-DD.json     #   按日期归档
├── src/                        # Python 源码（后续创建）
├── tests/                      # 测试代码（后续创建）
└── pyproject.toml              # 项目配置与依赖管理
```

---

## 知识条目 JSON 格式

每一条经过 AI 分析的知识条目遵循以下结构：

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "title": "OpenAI 发布 GPT-5 技术报告",
  "source": "hacker_news",
  "source_url": "https://news.ycombinator.com/item?id=12345678",
  "original_text": "原始内容的完整文本...",
  "summary": "OpenAI 于今日发布 GPT-5 技术报告，在推理能力、多模态理解和代码生成方面均有显著提升...",
  "summary_en": "OpenAI released the GPT-5 technical report today, showing significant improvements in reasoning, multimodal understanding, and code generation...",
  "tags": ["LLM", "OpenAI", "GPT-5", "模型发布"],
  "related_models": ["GPT-5", "GPT-4o"],
  "importance": "high",
  "status": "published",
  "fetched_at": "2026-07-19T08:30:00Z",
  "analyzed_at": "2026-07-19T08:35:00Z",
  "published_at": "2026-07-19T09:00:00Z",
  "distributed_to": ["telegram", "feishu"],
  "meta": {
    "upvotes": 342,
    "comments": 89,
    "stars": null
  }
}
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | `str` | ✅ | UUID v4 唯一标识 |
| `title` | `str` | ✅ | 知识条目标题 |
| `source` | `str` | ✅ | 信息来源：`github_trending` / `hacker_news` |
| `source_url` | `str` | ✅ | 原始链接 |
| `original_text` | `str` | ❌ | 原始内容全文 |
| `summary` | `str` | ✅ | 中文摘要（200-500 字） |
| `summary_en` | `str` | ❌ | 英文摘要（可选） |
| `tags` | `list[str]` | ✅ | 标签列表 |
| `related_models` | `list[str]` | ❌ | 涉及的 AI 模型 |
| `importance` | `str` | ✅ | 重要程度：`high` / `medium` / `low` |
| `status` | `str` | ✅ | 条目状态：`pending` / `analyzed` / `published` / `archived` |
| `fetched_at` | `str` | ✅ | ISO 8601 采集时间 |
| `analyzed_at` | `str` | ❌ | ISO 8601 分析完成时间 |
| `published_at` | `str` | ❌ | ISO 8601 分发时间 |
| `distributed_to` | `list[str]` | ❌ | 已分发渠道列表 |
| `meta` | `dict` | ❌ | 来源元数据（点赞数、评论数、Star 数等） |

---

## Agent 角色概览

| 角色 | Agent 名称 | 文件 | 职责 |
|------|-----------|------|------|
| 🔍 采集 | `collector` | `.opencode/agents/collector.md` | 定时从 GitHub Trending 和 Hacker News 抓取内容，过滤 AI/LLM/Agent 相关条目，存入 `knowledge/raw/` |
| 🧠 分析 | `analyzer` | `.opencode/agents/analyzer.md` | 读取 `knowledge/raw/` 中的原始数据，调用大模型生成中文摘要、提取标签、评估重要度，输出结构化 JSON 到 `knowledge/articles/` |
| 📦 整理 | `organizer` | `.opencode/agents/organizer.md` | 对已分析条目进行去重、关联、质量审核，将状态为 `published` 的条目通过 OpenClaw 分发到 Telegram / 飞书 |

### 流水线流程

```
GitHub Trending ──┐
                   ├──> collector ──> raw/ ──> analyzer ──> articles/ ──> organizer ──> Telegram/飞书
Hacker News ──────┘
```

---

## 红线（绝对禁止的操作）

1. **禁止提交 `raw/` 和 `articles/` 目录下的数据文件。** 这些是运行时产物，不得纳入版本控制。`.gitignore` 中必须排除 `knowledge/raw/` 和 `knowledge/articles/`。
2. **禁止在代码中硬编码 API Key、Webhook URL、Bot Token。** 所有密钥通过环境变量或 `.env` 文件注入，`.env` 文件不得提交。
3. **禁止在大模型 Prompt 中注入不可信的用户输入。** 所有 Prompt 模板必须预定义，变量通过参数化方式传入。
4. **禁止对同一来源进行高频爬取。** 两次采集间隔不得低于 30 分钟，遵守目标网站的 `robots.txt` 和使用条款。
5. **禁止修改 `knowledge/` 目录中已有条目的 `id`。** ID 一经生成即为不可变标识，状态变更仅更新 `status` 字段。
6. **禁止直接操作生产环境的 `knowledge/` 数据。** 所有变更必须经过 Agent 流水线，手动修正需通过专门的管理脚本并记录日志。
