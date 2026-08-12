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
    """从 GitHub API 获取指定仓库的基本信息（Star数、Fork数、描述）。

    Args:
        owner: 仓库所有者（用户名或组织名）。
        repo: 仓库名称。
        token: GitHub Personal Access Token，传入可提升 API 速率限制。

    Returns:
        包含仓库基本信息的字典，字段包括：
        - stars: Star 数量
        - forks: Fork 数量
        - description: 仓库描述

    Raises:
        ValueError: owner 或 repo 为空字符串时抛出。
        urllib.error.HTTPError: API 返回非 2xx 状态码时抛出。
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
        "stars": data.get("stargazers_count", 0),
        "forks": data.get("forks_count", 0),
        "description": data.get("description") or "",
    }

    logger.info(
        "仓库 %s/%s: %d stars, %d forks",
        owner, repo, result["stars"], result["forks"],
    )
    return result
