#!/usr/bin/env python3
"""五维度知识条目质量评分命令行工具。

五个评分维度（加权总分 100 分）：
  1. 摘要质量   — 25 分：摘要长度与技术关键词覆盖
  2. 技术深度   — 25 分：基于条目 score 字段（1-10 映射到 0-25）
  3. 格式规范   — 20 分：id / title / source_url / status / 时间戳各 4 分
  4. 标签精度   — 15 分：1-3 个合法标签最佳，按标准标签列表校验
  5. 空洞词检测 — 15 分：命中中英文空洞词黑名单扣分

等级标准：A（>=80 分）、B（>=60 分）、C（<60 分）。

用法:
    python hooks/check_quality.py knowledge/articles/test-quality-good.json
    python hooks/check_quality.py knowledge/articles/*.json
    python hooks/check_quality.py knowledge/articles/

支持单文件、通配符模式与目录三种输入，通配符通过 glob 展开，目录自动展开为
其中所有 *.json 文件。存在 C 级条目时退出码为 1，否则退出码为 0。
"""

from __future__ import annotations

import glob
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ── 空洞词黑名单 ──────────────────────────────────────────────────────────

HOLLOW_WORDS_ZH: tuple[str, ...] = (
    "赋能",
    "抓手",
    "闭环",
    "打通",
    "全链路",
    "底层逻辑",
    "颗粒度",
    "对齐",
    "拉通",
    "沉淀",
    "强大的",
    "革命性的",
)

HOLLOW_WORDS_EN: tuple[str, ...] = (
    "groundbreaking",
    "revolutionary",
    "game-changing",
    "cutting-edge",
    "state-of-the-art",
    "leverage",
    "synergy",
    "paradigm shift",
    "disruptive",
    "next-generation",
    "world-class",
)

HOLLOW_WORDS: tuple[str, ...] = HOLLOW_WORDS_ZH + HOLLOW_WORDS_EN

# ── 合法标签列表 ──────────────────────────────────────────────────────────
# ASCII 标签统一小写比对，中文标签原样比对。

VALID_TAGS: frozenset[str] = frozenset(
    {
        "agent",
        "rag",
        "mcp",
        "llm",
        "fine-tuning",
        "prompt-engineering",
        "multi-agent",
        "tool-use",
        "evaluation",
        "deployment",
        "security",
        "reasoning",
        "code-generation",
        "vision",
        "audio",
        "robotics",
        "开源项目",
        "工具调用",
        "推理",
        "多模态",
        "模型发布",
        "知识图谱",
        "可解释性",
        "代码智能",
        "量化",
        "教程指南",
        "评估",
        "Prompt工程",
        "视频生成",
        "行业分析",
        "内容生态",
        "技术博客",
        "AI教育",
        "法律AI",
        "端侧AI",
        "内容溯源",
        "AI基础设施",
        "编程语言",
        "理论",
        "可观测",
        "多Agent",
    }
)
VALID_TAGS_LOWER: frozenset[str] = frozenset(tag.lower() for tag in VALID_TAGS)

# ── 评分维度常量 ──────────────────────────────────────────────────────────

SUMMARY_MAX = 25.0
TECH_DEPTH_MAX = 25.0
FORMAT_MAX = 20.0
TAGS_MAX = 15.0
HOLLOW_MAX = 15.0

GRADE_A = 80
GRADE_B = 60

TIMESTAMP_FIELDS: tuple[str, ...] = (
    "fetched_at",
    "analyzed_at",
    "published_at",
    "collected_at",
    "updated_at",
)

TECH_KEYWORDS: tuple[str, ...] = (
    "模型",
    "训练",
    "推理",
    "API",
    "框架",
    "agent",
    "LLM",
    "RAG",
    "token",
    "向量",
    "embedding",
    "transformer",
    "微调",
)


# ── 评分结构 ──────────────────────────────────────────────────────────────


@dataclass
class DimensionScore:
    """单个评分维度的得分结果。

    Attributes:
        name: 维度名称。
        score: 实际得分。
        max_score: 该维度满分。
        details: 评分明细说明。
    """

    name: str
    score: float
    max_score: float
    details: str

    @property
    def percentage(self) -> float:
        """返回得分百分比（0-100）。

        Returns:
            得分占满分的百分比。
        """
        return (self.score / self.max_score * 100) if self.max_score > 0 else 0.0


@dataclass
class QualityReport:
    """一篇知识条目的五维度质量评估报告。

    Attributes:
        filepath: 被评估条目对应的文件路径。
        dimensions: 五个评分维度结果列表。
    """

    filepath: str
    dimensions: list[DimensionScore]

    @property
    def total_score(self) -> float:
        """返回加权总分。

        Returns:
            各维度得分之和。
        """
        return sum(dimension.score for dimension in self.dimensions)

    @property
    def max_total(self) -> float:
        """返回加权满分（应为 100 分）。

        Returns:
            各维度满分之和。
        """
        return sum(dimension.max_score for dimension in self.dimensions)

    @property
    def grade(self) -> str:
        """返回条目等级（A / B / C）。

        Returns:
            总分 >=80 返回 A，>=60 返回 B，否则返回 C。
        """
        if self.total_score >= GRADE_A:
            return "A"
        if self.total_score >= GRADE_B:
            return "B"
        return "C"


# ── 五维度评分函数 ────────────────────────────────────────────────────────


def score_summary_quality(data: dict[str, Any]) -> DimensionScore:
    """维度 1：摘要质量评分（满分 25 分）。

    摘要 >=50 字给 20 分基础分，>=20 字给 15 分基础分，否则 5 分；命中技术
    关键词每个 +1 分，奖励封顶 5 分；总分不超过 25 分。

    Args:
        data: 知识条目字典。

    Returns:
        摘要质量维度评分。
    """
    summary = data.get("summary", "")
    if not isinstance(summary, str) or not summary.strip():
        return DimensionScore("摘要质量", 0.0, SUMMARY_MAX, "无摘要")

    length = len(summary.strip())
    if length >= 50:
        base = 20.0
        detail = f"长度充足 ({length} 字)"
    elif length >= 20:
        base = 15.0
        detail = f"长度基本 ({length} 字)"
    else:
        base = 5.0
        detail = f"太短 ({length} 字)"

    lower_summary = summary.lower()
    keyword_count = sum(1 for kw in TECH_KEYWORDS if kw.lower() in lower_summary)
    bonus = min(5.0, keyword_count * 1.0)
    if bonus > 0:
        detail += f", 含 {keyword_count} 个技术关键词"

    score = min(SUMMARY_MAX, base + bonus)
    return DimensionScore("摘要质量", score, SUMMARY_MAX, detail)


def score_tech_depth(data: dict[str, Any]) -> DimensionScore:
    """维度 2：技术深度评分（满分 25 分）。

    将条目 score 字段（1-10）线性映射到 0-25 分；score 缺失时按中间值 5
    折算，类型异常时给基础分 10 分。

    Args:
        data: 知识条目字典。

    Returns:
        技术深度维度评分。
    """
    article_score = data.get("score", 5)

    if isinstance(article_score, bool) or not isinstance(article_score, (int, float)):
        return DimensionScore("技术深度", 10.0, TECH_DEPTH_MAX, "score 字段类型异常")

    clamped = max(0.0, min(10.0, float(article_score)))
    mapped = round((clamped / 10.0) * TECH_DEPTH_MAX, 1)
    detail = f"文章评分 {article_score}/10 → {mapped:.1f}/{TECH_DEPTH_MAX:.0f}"
    return DimensionScore("技术深度", mapped, TECH_DEPTH_MAX, detail)


def score_format(data: dict[str, Any]) -> DimensionScore:
    """维度 3：格式规范评分（满分 20 分）。

    id / title / source_url / status 四个必填字段各 4 分，任一时间戳字段
    （fetched_at / analyzed_at / published_at / collected_at / updated_at）
    非空给 4 分。

    Args:
        data: 知识条目字典。

    Returns:
        格式规范维度评分。
    """
    score = 0.0
    missing: list[str] = []

    for field_name in ("id", "title", "source_url", "status"):
        value = data.get(field_name)
        if isinstance(value, str) and value.strip():
            score += 4.0
        else:
            missing.append(field_name)

    if any(isinstance(data.get(field), str) and data[field].strip() for field in TIMESTAMP_FIELDS):
        score += 4.0
    else:
        missing.append("时间戳")

    detail = "完整" if not missing else "缺失: " + ", ".join(missing)
    return DimensionScore("格式规范", score, FORMAT_MAX, detail)


def score_tags(data: dict[str, Any]) -> DimensionScore:
    """维度 4：标签精度评分（满分 15 分）。

    1-3 个合法标签给满分；存在合法标签但非全部给 10 分；均不合法给 5 分；
    无标签给 0 分。标签总数超过 5 个时每个超出扣 1 分，封顶扣 5 分。

    Args:
        data: 知识条目字典。

    Returns:
        标签精度维度评分。
    """
    tags = data.get("tags", [])
    if not isinstance(tags, list):
        return DimensionScore("标签精度", 0.0, TAGS_MAX, "tags 字段类型异常")

    normalized = [tag.strip() if isinstance(tag, str) else "" for tag in tags]
    valid_count = sum(1 for tag in normalized if tag.lower() in VALID_TAGS_LOWER)
    total_count = len(normalized)

    if 1 <= total_count <= 3 and valid_count == total_count:
        score = TAGS_MAX
        detail = f"{total_count} 个标签，全部合法"
    elif valid_count > 0:
        score = 10.0
        detail = f"{valid_count}/{total_count} 个合法标签"
    else:
        score = 5.0
        detail = f"有 {total_count} 个标签但均不在标准列表"

    if total_count > 5:
        penalty = min(5.0, (total_count - 5) * 1.0)
        score = max(0.0, score - penalty)
        detail += f", 标签过多 (扣 {penalty:.0f} 分)"

    return DimensionScore("标签精度", score, TAGS_MAX, detail)


def score_hollow_words(data: dict[str, Any]) -> DimensionScore:
    """维度 5：空洞词检测评分（满分 15 分）。

    在 title 与 summary 文本中检索中英文空洞词黑名单，每命中一个扣 3 分，
    扣完为止（最低 0 分）。

    Args:
        data: 知识条目字典。

    Returns:
        空洞词检测维度评分。
    """
    parts: list[str] = []
    for field in ("summary", "title"):
        value = data.get(field)
        if isinstance(value, str):
            parts.append(value)
    text = " ".join(parts).lower()

    found: list[str] = []
    for word in HOLLOW_WORDS:
        if word.lower() in text:
            found.append(word)

    penalty = min(HOLLOW_MAX, len(found) * 3.0)
    score = HOLLOW_MAX - penalty

    if found:
        detail = f"发现 {len(found)} 个空洞词: {', '.join(found[:5])}"
    else:
        detail = "未发现空洞词"

    return DimensionScore("空洞词检测", score, HOLLOW_MAX, detail)


# ── 综合评估 ──────────────────────────────────────────────────────────────


def evaluate_quality(filepath: str, data: dict[str, Any]) -> QualityReport:
    """对单篇知识条目进行五维度综合评估。

    Args:
        filepath: 条目对应的文件路径，仅用于报告展示。
        data: 知识条目字典。

    Returns:
        该条目的综合质量报告。
    """
    dimensions = [
        score_summary_quality(data),
        score_tech_depth(data),
        score_format(data),
        score_tags(data),
        score_hollow_words(data),
    ]
    return QualityReport(filepath=filepath, dimensions=dimensions)


def print_report(report: QualityReport) -> None:
    """向标准输出打印单篇条目的可视化评估报告。

    Args:
        report: 待打印的质量评估报告。
    """
    print(f"\n{'─' * 50}")
    print(f"文件: {report.filepath}")
    print(f"{'─' * 50}")

    for dimension in report.dimensions:
        bar_len = int(dimension.percentage / 5)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(
            f"  {dimension.name:8s} [{bar}] "
            f"{dimension.score:5.1f}/{dimension.max_score:.0f}  {dimension.details}"
        )

    print(
        f"\n  总分: {report.total_score:.1f}/{report.max_total:.0f}  "
        f"等级: {report.grade}"
    )


# ── CLI 入口 ──────────────────────────────────────────────────────────────


def _expand_args(args: list[str]) -> list[Path]:
    """将命令行参数展开为待检查的文件路径列表（去重）。

    支持三种参数形态：单文件、通配符模式（按 glob 展开）、目录（展开为其
    中全部 *.json 文件）。

    Args:
        args: 命令行参数列表。

    Returns:
        展开并去重后的文件路径列表。
    """
    files: list[Path] = []
    seen: set[Path] = set()

    for arg in args:
        if glob.has_magic(arg):
            matches = [Path(match) for match in glob.glob(arg)]
            for match in sorted(matches):
                if match not in seen:
                    seen.add(match)
                    files.append(match)
            continue

        path = Path(arg)
        if path.is_dir():
            for match in sorted(path.glob("*.json")):
                if match not in seen:
                    seen.add(match)
                    files.append(match)
        elif path not in seen:
            seen.add(path)
            files.append(path)

    return files


def main(argv: list[str]) -> int:
    """运行五维度质量评分并返回进程退出码。

    Args:
        argv: 命令行参数（不含程序名）。

    Returns:
        存在 C 级条目或文件解析失败时返回 1；否则返回 0。无参数时返回 1。
    """
    if not argv:
        print("用法: python hooks/check_quality.py <json_file> [json_file ...]")
        print("示例: python hooks/check_quality.py knowledge/articles/*.json")
        print("      python hooks/check_quality.py knowledge/articles/")
        return 1

    files = _expand_args(argv)
    if not files:
        print("[WARN] 未匹配到任何 JSON 文件")
        return 1

    total_files = 0
    grade_counts = {"A": 0, "B": 0, "C": 0}
    has_c_grade = False

    for path in files:
        if not path.exists() or path.suffix != ".json":
            continue

        total_files += 1
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            print(f"[ERROR] {path}: 文件无法解析 — {exc}")
            has_c_grade = True
            continue

        report = evaluate_quality(str(path), data)
        print_report(report)
        grade_counts[report.grade] += 1
        if report.grade == "C":
            has_c_grade = True

    print(f"\n{'=' * 50}")
    print(f"质量评估汇总: {total_files} 文件")
    print(f"  A 级 (>=80): {grade_counts['A']}")
    print(f"  B 级 (>=60): {grade_counts['B']}")
    print(f"  C 级 (<60):  {grade_counts['C']}")
    print(f"{'=' * 50}")

    return 1 if has_c_grade else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
