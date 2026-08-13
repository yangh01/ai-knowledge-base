#!/usr/bin/env python3
"""四步知识库自动化流水线：采集 → 分析 → 整理 → 保存。

Step 1 采集（Collect）：从 GitHub Search API 与 RSS 源采集 AI 相关内容。
Step 2 分析（Analyze）：调用 LLM（model_client）对每条内容做摘要/评分/标签。
Step 3 整理（Organize）：去重 + 格式标准化 + 必填字段校验。
Step 4 保存（Save）：将文章写入 knowledge/articles/ 下的独立 JSON 文件。

采集原始数据先落盘到 knowledge/raw/，最终文章写入 knowledge/articles/。

用法:
    python pipeline/pipeline.py --sources github,rss --limit 20
    python pipeline/pipeline.py --sources github --limit 5
    python pipeline/pipeline.py --sources rss --limit 10
    python pipeline/pipeline.py --sources github --limit 5 --dry-run
    python pipeline/pipeline.py --step 1               # 只执行 Step 1 采集
    python pipeline/pipeline.py --step 1,2             # 执行 Step 1-2 采集+分析
    python pipeline/pipeline.py --step 3,4             # 基于已落盘数据执行整理+保存
    python pipeline/pipeline.py --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml
from dotenv import load_dotenv
from model_client import chat_with_retry, create_provider, tracker

# 显式加载 pipeline/ 目录下的 .env，避免依赖当前工作目录。
PIPELINE_DIR = Path(__file__).resolve().parent
load_dotenv(PIPELINE_DIR / ".env")

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "knowledge" / "raw"
ARTICLES_DIR = PROJECT_ROOT / "knowledge" / "articles"

GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
GITHUB_SEARCH_QUERY = "ai OR llm OR agent in:name,description,topics"
RSS_SOURCES_FILE = PIPELINE_DIR / "rss_sources.yaml"

PIPELINE_STEPS = frozenset({1, 2, 3, 4})
STATE_DIR = PIPELINE_DIR / ".state"


def _load_rss_feeds() -> tuple[str, ...]:
    """从 rss_sources.yaml 读取启用的 RSS/Atom 源地址。

    Returns:
        所有 enabled 为 true 的源 url 元组，保持 YAML 中的声明顺序。

    Raises:
        FileNotFoundError: 配置文件不存在时抛出。
        yaml.YAMLError: YAML 解析失败时抛出。
        ValueError: 配置缺少 sources 字段或条目缺少 url 时抛出。
    """
    if not RSS_SOURCES_FILE.exists():
        raise FileNotFoundError(f"RSS 配置文件不存在: {RSS_SOURCES_FILE}")
    data = yaml.safe_load(RSS_SOURCES_FILE.read_text(encoding="utf-8")) or {}
    sources = data.get("sources")
    if not isinstance(sources, list):
        raise TypeError(f"{RSS_SOURCES_FILE} 缺少 sources 列表")
    feeds: list[str] = []
    for source in sources:
        if not isinstance(source, dict) or not source.get("enabled", False):
            continue
        url = source.get("url")
        if not isinstance(url, str) or not url:
            raise ValueError(f"{RSS_SOURCES_FILE} 中启用的源缺少 url: {source}")
        feeds.append(url)
    if not feeds:
        logger.warning("RSS 配置中没有启用的数据源: %s", RSS_SOURCES_FILE)
    return tuple(feeds)


RSS_FEEDS: tuple[str, ...] = _load_rss_feeds()

USER_AGENT = "ai-knowledge-base-pipeline/0.1"
HTTP_TIMEOUT = 30.0

# 项目知识条目规范仅接受 github_trending / hacker_news 两类 source，
# 将流水线采集源映射到规范值，保证校验通过。
SOURCE_MAP: dict[str, str] = {"github": "github_trending", "rss": "hacker_news"}

ANALYSIS_SYSTEM_PROMPT = (
    "你是一个严谨的 AI 技术内容分析专家。你只输出合法 JSON 对象，"
    "不要输出任何解释性文字或 markdown 代码块。"
)

ANALYSIS_USER_PROMPT_TEMPLATE = """请分析以下技术内容，并输出一个 JSON 对象：

标题：{title}
来源链接：{url}
原始内容：{original_text}

输出字段：
- title: 优化后的中文标题（字符串）
- summary: 200-500 字中文摘要，客观概括核心信息
- summary_en: 英文摘要（字符串）
- tags: 3-5 个标签（字符串数组）
- importance: high / medium / low 三选一
- score: 1-10 的整数，代表技术价值

只输出 JSON 对象本身，不要带其他内容。"""


def _utc_now() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _http_client() -> httpx.Client:
    """创建带超时与 UA 的同步 HTTP 客户端。

    Returns:
        配置好的 httpx.Client 实例，需由调用方负责关闭。
    """
    return httpx.Client(
        timeout=HTTP_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


def _extract_tag(block: str, tag: str) -> str:
    """用正则从 RSS 条目块中提取指定标签的文本。

    Args:
        block: 单个 <item> 的原始 XML 文本。
        tag: 标签名（如 title / link / description）。

    Returns:
        标签内文本，去除 CDATA 包裹与首尾空白；未找到返回空字符串。
    """
    match = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, re.DOTALL)
    if not match:
        return ""
    text = match.group(1).strip()
    text = re.sub(r"^<!\[CDATA\[|\]\]>$", "", text).strip()
    return re.sub(r"<[^>]+>", "", text).strip()


def collect_github(client: httpx.Client, limit: int) -> list[dict[str, Any]]:
    """从 GitHub Search API 采集 AI 相关热门仓库。

    Args:
        client: 共享的 HTTP 客户端。
        limit: 最多采集的仓库数量。

    Returns:
        原始条目字典列表，每条含 title / url / source / popularity / summary。

    Raises:
        httpx.HTTPStatusError: GitHub API 返回非 2xx 状态码时抛出。
    """
    headers: dict[str, str] = {}
    token = os.getenv("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    params = {
        "q": GITHUB_SEARCH_QUERY,
        "sort": "stars",
        "order": "desc",
        "per_page": limit,
    }
    response = client.get(GITHUB_SEARCH_URL, params=params, headers=headers)
    response.raise_for_status()
    data = response.json()

    items: list[dict[str, Any]] = []
    for repo in data.get("items", [])[:limit]:
        items.append(
            {
                "title": repo.get("full_name", ""),
                "url": repo.get("html_url", ""),
                "source": "github",
                "popularity": {"stars": repo.get("stargazers_count", 0)},
                "summary": (repo.get("description") or "")[:300],
            }
        )
    logger.info("GitHub 采集完成: %d 条", len(items))
    return items


def collect_rss(client: httpx.Client, limit: int) -> list[dict[str, Any]]:
    """从预设 RSS/Atom 源采集 AI 相关内容（简易正则解析）。

    Args:
        client: 共享的 HTTP 客户端。
        limit: 最多采集的条目数量（按剩余配额在源间分配）。

    Returns:
        原始条目字典列表，每条含 title / url / source / summary。
    """
    items: list[dict[str, Any]] = []
    feeds = list(RSS_FEEDS)
    total_feeds = len(feeds)
    remaining = limit

    for index, feed in enumerate(feeds):
        if remaining <= 0:
            break
        # 按剩余配额在剩余源间均分（向上取整），某源抓取不足时由后续源补足。
        per_feed = max(1, -(-remaining // (total_feeds - index)))
        try:
            response = client.get(feed)
            response.raise_for_status()
            entries = _parse_feed(response.text)
            take = min(len(entries), per_feed, remaining)
            for entry in entries[:take]:
                items.append(
                    {
                        "title": entry["title"],
                        "url": entry["link"],
                        "source": "rss",
                        "popularity": {},
                        "summary": entry["description"][:300],
                    }
                )
            remaining -= take
        except (
            httpx.HTTPStatusError,
            httpx.ConnectError,
            httpx.TimeoutException,
        ) as exc:
            logger.warning("RSS 源抓取失败 %s: %s", feed, exc)

    logger.info("RSS 采集完成: %d 条", len(items))
    return items


def _extract_link(block: str) -> str:
    """提取条目块中的链接。

    兼容两种写法：RSS 的 <link>文本</link>，以及 Atom 的
    <link href="..."/> 自闭合标签。

    Args:
        block: 单条条目（<item> 或 <entry>）的原始 XML 文本。

    Returns:
        提取到的链接字符串；未找到返回空字符串。
    """
    text = _extract_tag(block, "link")
    if text:
        return text
    match = re.search(r'<link[^>]*href=["\']([^"\']+)["\']', block)
    return match.group(1).strip() if match else ""


def _parse_feed(xml_text: str) -> list[dict[str, str]]:
    """用正则解析 RSS 2.0 与 Atom 两种 XML 格式。

    Args:
        xml_text: 完整 feed XML 文本。

    Returns:
        条目字典列表，每条含 title / link / description / pubDate。
    """
    results: list[dict[str, str]] = []
    for block in re.findall(r"<item>(.*?)</item>", xml_text, re.DOTALL):
        entry = {
            "title": _extract_tag(block, "title"),
            "link": _extract_tag(block, "link"),
            "description": _extract_tag(block, "description"),
            "pubDate": _extract_tag(block, "pubDate"),
        }
        if entry["title"] and entry["link"]:
            results.append(entry)

    for block in re.findall(r"<entry>(.*?)</entry>", xml_text, re.DOTALL):
        entry = {
            "title": _extract_tag(block, "title"),
            "link": _extract_link(block),
            "description": _extract_tag(block, "summary")
            or _extract_tag(block, "content"),
            "pubDate": _extract_tag(block, "published")
            or _extract_tag(block, "updated"),
        }
        if entry["title"] and entry["link"]:
            results.append(entry)
    return results


def collect_sources(sources: list[str], limit: int) -> list[dict[str, Any]]:
    """按指定来源执行采集，返回合并后的原始条目。

    Args:
        sources: 采集源列表（github / rss）。
        limit: 总采集条数上限。

    Returns:
        合并去重后的原始条目列表。

    Raises:
        httpx.HTTPStatusError: GitHub API 返回非 2xx 状态码时抛出。
    """
    client = _http_client()
    items: list[dict[str, Any]] = []
    try:
        # 将总 limit 均分到各采集源，避免先执行的源独占全部配额，
        # 导致后执行的源（如 rss）采集的数据被 [:limit] 截断丢弃。
        base_budget = limit // len(sources)
        for index, source in enumerate(sources):
            if index == len(sources) - 1:
                budget = limit - base_budget * (len(sources) - 1)
            else:
                budget = base_budget
            if source == "github":
                items.extend(collect_github(client, budget))
            elif source == "rss":
                items.extend(collect_rss(client, budget))
    finally:
        client.close()

    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        url = item.get("url", "")
        if url and url in seen:
            continue
        seen.add(url)
        deduped.append(item)
    return deduped[:limit]


def analyze_item(
    provider: Any,
    item: dict[str, Any],
) -> dict[str, Any] | None:
    """调用 LLM 对单条原始内容做摘要/评分/标签分析。

    Args:
        provider: 来自 model_client.create_provider 的 LLM 提供商实例。
        item: 原始条目字典。

    Returns:
        分析结果字典（含 title / summary / tags / importance / score 等），
        分析失败时返回 None。
    """
    messages = [
        {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": ANALYSIS_USER_PROMPT_TEMPLATE.format(
                title=item.get("title", ""),
                url=item.get("url", ""),
                original_text=item.get("summary", ""),
            ),
        },
    ]
    try:
        response = chat_with_retry(provider, messages, temperature=0.3)
        data = _parse_llm_json(response.content)
    except (
        ValueError,
        json.JSONDecodeError,
        httpx.HTTPStatusError,
        httpx.ConnectError,
        httpx.TimeoutException,
    ) as exc:
        logger.error("条目分析失败 [%s]: %s", item.get("url", ""), exc)
        return None

    title = data.get("title") or item.get("title", "")
    summary = str(data.get("summary", "")).strip()
    summary_en = str(data.get("summary_en", "")).strip()
    tags = [str(tag).strip() for tag in data.get("tags", []) if str(tag).strip()]
    importance = data.get("importance", "medium")
    score = data.get("score", 5)
    if importance not in ("high", "medium", "low"):
        importance = "medium"
    try:
        score = int(score)
    except (TypeError, ValueError):
        score = 5

    return {
        "title": title,
        "summary": summary,
        "summary_en": summary_en,
        "tags": tags,
        "importance": importance,
        "score": max(1, min(10, score)),
    }


def _parse_llm_json(content: str) -> dict[str, Any]:
    """从 LLM 输出中解析 JSON 对象。

    Args:
        content: LLM 返回的文本，可能包裹 markdown 代码块或夹杂说明。

    Returns:
        解析后的 JSON 字典。

    Raises:
        ValueError: 未在文本中找到合法 JSON 对象时抛出。
        json.JSONDecodeError: JSON 语法非法时抛出。
    """
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("LLM 输出中未找到 JSON 对象")
    return json.loads(text[start : end + 1])


def _load_existing_source_urls() -> set[str]:
    """扫描 knowledge/articles/ 已有条目的 source_url，用于跨批次去重。

    Returns:
        已存在条目的 source_url 集合。
    """
    existing: set[str] = set()
    if not ARTICLES_DIR.exists():
        return existing
    for path in ARTICLES_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        entries = data if isinstance(data, list) else [data]
        for entry in entries:
            url = entry.get("source_url")
            if isinstance(url, str) and url:
                existing.add(url)
    return existing


def organize_items(
    analyzed: list[tuple[dict[str, Any], dict[str, Any] | None]],
) -> list[dict[str, Any]]:
    """去重 + 格式标准化 + 必填字段校验。

    Args:
        analyzed: (原始条目, 分析结果或 None) 配对列表。

    Returns:
        通过校验的标准化文章列表。
    """
    existing_urls = _load_existing_source_urls()
    seen_urls: set[str] = set()
    organized: list[dict[str, Any]] = []

    for item, analysis in analyzed:
        url = item.get("url", "")
        if not url or url in seen_urls or url in existing_urls:
            continue
        seen_urls.add(url)

        article = _standardize_article(item, analysis)
        errors = validate_article(article)
        if errors:
            logger.warning("文章校验未通过 [%s]: %s", url, "; ".join(errors))
            continue
        organized.append(article)

    logger.info("整理完成: %d 篇文章通过校验", len(organized))
    return organized


def _standardize_article(
    item: dict[str, Any],
    analysis: dict[str, Any] | None,
) -> dict[str, Any]:
    """将原始条目与分析结果标准化为知识条目字典。

    Args:
        item: 原始条目字典。
        analysis: LLM 分析结果字典，可能为 None（分析失败）。

    Returns:
        符合项目知识条目格式的字典。
    """
    source = SOURCE_MAP.get(item.get("source", ""), "github_trending")
    fetched_at = _utc_now()
    analyzed = analysis is not None

    meta: dict[str, Any] = {}
    popularity = item.get("popularity") or {}
    if "stars" in popularity:
        meta["stars"] = popularity["stars"]
    if "points" in popularity:
        meta["points"] = popularity["points"]
    if "comments" in popularity:
        meta["comments"] = popularity["comments"]

    article: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "title": analysis.get("title") if analysis else item.get("title", ""),
        "source": source,
        "source_url": item.get("url", ""),
        "summary": analysis.get("summary") if analysis else item.get("summary", ""),
        "tags": analysis.get("tags") if analysis else ["AI"],
        "importance": analysis.get("importance") if analysis else "medium",
        "status": "analyzed" if analyzed else "draft",
        "fetched_at": fetched_at,
    }
    if analyzed:
        article["analyzed_at"] = fetched_at
    if analysis and analysis.get("summary_en"):
        article["summary_en"] = analysis["summary_en"]
    if analysis and analysis.get("score"):
        article["score"] = analysis["score"]
    if meta:
        article["meta"] = meta
    return article


def validate_article(article: dict[str, Any]) -> list[str]:
    """校验知识条目必填字段与枚举取值。

    Args:
        article: 待校验的知识条目字典。

    Returns:
        错误信息列表，合法时为空列表。
    """
    errors: list[str] = []
    string_fields = ("id", "title", "source", "source_url", "summary", "status")
    for field in string_fields:
        value = article.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"缺少必填字段: {field}")

    if article.get("source") not in SOURCE_MAP.values():
        errors.append(f"source 非法: {article.get('source')}")
    if article.get("status") not in (
        "draft",
        "analyzed",
        "review",
        "published",
        "archived",
    ):
        errors.append(f"status 非法: {article.get('status')}")
    if article.get("importance") not in ("high", "medium", "low"):
        errors.append(f"importance 非法: {article.get('importance')}")
    if not article.get("source_url", "").startswith("https://"):
        errors.append("source_url 应为 https:// 开头")

    tags = article.get("tags")
    if not isinstance(tags, list) or not tags:
        errors.append("tags 应为非空数组")
    else:
        for index, tag in enumerate(tags):
            if not isinstance(tag, str) or not tag.strip():
                errors.append(f"tags[{index}] 应为非空字符串")
    return errors


def save_raw_items(items: list[dict[str, Any]], dry_run: bool) -> None:
    """将原始采集数据合并写入 knowledge/raw/YYYY-MM-DD.json。

    采用「读取现有 → 按 url 去重合并 → 写回」的方式，仅做增量追加，
    不覆盖已有数据。

    Args:
        items: 原始采集条目列表。
        dry_run: 为 True 时仅打印将要写入的路径，不实际落盘。
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    path = RAW_DIR / f"{today}.json"

    if dry_run:
        logger.info("[dry-run] 将写入原始数据: %s (%d 条)", path, len(items))
        return

    existing: list[dict[str, Any]] = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                existing = data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("读取原始数据文件失败，将新建: %s", exc)

    seen = {str(item.get("url", "")) for item in existing if item.get("url")}
    merged = list(existing)
    for item in items:
        url = str(item.get("url", ""))
        if url and url in seen:
            continue
        seen.add(url)
        merged.append(item)

    path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("原始数据已保存: %s (%d 条)", path, len(merged))


def _article_filename(article: dict[str, Any]) -> str:
    """根据文章生成唯一文件名（日期-来源-标识）。

    Args:
        article: 知识条目字典。

    Returns:
        形如 YYYY-MM-DD-source-key.json 的文件名。
    """
    date = datetime.now(timezone.utc).date().isoformat()
    source = article.get("source", "github_trending")
    url = article.get("source_url", "")
    key = ""
    if "github.com/" in url:
        match = re.search(r"github\.com/([^/]+)/([^/]+)", url)
        if match:
            key = f"{match.group(1)}_{match.group(2)}".lower()
    if not key:
        key = (
            re.sub(r"[^a-z0-9]+", "-", url.rstrip("/").split("/")[-1].lower())[:40]
            or article["id"][:8]
        )
    return f"{date}-{source}-{key}.json"


def save_articles(
    articles: list[dict[str, Any]],
    dry_run: bool,
) -> list[Path]:
    """将标准化文章逐一写入 knowledge/articles/ 下的独立 JSON 文件。

    Args:
        articles: 标准化文章列表。
        dry_run: 为 True 时仅打印将要写入的路径，不实际落盘。

    Returns:
        实际写入（或将要写入）的文件路径列表。
    """
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for article in articles:
        path = ARTICLES_DIR / _article_filename(article)
        paths.append(path)
        if dry_run:
            logger.info("[dry-run] 将写入文章: %s", path)
            continue
        path.write_text(
            json.dumps(article, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info("文章已保存: %s", path)
    return paths


def _state_file(kind: str) -> Path:
    """返回流水线中间状态文件的路径（按当天日期命名）。

    Args:
        kind: 状态类型，可选 analyzed / organized。

    Returns:
        .state 目录下形如 {kind}-YYYY-MM-DD.json 的路径。
    """
    today = datetime.now(timezone.utc).date().isoformat()
    return STATE_DIR / f"{kind}-{today}.json"


def _load_raw_items() -> list[dict[str, Any]] | None:
    """从 knowledge/raw/YYYY-MM-DD.json 读取原始采集条目。

    Returns:
        原始条目列表；文件缺失、解析失败或格式非法时返回 None。
    """
    today = datetime.now(timezone.utc).date().isoformat()
    path = RAW_DIR / f"{today}.json"
    if not path.exists():
        logger.error("未找到原始数据，请先运行 Step 1: %s", path)
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("读取原始数据失败: %s", exc)
        return None
    if not isinstance(data, list):
        logger.error("原始数据格式非法: %s", path)
        return None
    return data


def _load_analyzed_items() -> list[tuple[dict[str, Any], dict[str, Any] | None]] | None:
    """从 .state/analyzed-YYYY-MM-DD.json 读取分析结果。

    Returns:
        (原始条目, 分析结果或 None) 配对列表；文件缺失或格式非法时返回 None。
    """
    path = _state_file("analyzed")
    if not path.exists():
        logger.error("未找到分析结果，请先运行 Step 2: %s", path)
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("读取分析结果失败: %s", exc)
        return None
    if not isinstance(data, list):
        logger.error("分析结果格式非法: %s", path)
        return None
    return [
        (entry["item"], entry["analysis"])
        for entry in data
        if isinstance(entry, dict) and isinstance(entry.get("item"), dict)
    ]


def _load_organized_items() -> list[dict[str, Any]] | None:
    """从 .state/organized-YYYY-MM-DD.json 读取整理后的文章。

    Returns:
        标准文章列表；文件缺失或格式非法时返回 None。
    """
    path = _state_file("organized")
    if not path.exists():
        logger.error("未找到整理结果，请先运行 Step 3: %s", path)
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("读取整理结果失败: %s", exc)
        return None
    if not isinstance(data, list):
        logger.error("整理结果格式非法: %s", path)
        return None
    return data


def _save_analyzed_items(
    analyzed: list[tuple[dict[str, Any], dict[str, Any] | None]],
    dry_run: bool,
) -> None:
    """将分析结果写入 .state/analyzed-YYYY-MM-DD.json。

    Args:
        analyzed: (原始条目, 分析结果或 None) 配对列表。
        dry_run: 为 True 时仅打印将要写入的路径，不落盘。
    """
    path = _state_file("analyzed")
    if dry_run:
        logger.info("[dry-run] 将写入分析结果: %s", path)
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = [{"item": item, "analysis": analysis} for item, analysis in analyzed]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("分析结果已保存: %s (%d 条)", path, len(analyzed))


def _save_organized_items(articles: list[dict[str, Any]], dry_run: bool) -> None:
    """将整理后的文章写入 .state/organized-YYYY-MM-DD.json。

    Args:
        articles: 标准文章列表。
        dry_run: 为 True 时仅打印将要写入的路径，不落盘。
    """
    path = _state_file("organized")
    if dry_run:
        logger.info("[dry-run] 将写入整理结果: %s", path)
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(articles, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("整理结果已保存: %s (%d 篇)", path, len(articles))


def _step_collect(
    sources: list[str],
    limit: int,
    dry_run: bool,
) -> list[dict[str, Any]] | None:
    """Step 1 采集：抓取原始内容并写入 knowledge/raw/。

    Args:
        sources: 采集源列表（github / rss）。
        limit: 总采集条数上限。
        dry_run: 干跑模式，不实际落盘。

    Returns:
        采集到的原始条目列表；采集失败返回 None。
    """
    logger.info("=== Step 1 采集 ===")
    try:
        raw_items = collect_sources(sources, limit)
    except httpx.HTTPStatusError as exc:
        logger.error("采集失败: %s", exc)
        return None
    save_raw_items(raw_items, dry_run)
    if not raw_items:
        logger.warning("Step 1 采集结果为空")
    return raw_items


def _step_analyze(
    raw_items: list[dict[str, Any]],
    dry_run: bool,
) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    """Step 2 分析：调用 LLM 对每条原始内容做摘要/评分/标签。

    Args:
        raw_items: 原始条目列表。
        dry_run: 干跑模式，不实际落盘。

    Returns:
        (原始条目, 分析结果或 None) 配对列表。
    """
    logger.info("=== Step 2 分析 ===")
    try:
        provider = create_provider()
    except RuntimeError as exc:
        logger.warning("%s — 跳过 LLM 分析，条目将以 draft 状态保存", exc)
        provider = None

    analyzed: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for item in raw_items:
        analysis = None
        if provider is not None:
            analysis = analyze_item(provider, item)
        analyzed.append((item, analysis))
    if provider is not None:
        provider.close()
    _save_analyzed_items(analyzed, dry_run)
    return analyzed


def _step_organize(
    analyzed: list[tuple[dict[str, Any], dict[str, Any] | None]],
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Step 3 整理：去重、标准化并校验文章。

    Args:
        analyzed: (原始条目, 分析结果或 None) 配对列表。
        dry_run: 干跑模式，不实际落盘。

    Returns:
        通过校验的标准文章列表。
    """
    logger.info("=== Step 3 整理 ===")
    articles = organize_items(analyzed)
    _save_organized_items(articles, dry_run)
    return articles


def _step_save(articles: list[dict[str, Any]], dry_run: bool) -> list[Path]:
    """Step 4 保存：将标准文章写入 knowledge/articles/。

    Args:
        articles: 标准文章列表。
        dry_run: 干跑模式，不实际落盘。

    Returns:
        写入（或将要写入）的文章文件路径列表。
    """
    logger.info("=== Step 4 保存 ===")
    return save_articles(articles, dry_run)


def run_pipeline(
    sources: list[str],
    limit: int,
    dry_run: bool,
    steps: set[int] | None = None,
) -> int:
    """执行流水线中指定的步骤。

    未指定 steps 时执行全部四步；指定子集时，选中的步骤优先复用本进程
    内存中已生成的中间结果，否则从落盘状态文件加载前置步骤产物，
    缺失时以错误码 1 退出。

    Args:
        sources: 采集源列表（github / rss）。
        limit: 总采集条数上限。
        dry_run: 干跑模式，不实际落盘。
        steps: 要执行的步骤号集合，None 时执行全部四步。

    Returns:
        成功返回 0，前置数据缺失或采集失败返回 1。
    """
    if steps is None:
        steps = set(PIPELINE_STEPS)

    raw_items: list[dict[str, Any]] | None = None
    analyzed: list[tuple[dict[str, Any], dict[str, Any] | None]] | None = None
    articles: list[dict[str, Any]] | None = None

    if 1 in steps:
        raw_items = _step_collect(sources, limit, dry_run)
        if raw_items is None:
            return 1
        if not raw_items:
            logger.warning("Step 1 采集结果为空，跳过后续步骤")
            return 0

    if 2 in steps:
        if raw_items is None:
            raw_items = _load_raw_items()
            if raw_items is None:
                return 1
        analyzed = _step_analyze(raw_items, dry_run)

    if 3 in steps:
        if analyzed is None:
            analyzed = _load_analyzed_items()
            if analyzed is None:
                return 1
        articles = _step_organize(analyzed, dry_run)

    if 4 in steps:
        if articles is None:
            articles = _load_organized_items()
            if articles is None:
                return 1
        paths = _step_save(articles, dry_run)
        logger.info(
            "流水线完成（步骤 %s）: 保存 %d 篇",
            ",".join(str(step) for step in sorted(steps)),
            len(paths),
        )
        return 0

    logger.info(
        "流水线完成（步骤 %s）",
        ",".join(str(step) for step in sorted(steps)),
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。

    Returns:
        配置完成的 argparse.ArgumentParser 实例。
    """
    parser = argparse.ArgumentParser(
        description="四步知识库自动化流水线：采集 → 分析 → 整理 → 保存"
    )
    parser.add_argument(
        "--sources",
        type=str,
        default="github,rss",
        help="采集源，逗号分隔（可选 github / rss），默认 github,rss",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="总采集条数上限，默认 20",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="干跑模式：执行完整流程但不写任何文件",
    )
    parser.add_argument(
        "--step",
        type=str,
        default="",
        metavar="N[,N...]",
        help="只执行指定步骤（可选 1/2/3/4），逗号分隔，如 --step 1,2；留空执行全部",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="输出 DEBUG 级别详细日志",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：解析参数并运行流水线。

    Args:
        argv: 命令行参数列表，None 时取 sys.argv[1:]。

    Returns:
        进程退出码。
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s %(message)s")

    valid_sources = {"github", "rss"}
    sources = [name.strip() for name in args.sources.split(",") if name.strip()]
    invalid = [name for name in sources if name not in valid_sources]
    if invalid:
        parser.error(f"非法采集源: {', '.join(invalid)}（可选 github / rss）")

    if args.limit < 1:
        parser.error("--limit 必须大于 0")

    steps: set[int] | None = None
    if args.step:
        parts = [name.strip() for name in args.step.split(",") if name.strip()]
        if not parts:
            parser.error("--step 不能为空")
        try:
            steps = {int(part) for part in parts}
        except ValueError:
            parser.error("--step 必须是 1-4 的数字，逗号分隔，如 --step 1,2")
        invalid = steps - set(PIPELINE_STEPS)
        if invalid:
            parser.error(
                "非法步骤: {}（可选 1/2/3/4）".format(
                    ", ".join(str(step) for step in sorted(invalid))
                )
            )

    rc = run_pipeline(sources, args.limit, args.dry_run, steps)
    if rc == 0:
        tracker.save_report(
            PIPELINE_DIR
            / "logs"
            / f"cost-{datetime.now(timezone.utc).date().isoformat()}.txt"
        )
    return rc


if __name__ == "__main__":
    sys.exit(main())
