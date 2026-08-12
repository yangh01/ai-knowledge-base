"""统一 LLM 客户端 — 工厂模式封装多模型调用。

支持 DeepSeek、Qwen、OpenAI 三种提供商，通过环境变量切换：
    LLM_PROVIDER: deepseek（默认）/ qwen / openai
    <PROVIDER>_API_KEY: 对应的 API Key
    <PROVIDER>_BASE_URL: 可选，默认使用官方地址
    <PROVIDER>_MODEL:    可选，默认使用各家的默认模型

返回统一格式 LLMResponse（content + Usage 用量统计），不依赖 openai SDK，
使用 httpx 直接调用 OpenAI 兼容 Chat Completions API。
"""

from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── 数据结构 ───────────────────────────────────────────────────────────────


@dataclass
class Usage:
    """Token 用量统计。

    Attributes:
        prompt_tokens: 输入（Prompt）消耗的 Token 数。
        completion_tokens: 输出（Completion）消耗的 Token 数。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """返回总 Token 消耗数（输入 + 输出）。"""
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict[str, int]:
        """转换为字典表示，便于 JSON 序列化。

        Returns:
            包含 prompt_tokens / completion_tokens / total_tokens 的字典。
        """
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class LLMResponse:
    """统一的 LLM 响应格式。

    Attributes:
        content: 模型返回的文本内容。
        usage: Token 用量统计。
    """

    content: str
    usage: Usage = field(default_factory=Usage)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典表示，便于 JSON 序列化。

        Returns:
            包含 content 与 usage 的字典。
        """
        return {
            "content": self.content,
            "usage": self.usage.to_dict(),
        }


# ── 成本估算（每 1K tokens 价格，单位 USD） ──────────────────────────────

PRICING: dict[str, dict[str, float]] = {
    "deepseek-chat": {"input": 0.0014, "output": 0.0028},
    "deepseek-reasoner": {"input": 0.004, "output": 0.016},
    "qwen-plus": {"input": 0.002, "output": 0.006},
    "qwen-turbo": {"input": 0.0005, "output": 0.001},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4o": {"input": 0.005, "output": 0.015},
}

# 未知模型时的默认价格（USD / 1K tokens），避免查询失败报错。
DEFAULT_PRICE: dict[str, float] = {"input": 0.002, "output": 0.006}


def estimate_cost(model: str, usage: Usage) -> float:
    """估算单次调用的成本（USD）。

    Args:
        model: 模型名称（如 deepseek-chat），用于查询 PRICING 表。
        usage: Token 用量统计。

    Returns:
        估算成本（美元），精确到小数点后 6 位。
    """
    prices = PRICING.get(model, DEFAULT_PRICE)
    cost = (
        usage.prompt_tokens / 1000 * prices["input"]
        + usage.completion_tokens / 1000 * prices["output"]
    )
    return round(cost, 6)


# ── Provider 抽象基类 ────────────────────────────────────────────────────


class LLMProvider(ABC):
    """LLM 提供商抽象基类，定义统一的聊天接口。

    各提供商通过继承本类并实现 chat() 方法接入；新建提供商只需新增子类与
    工厂配置，无需改动上层调用代码。

    Attributes:
        api_key: API 密钥。
        base_url: API 基础地址（不含 /chat/completions 后缀）。
        model: 默认使用的模型名称。
        client: httpx 同步客户端，统一设置 60 秒超时。
    """

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.client = httpx.Client(timeout=60.0)

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        """发送聊天请求，返回统一格式响应。

        Args:
            messages: OpenAI 格式的对话消息列表
                （形如 [{"role": "system", "content": "..."}]）。
            temperature: 采样温度，控制输出的随机性。
            max_tokens: 生成内容的最大 Token 数。

        Returns:
            统一格式的 LLMResponse。

        Raises:
            httpx.HTTPStatusError: API 返回非 2xx 状态码时抛出。
            httpx.ConnectError: 网络连接失败时抛出。
            httpx.TimeoutException: 请求超时时抛出。
        """
        ...

    def close(self) -> None:
        """关闭底层 HTTP 连接，释放资源。"""
        self.client.close()


class OpenAICompatibleProvider(LLMProvider):
    """兼容 OpenAI Chat Completions API 格式的提供商。

    DeepSeek、Qwen（DashScope 兼容模式）与 OpenAI 均使用同一套
    /chat/completions 接口协议，故共用一个实现。
    """

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        """发送聊天请求并解析为统一的 LLMResponse。

        Args:
            messages: OpenAI 格式的对话消息列表。
            temperature: 采样温度，默认 0.7。
            max_tokens: 生成内容的最大 Token 数，默认 2000。

        Returns:
            解析后的 LLMResponse。

        Raises:
            httpx.HTTPStatusError: API 返回非 2xx 状态码时抛出。
            httpx.ConnectError: 网络连接失败时抛出。
            httpx.TimeoutException: 请求超时时抛出。
        """
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        response = self.client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

        content = data["choices"][0]["message"]["content"]
        usage_data = data.get("usage", {})
        usage = Usage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
        )
        return LLMResponse(content=content, usage=usage)


# ── 工厂函数 ─────────────────────────────────────────────────────────────


PROVIDER_CONFIG: dict[str, dict[str, str]] = {
    "deepseek": {
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url_env": "DEEPSEEK_BASE_URL",
        "model_env": "DEEPSEEK_MODEL",
        "default_base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
    },
    "qwen": {
        "api_key_env": "QWEN_API_KEY",
        "base_url_env": "QWEN_BASE_URL",
        "model_env": "QWEN_MODEL",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
    },
    "openai": {
        "api_key_env": "OPENAI_API_KEY",
        "base_url_env": "OPENAI_BASE_URL",
        "model_env": "OPENAI_MODEL",
        "default_base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
    },
}


def create_provider(provider_name: str | None = None) -> LLMProvider:
    """工厂函数：根据提供商名称创建对应的 LLM 客户端。

    提供商名称取参数值或环境变量 LLM_PROVIDER，默认 deepseek；API Key、
    Base URL、模型名均从对应提供商的环境变量读取。

    Args:
        provider_name: 提供商名称（deepseek / qwen / openai），
            默认读取环境变量 LLM_PROVIDER。

    Returns:
        配置完成的 LLMProvider 实例。

    Raises:
        ValueError: 提供商名称不在支持列表内时抛出。
        RuntimeError: 缺少对应提供商 API Key 时抛出。
    """
    name = (provider_name or os.getenv("LLM_PROVIDER", "deepseek")).lower()
    if name not in PROVIDER_CONFIG:
        raise ValueError(f"未知的模型提供商: {name}")

    config = PROVIDER_CONFIG[name]
    api_key = os.getenv(config["api_key_env"], "")
    if not api_key:
        raise RuntimeError(f"缺少 API Key，请设置环境变量: {config['api_key_env']}")

    base_url = os.getenv(config["base_url_env"], config["default_base_url"])
    model = os.getenv(config["model_env"], config["default_model"])

    logger.info("创建 LLM 客户端: provider=%s, model=%s", name, model)
    return OpenAICompatibleProvider(api_key=api_key, base_url=base_url, model=model)


# ── 带重试的调用封装 ─────────────────────────────────────────────────────


def chat_with_retry(
    provider: LLMProvider,
    messages: list[dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 2000,
    max_retries: int = 3,
    backoff_base: float = 2.0,
) -> LLMResponse:
    """带指数退避重试的聊天调用。

    对可恢复的 HTTP 错误（状态码错误、连接失败、超时）最多重试 max_retries
    次，重试间隔按 2^attempt 秒递增（1s、2s、4s）。

    Args:
        provider: LLM 提供商实例。
        messages: OpenAI 格式的对话消息列表。
        temperature: 采样温度，默认 0.7。
        max_tokens: 生成内容的最大 Token 数，默认 2000。
        max_retries: 最大重试次数，默认 3。
        backoff_base: 退避基数，第 n 次重试前等待 backoff_base**n 秒。

    Returns:
        成功调用的 LLMResponse。

    Raises:
        httpx.HTTPStatusError: 重试耗尽后最后一次请求仍失败时抛出。
        httpx.ConnectError: 重试耗尽后网络仍无法连接时抛出。
        httpx.TimeoutException: 重试耗尽后请求仍超时时抛出。
    """
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            response = provider.chat(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if attempt > 0:
                logger.info("第 %d 次重试成功", attempt)
            return response
        except (
            httpx.HTTPStatusError,
            httpx.ConnectError,
            httpx.TimeoutException,
        ) as exc:
            last_error = exc
            if attempt < max_retries - 1:
                wait_time = backoff_base**attempt
                logger.warning(
                    "LLM 调用失败（第 %d/%d 次），%.1fs 后重试: %s",
                    attempt + 1,
                    max_retries,
                    wait_time,
                    exc,
                )
                time.sleep(wait_time)
            else:
                logger.error("LLM 调用失败，已达最大重试次数: %s", exc)
    if last_error is None:
        raise RuntimeError("chat_with_retry 意外结束：无可用错误信息")
    raise last_error


# ── 便捷函数 ─────────────────────────────────────────────────────────────


def quick_chat(
    prompt: str,
    system: str = "你是一个 AI 技术分析助手。",
    provider_name: str | None = None,
) -> str:
    """快捷调用：一句话调用 LLM，返回纯文本内容。

    自动创建并关闭 provider，记录 Token 用量与估算成本。

    Args:
        prompt: 用户提示词。
        system: 系统提示词，默认扮演 AI 技术分析助手。
        provider_name: 提供商名称，默认读取环境变量 LLM_PROVIDER。

    Returns:
        模型返回的文本内容。

    Raises:
        ValueError: 提供商名称非法时抛出。
        RuntimeError: 缺少 API Key 时抛出。
        httpx.HTTPStatusError: 重试耗尽后请求仍失败时抛出。
        httpx.ConnectError: 网络连接失败时抛出。
        httpx.TimeoutException: 请求超时时抛出。
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    provider = create_provider(provider_name)
    try:
        response = chat_with_retry(provider, messages)
        cost = estimate_cost(provider.model, response.usage)
        logger.info(
            "Token 用量: %d (prompt) + %d (completion) = %d, 估算成本: $%.6f",
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
            response.usage.total_tokens,
            cost,
        )
        return response.content
    finally:
        provider.close()


def chat(
    prompt: str,
    system: str = "你是一个 AI 技术分析助手。",
    provider: str | None = None,
    max_retries: int = 3,
) -> dict[str, Any]:
    """便捷调用 LLM，返回包含 content 和 usage 的字典。

    Args:
        prompt: 用户提示词。
        system: 系统提示词，默认扮演 AI 技术分析助手。
        provider: 提供商名称（deepseek / qwen / openai），默认读环境变量。
        max_retries: 最大重试次数，默认 3。

    Returns:
        形如 {"content": str, "usage": {...}} 的字典，
        usage 包含 prompt_tokens / completion_tokens / total_tokens。

    Raises:
        ValueError: 提供商名称非法时抛出。
        RuntimeError: 缺少 API Key 时抛出。
        httpx.HTTPStatusError: 重试耗尽后请求仍失败时抛出。
        httpx.ConnectError: 网络连接失败时抛出。
        httpx.TimeoutException: 请求超时时抛出。
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    provider_name = provider or os.getenv("LLM_PROVIDER", "deepseek")
    llm = create_provider(provider_name)
    try:
        response = chat_with_retry(llm, messages, max_retries=max_retries)
        return response.to_dict()
    finally:
        llm.close()


# ── CLI 测试入口 ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    print("=== LLM 客户端测试 ===")
    print(f"提供商: {os.getenv('LLM_PROVIDER', 'deepseek')}")
    try:
        result = quick_chat("用一句话介绍什么是 AI Agent。")
        print(f"\n回复: {result}")
    except Exception as exc:  # noqa: BLE001 — CLI 测试入口需捕获任意异常
        print(f"\n错误: {exc}")
        print("请检查 .env 文件中的 API Key 配置。")
