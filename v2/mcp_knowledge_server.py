#!/usr/bin/env python3
"""MCP 服务器：让 AI 工具通过 MCP 协议搜索本地知识库。

实现基于 JSON-RPC 2.0 的 Model Context Protocol (MCP) stdio 服务器，
仅使用 Python 标准库，无任何第三方依赖。启动后从 stdin 逐行读取
JSON-RPC 请求，处理结果以单行 JSON 写回 stdout。

数据来源为 knowledge/articles/ 目录下所有 JSON 文章文件。

支持的方法:
    initialize               协商协议版本与服务器能力
    notifications/initialized 客户端初始化完成通知（无需响应）
    tools/list               列出可用工具
    tools/call               调用具体工具
    ping                     心跳检测

用法:
    python mcp_knlowledge_server.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger("mcp_knowledge_server")

SERVER_NAME = "knowledge-search-server"
SERVER_VERSION = "0.1.0"
SUPPORTED_PROTOCOL_VERSION = "2025-06-18"

ARTICLES_DIR = Path(
    os.environ.get("KNOWLEDGE_ARTICLES_DIR")
    or Path(__file__).resolve().parent / "knowledge" / "articles"
)

_JSONRPC_VERSION = "2.0"


class KnowledgeBase:
    """加载并索引 knowledge/articles/ 目录下的知识文章。

    Args:
        articles_dir: 文章 JSON 文件所在目录，默认为脚本同级的
            knowledge/articles/ 目录。

    Attributes:
        _articles_dir: 文章目录路径。
        _cache: 已加载的文章列表缓存。
        _cache_key: 当前缓存对应的目录快照签名。
    """

    def __init__(self, articles_dir: Path = ARTICLES_DIR) -> None:
        self._articles_dir = articles_dir
        self._cache: list[dict[str, Any]] = []
        self._cache_key: tuple[tuple[str, int, int], ...] | None = None

    def _snapshot_key(self) -> tuple[tuple[str, int, int], ...]:
        """计算文章目录内容签名，用于判断是否需要重新加载缓存。

        Returns:
            按文件名排序的 (文件名, 修改时间纳秒, 文件大小) 元组列表。
        """
        if not self._articles_dir.exists():
            return ()
        files = sorted(self._articles_dir.glob("*.json"))
        key: list[tuple[str, int, int]] = []
        for path in files:
            stat = path.stat()
            key.append((path.name, stat.st_mtime_ns, stat.st_size))
        return tuple(key)

    def _load_all(self) -> list[dict[str, Any]]:
        """读取目录下全部文章，必要时重建缓存。

        Returns:
            所有文章字典列表；目录不存在或为空时返回空列表。

        Raises:
            ValueError: 任一 JSON 文件解析失败或顶层不是对象时抛出。
        """
        snapshot = self._snapshot_key()
        if snapshot == self._cache_key and self._cache_key is not None:
            return self._cache
        if not self._articles_dir.exists():
            logger.warning("文章目录不存在: %s", self._articles_dir)
            self._cache, self._cache_key = [], snapshot
            return self._cache
        articles: list[dict[str, Any]] = []
        for path in self._articles_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"文章文件解析失败 {path}: {exc}") from exc
            if not isinstance(data, dict):
                raise TypeError(f"文章文件顶层必须是 JSON 对象: {path}")
            articles.append(data)
        self._cache, self._cache_key = articles, snapshot
        logger.debug("已加载 %d 篇文章，来源: %s", len(articles), self._articles_dir)
        return articles

    def search_articles(self, keyword: str, limit: int = 5) -> list[dict[str, Any]]:
        """按关键词在标题、摘要与标签中模糊搜索文章。

        Args:
            keyword: 搜索关键词，大小写不敏感。
            limit: 返回结果数量上限，按 score 降序取前 limit 条。

        Returns:
            匹配文章的摘要信息列表，未命中时返回空列表。
        """
        needle = keyword.lower()
        articles = self._load_all()
        matches: list[dict[str, Any]] = []
        for article in articles:
            title = str(article.get("title") or "").lower()
            summary = str(article.get("summary") or "").lower()
            tags = " ".join(str(tag) for tag in (article.get("tags") or []))
            if needle in title or needle in summary or needle in tags.lower():
                matches.append(article)
        matches.sort(key=lambda item: item.get("score") or 0, reverse=True)
        results: list[dict[str, Any]] = []
        for article in matches[:limit]:
            results.append(
                {
                    "id": article.get("id"),
                    "title": article.get("title"),
                    "source": article.get("source"),
                    "score": article.get("score"),
                    "importance": article.get("importance"),
                    "tags": article.get("tags") or [],
                    "summary": article.get("summary"),
                }
            )
        return results

    def get_article(self, article_id: str) -> dict[str, Any] | None:
        """按 ID 获取文章完整内容。

        Args:
            article_id: 文章唯一 ID。

        Returns:
            匹配的文章完整字典；未找到时返回 None。
        """
        for article in self._load_all():
            if article.get("id") == article_id:
                return article
        return None

    def knowledge_stats(self, top_tags: int = 10) -> dict[str, Any]:
        """汇总知识库统计信息。

        Args:
            top_tags: 热门标签返回数量上限。

        Returns:
            包含文章总数、来源分布与热门标签的统计字典。
        """
        articles = self._load_all()
        source_distribution: dict[str, int] = {}
        tag_counter: dict[str, int] = {}
        for article in articles:
            source = article.get("source")
            if isinstance(source, str) and source:
                source_distribution[source] = source_distribution.get(source, 0) + 1
            for tag in article.get("tags") or []:
                if isinstance(tag, str) and tag:
                    tag_counter[tag] = tag_counter.get(tag, 0) + 1
        hot_tags = [
            {"tag": tag, "count": count}
            for tag, count in sorted(
                tag_counter.items(), key=lambda item: item[1], reverse=True
            )
        ]
        return {
            "total_articles": len(articles),
            "source_distribution": source_distribution,
            "hot_tags": hot_tags[:top_tags],
        }


class KnowledgeMcpServer:
    """基于 JSON-RPC 2.0 的 MCP stdio 服务器。

    Args:
        knowledge_base: 底层知识库实例，提供文章检索能力。
    """

    def __init__(self, knowledge_base: KnowledgeBase) -> None:
        self._kb = knowledge_base
        self._tool_handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "search_articles": self._handle_search,
            "get_article": self._handle_get_article,
            "knowledge_stats": self._handle_stats,
        }
        self._tools: list[dict[str, Any]] = [
            {
                "name": "search_articles",
                "description": "按关键词在文章标题、摘要与标签中搜索本地知识库，返回按 score 降序排列的结果列表。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "keyword": {
                            "type": "string",
                            "description": "搜索关键词，大小写不敏感",
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 50,
                            "default": 5,
                            "description": "返回结果数量上限",
                        },
                    },
                    "required": ["keyword"],
                },
            },
            {
                "name": "get_article",
                "description": "按文章 ID 获取知识库中的文章完整内容，包含摘要、标签、来源等全部字段。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "article_id": {
                            "type": "string",
                            "description": "文章唯一 ID",
                        }
                    },
                    "required": ["article_id"],
                },
            },
            {
                "name": "knowledge_stats",
                "description": "返回知识库统计信息，包括文章总数、来源分布与热门标签。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "top_tags": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                            "default": 10,
                            "description": "热门标签返回数量上限",
                        }
                    },
                },
            },
        ]

    @staticmethod
    def _make_result(msg_id: Any, result: Any) -> dict[str, Any]:
        """构造 JSON-RPC 成功响应。

        Args:
            msg_id: 请求 id，原样回显。
            result: 响应结果。

        Returns:
            JSON-RPC 成功响应字典。
        """
        return {"jsonrpc": _JSONRPC_VERSION, "id": msg_id, "result": result}

    @staticmethod
    def _make_error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
        """构造 JSON-RPC 错误响应。

        Args:
            msg_id: 请求 id，原样回显。
            code: 错误码。
            message: 错误描述。

        Returns:
            JSON-RPC 错误响应字典。
        """
        return {
            "jsonrpc": _JSONRPC_VERSION,
            "id": msg_id,
            "error": {"code": code, "message": message},
        }

    def dispatch_line(self, line: str) -> dict[str, Any] | None:
        """解析单行 JSON 并分发请求，通知类消息不产生响应。

        Args:
            line: stdin 读入的单行 JSON 字符串。

        Returns:
            响应字典；请求为通知或消息为空时返回 None。
        """
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.error("JSON-RPC 解析失败: %s", exc)
            return self._make_error(None, -32700, f"Parse error: {exc}")
        if not isinstance(message, dict):
            return self._make_error(None, -32600, "Invalid Request")
        return self.handle_request(message)

    def handle_request(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """根据 method 分发请求，返回响应或 None（通知）。

        Args:
            message: 已解析的 JSON-RPC 请求字典。

        Returns:
            JSON-RPC 响应字典；通知消息返回 None。

        Raises:
            ValueError: params 结构非法时抛出。
        """
        msg_id = message.get("id")
        method = message.get("method")
        if msg_id is None:
            logger.debug("收到通知: %s", method)
            return None
        if not isinstance(method, str):
            return self._make_error(msg_id, -32600, "Invalid Request: method 缺失")
        try:
            if method == "initialize":
                return self._make_result(msg_id, self._handle_initialize(message))
            if method == "tools/list":
                return self._make_result(msg_id, {"tools": self._tools})
            if method == "tools/call":
                return self._make_result(msg_id, self._call_tool(message))
            if method == "ping":
                return self._make_result(msg_id, {})
            return self._make_error(msg_id, -32601, f"Method not found: {method}")
        except (TypeError, ValueError) as exc:
            logger.warning("请求参数非法: %s", exc)
            return self._make_error(msg_id, -32602, f"Invalid params: {exc}")
        except Exception as exc:
            logger.exception("处理请求 %s 时发生内部错误", method)
            return self._make_error(msg_id, -32603, f"Internal error: {exc}")

    def _handle_initialize(self, message: dict[str, Any]) -> dict[str, Any]:
        """处理 initialize 握手，回显客户端协议版本。

        Args:
            message: initialize 请求字典。

        Returns:
            协议版本、服务器能力与服务器信息组成的握手结果。
        """
        params = message.get("params")
        client_info = ""
        protocol_version = SUPPORTED_PROTOCOL_VERSION
        if isinstance(params, dict):
            offered = params.get("protocolVersion")
            if isinstance(offered, str):
                protocol_version = offered
            client = params.get("clientInfo") or {}
            if isinstance(client, dict):
                name = client.get("name") or "unknown"
                version = client.get("version") or ""
                client_info = f"{name} {version}".strip()
        logger.info(
            "MCP 客户端已连接: %s (protocol %s)",
            client_info or "unknown",
            protocol_version,
        )
        return {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "该服务器提供对本地 AI 知识库的检索能力。可用工具: "
                "search_articles(关键词搜索)、get_article(按 ID 获取全文)、"
                "knowledge_stats(统计信息)。"
            ),
        }

    def _call_tool(self, message: dict[str, Any]) -> dict[str, Any]:
        """分发 tools/call 到具体工具处理器。

        Args:
            message: tools/call 请求字典。

        Returns:
            MCP 工具调用结果，content 为序列化 JSON 文本。

        Raises:
            ValueError: 工具名不存在或 arguments 结构非法时抛出。
        """
        params = message.get("params")
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            raise TypeError("tools/call 需要 params.name")
        name = params["name"]
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise TypeError("tools/call 的 arguments 必须是 JSON 对象")
        handler = self._tool_handlers.get(name)
        if handler is None:
            raise ValueError(f"未知工具: {name}")
        try:
            payload = handler(arguments)
        except (TypeError, ValueError) as exc:
            logger.warning("工具 %s 参数校验失败: %s", name, exc)
            return self._tool_result(f"工具调用失败: {exc}", is_error=True)
        except Exception as exc:
            logger.exception("工具 %s 执行异常", name)
            return self._tool_result(f"工具执行异常: {exc}", is_error=True)
        return self._tool_result(
            json.dumps(payload, ensure_ascii=False, indent=2), is_error=False
        )

    @staticmethod
    def _tool_result(text: str, is_error: bool) -> dict[str, Any]:
        """构造 MCP 工具调用结果。

        Args:
            text: 文本内容。
            is_error: 是否为执行错误。

        Returns:
            MCP tools/call 结果字典。
        """
        return {"content": [{"type": "text", "text": text}], "isError": is_error}

    def _handle_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """实现 search_articles 工具。

        Args:
            arguments: 工具参数，需包含 keyword，可选 limit。

        Returns:
            查询关键词、匹配总数与结果列表。

        Raises:
            ValueError: 参数缺失或类型非法时抛出。
        """
        keyword = arguments.get("keyword")
        if not isinstance(keyword, str) or not keyword.strip():
            raise ValueError("参数 keyword 必填且不能为空字符串")
        limit = arguments.get("limit", 5)
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise TypeError("参数 limit 必须是整数")
        limit = max(1, min(limit, 50))
        results = self._kb.search_articles(keyword=keyword.strip(), limit=limit)
        return {
            "keyword": keyword.strip(),
            "limit": limit,
            "total_matches": len(results),
            "results": results,
        }

    def _handle_get_article(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """实现 get_article 工具。

        Args:
            arguments: 工具参数，需包含 article_id。

        Returns:
            包含 found 标志与 article 全文的结果字典。

        Raises:
            ValueError: 参数缺失或类型非法时抛出。
        """
        article_id = arguments.get("article_id")
        if not isinstance(article_id, str) or not article_id.strip():
            raise ValueError("参数 article_id 必填且不能为空字符串")
        article = self._kb.get_article(article_id=article_id.strip())
        if article is None:
            return {
                "found": False,
                "message": f"未找到 ID 为 {article_id.strip()} 的文章",
            }
        return {"found": True, "article": article}

    def _handle_stats(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """实现 knowledge_stats 工具。

        Args:
            arguments: 工具参数，可选 top_tags。

        Returns:
            知识库统计字典。

        Raises:
            ValueError: top_tags 类型非法时抛出。
        """
        top_tags = arguments.get("top_tags", 10)
        if not isinstance(top_tags, int) or isinstance(top_tags, bool):
            raise TypeError("参数 top_tags 必须是整数")
        top_tags = max(1, min(top_tags, 100))
        return self._kb.knowledge_stats(top_tags=top_tags)


def main() -> int:
    """启动 MCP 服务器主循环，从 stdin 逐行读取请求并响应。

    Returns:
        进程退出码，正常退出返回 0。
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    sys.stdout.reconfigure(encoding="utf-8")
    server = KnowledgeMcpServer(KnowledgeBase())
    logger.info("%s v%s 启动，文章目录: %s", SERVER_NAME, SERVER_VERSION, ARTICLES_DIR)
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            response = server.dispatch_line(line)
        except Exception:
            logger.exception("分发消息时发生未预期异常")
            response = server._make_error(None, -32603, "Internal error")
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    logger.info("stdin 关闭，服务器退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
