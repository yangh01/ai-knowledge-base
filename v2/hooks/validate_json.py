#!/usr/bin/env python3
"""校验知识条目 JSON 文件的命令行工具。

用法:
    python hooks/validate_json.py <json_file> [json_file2 ...]

支持通配符路径（如 knowledge/articles/*.json）。所有文件校验通过时退出码
为 0；任一文件存在错误时退出码为 1，并输出错误列表与汇总统计。
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

MIN_SUMMARY_LENGTH = 20
MIN_TAGS = 1
SCORE_MIN = 1
SCORE_MAX = 10

REQUIRED_FIELDS: tuple[str, ...] = (
    "id",
    "title",
    "source",
    "summary",
    "tags",
    "status",
)
VALID_STATUSES: frozenset[str] = frozenset(
    {"draft", "review", "published", "archived", "analyzed"}
)
VALID_SOURCES: frozenset[str] = frozenset({"github_trending", "hacker_news"})
VALID_AUDIENCES: frozenset[str] = frozenset(
    {"beginner", "intermediate", "advanced"}
)


def _is_https_url(value: object) -> bool:
    """判断值是否为以 https:// 开头的合法 URL。

    Args:
        value: 待检查的值。

    Returns:
        合法时返回 True，否则返回 False。
    """
    if not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme == "https" and bool(parsed.netloc)


def _format_enums(values: frozenset[str]) -> str:
    """将枚举值集合格式化为可读字符串。

    Args:
        values: 枚举值集合。

    Returns:
        以 "/" 分隔的枚举值字符串。
    """
    return " / ".join(sorted(values))


def validate_entry(data: dict[str, object]) -> list[str]:
    """校验单个知识条目字典并返回错误信息列表。

    Args:
        data: 待校验的知识条目字典。

    Returns:
        错误信息字符串列表；条目完全合法时返回空列表。
    """
    errors: list[str] = []

    for field in REQUIRED_FIELDS:
        if field not in data:
            errors.append(f"[required] 缺少必填字段: {field}")

    if "id" in data:
        item_id = data["id"]
        if not (isinstance(item_id, str) and item_id.strip()):
            errors.append("[type] 字段 id 应为非空字符串")

    if "title" in data:
        title = data["title"]
        if not (isinstance(title, str) and title.strip()):
            errors.append("[type] 字段 title 应为非空字符串")

    if "source" in data:
        source = data["source"]
        if not isinstance(source, str) or not source.strip():
            errors.append("[type] 字段 source 应为非空字符串")
        elif source not in VALID_SOURCES:
            errors.append(
                f"[value] source 值 {source!r} 非法，应为 {_format_enums(VALID_SOURCES)} 之一"
            )

    if "source_url" in data:
        source_url = data["source_url"]
        if not _is_https_url(source_url):
            errors.append(
                f"[url] source_url 格式无效: {source_url!r}（应为 https:// 开头的有效 URL）"
            )

    if "summary" in data:
        summary = data["summary"]
        if not isinstance(summary, str):
            errors.append("[type] 字段 summary 应为字符串")
        elif len(summary.strip()) < MIN_SUMMARY_LENGTH:
            errors.append(
                f"[summary] summary 过短（{len(summary.strip())} 字），"
                f"最少 {MIN_SUMMARY_LENGTH} 字"
            )

    if "tags" in data:
        tags = data["tags"]
        if not isinstance(tags, list):
            errors.append("[type] 字段 tags 应为数组 (list)")
        else:
            if len(tags) < MIN_TAGS:
                errors.append(f"[tags] tags 至少需要 {MIN_TAGS} 个标签")
            for index, tag in enumerate(tags):
                if not (isinstance(tag, str) and tag.strip()):
                    errors.append(f"[type] tags[{index}] 应为非空字符串")

    if "status" in data:
        status = data["status"]
        if not isinstance(status, str):
            errors.append("[type] 字段 status 应为字符串")
        elif status not in VALID_STATUSES:
            errors.append(
                f"[value] status 值 {status!r} 非法，"
                f"应为 {_format_enums(VALID_STATUSES)} 之一"
            )

    if "score" in data:
        score = data["score"]
        if isinstance(score, bool) or not isinstance(score, int):
            errors.append("[type] 字段 score 应为整数")
        elif not SCORE_MIN <= score <= SCORE_MAX:
            errors.append(
                f"[score] score 值 {score} 超出范围，"
                f"应为 {SCORE_MIN}-{SCORE_MAX} 的整数"
            )

    if "audience" in data:
        audience = data["audience"]
        if not isinstance(audience, str) or audience not in VALID_AUDIENCES:
            errors.append(
                f"[value] audience 值 {audience!r} 非法，"
                f"应为 {_format_enums(VALID_AUDIENCES)} 之一"
            )

    return errors


def validate_file(path: Path) -> list[str]:
    """校验单个 JSON 文件并返回错误信息列表。

    支持两种顶层结构：单个知识条目对象，或知识条目对象数组。

    Args:
        path: 待校验的 JSON 文件路径。

    Returns:
        错误信息字符串列表；文件完全合法时返回空列表。
    """
    errors: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        return [f"[parse] 文件无法解析: {exc}"]

    if isinstance(data, dict):
        errors.extend(validate_entry(data))
    elif isinstance(data, list):
        if not data:
            errors.append("[structure] JSON 数组为空，至少应包含一条知识条目")
        for index, item in enumerate(data):
            if not isinstance(item, dict):
                errors.append(f"[structure] 数组第 {index} 项应为 JSON 对象")
            else:
                for error in validate_entry(item):
                    errors.append(f"[#{index}] {error}")
    else:
        errors.append("[structure] 顶层结构应为 JSON 对象或对象数组")
    return errors


def main(argv: list[str]) -> int:
    """校验命令行指定的 JSON 文件并返回进程退出码。

    Args:
        argv: 命令行参数（不含程序名）。

    Returns:
        校验全部通过返回 0；用法错误或存在错误返回 1。
    """
    if not argv:
        print("用法: python hooks/validate_json.py <json_file> [json_file2 ...]")
        print("示例: python hooks/validate_json.py knowledge/articles/*.json")
        return 1

    files: list[Path] = []
    unmatched: list[str] = []
    seen: set[Path] = set()
    for pattern in argv:
        if glob.has_magic(pattern):
            matches = [Path(match) for match in glob.glob(pattern)]
            if not matches:
                unmatched.append(pattern)
            for match in sorted(matches):
                if match not in seen:
                    seen.add(match)
                    files.append(match)
        else:
            path = Path(pattern)
            if path not in seen:
                seen.add(path)
                files.append(path)

    results: list[tuple[str, list[str]]] = []
    for pattern in unmatched:
        results.append((pattern, [f"[file] 通配符未匹配到任何文件: {pattern}"]))
    for path in files:
        if path.is_dir():
            results.append((str(path), ["[file] 路径为目录而非 JSON 文件"]))
            continue
        if not path.exists():
            results.append((str(path), [f"[file] 文件不存在: {path}"]))
            continue
        results.append((str(path), validate_file(path)))

    failed_files = sum(1 for _, errors in results if errors)
    total_errors = sum(len(errors) for _, errors in results)

    for name, errors in results:
        if errors:
            print(f"[FAIL] {name}")
            for error in errors:
                print(f"  - {error}")
        else:
            print(f"[ OK ] {name}")

    print()
    print("=== 汇总 ===")
    print(
        f"总文件: {len(results)} | 通过: {len(results) - failed_files} "
        f"| 失败: {failed_files} | 错误总数: {total_errors}"
    )
    if failed_files:
        print("校验未通过")
        return 1
    print("校验全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
