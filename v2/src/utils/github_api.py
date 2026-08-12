import json
import logging
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"


def get_repo_info(
    owner: str,
    repo: str,
    token: Optional[str] = None,
) -> dict:
    """从 GitHub API 获取指定仓库的基本信息。

    Args:
        owner: 仓库所有者（用户名或组织名）。
        repo: 仓库名称。
        token: GitHub Personal Access Token，传入可提升 API 速率限制。

    Returns:
        包含仓库基本信息的字典，字段包括：
        - full_name: 完整仓库名
        - stars: Star 数量
        - forks: Fork 数量
        - description: 仓库描述
        - language: 主要编程语言
        - topics: 主题标签列表
        - url: 仓库 HTML 地址

    Raises:
        ValueError: owner 或 repo 为空字符串时抛出。
        urllib.error.HTTPError: API 返回非 2xx 状态码时抛出（含 404、403 速率限制等）。
        urllib.error.URLError: 网络连接失败时抛出。
    """
    if not owner or not repo:
        raise ValueError("owner 和 repo 不能为空")

    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ai-knowledge-base",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    logger.info("正在获取仓库信息: %s/%s", owner, repo)

    with urllib.request.urlopen(request) as response:
        data = json.loads(response.read().decode())

    result = {
        "full_name": data.get("full_name", ""),
        "stars": data.get("stargazers_count", 0),
        "forks": data.get("forks_count", 0),
        "description": data.get("description") or "",
        "language": data.get("language") or "",
        "topics": data.get("topics", []),
        "url": data.get("html_url", ""),
    }

    logger.info(
        "仓库 %s/%s: %d stars, %d forks",
        owner, repo, result["stars"], result["forks"],
    )
    return result
