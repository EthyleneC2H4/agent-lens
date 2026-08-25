"""Provider 抽象（mock-first）——所有 LLM 调用统一入口。

- MockProvider：确定性规则输出，无网络，测试与 demo 默认；返回估算 token 数。
- OpenAICompatibleProvider：可选真实模型路径。免费节点示例：
  NVIDIA 托管免费端点 https://integrate.api.nvidia.com/v1
  （model 如 nvidia/llama-3.3-nemotron-super-49b-v1）。
  key 只从环境变量读取；缺失时降级 mock 并提示。
  重试策略参数化（指数退避 + Retry-After 优先），transport 可注入以便离线测试。
  严禁硬编码付费服务端点或密钥。
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol


class NetworkError(RuntimeError):
    """可重试的网络类失败——runner 据此与任务失败分开计数。"""


class ProviderUnavailable(NetworkError):
    """provider 未配置可用凭据等不可用状态。"""


@dataclass
class ChatResult:
    """一次 chat 调用的结构化结果：文本 + token 记账 + 元信息。

    token 记账是一等公民：真实端点回填 usage 字段；mock 端给词数估算，
    保证离线路径也能走通成本汇总逻辑。
    """

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    latency_ms: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


Transport = Callable[[dict[str, Any]], dict[str, Any]]
Sleep = Callable[[float], None]


def _estimate_tokens(text: str) -> int:
    """粗估 token 数（词数近似）。仅用于 mock 路径的成本演练。"""
    return max(1, len(text.split()))


class BaseProvider(Protocol):
    def chat(self, messages: list[dict[str, str]]) -> ChatResult: ...


class MockProvider:
    """确定性 mock：对 prompt 做稳定哈希映射到固定词表。

    规则：prompt 含参考答案与 agent 输出时，做包含式匹配返回 yes/no，
    使 LLMJudgeScorer 在离线测试中有语义而非纯随机。
    """

    _WORDS = ("yes", "no", "maybe", "ok")

    def __init__(self, model: str = "mock") -> None:
        self.model = model

    def chat(self, messages: list[dict[str, str]]) -> ChatResult:
        text = "\n".join(m.get("content", "") for m in messages)
        gold_line = next((ln for ln in text.splitlines() if "参考答案" in ln), "")
        out_line = next((ln for ln in text.splitlines() if "Agent 输出" in ln), "")
        if gold_line and out_line:  # judge 形态的 prompt：做包含式语义判定
            gold = gold_line.split(":", 1)[-1].strip()
            out = out_line.split(":", 1)[-1].strip()
            answer = "yes" if (gold and out and gold.strip() == out.strip()) else "no"
        else:
            digest = hashlib.sha256(text.encode()).hexdigest()
            answer = self._WORDS[int(digest[:8], 16) % len(self._WORDS)]
        return ChatResult(
            text=answer,
            prompt_tokens=_estimate_tokens(text),
            completion_tokens=_estimate_tokens(answer),
            model=self.model,
        )


class _Retryable(Exception):
    """内部：携带 Retry-After 的可重试传输错误。"""

    def __init__(self, msg: str, retry_after: float | None = None) -> None:
        super().__init__(msg)
        self.retry_after = retry_after


def http_transport(base_url: str, api_key: str, timeout_s: float) -> Transport:
    """默认 urllib transport：把 HTTP 层错误翻译成可重试/不可重试两类。"""
    import json
    import urllib.error
    import urllib.request

    def _send(body: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(  # noqa: S310
            f"{base_url.rstrip('/')}/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
                return dict(json.load(resp))
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                ra = e.headers.get("Retry-After") if e.headers else None
                delay = float(ra) if ra and ra.replace(".", "").isdigit() else None
                raise _Retryable(f"HTTP {e.code}", retry_after=delay) from e
            raise RuntimeError(f"HTTP {e.code}（不可重试）") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise _Retryable(f"网络错误: {e}") from e

    return _send


class OpenAICompatibleProvider:
    """OpenAI-compatible chat completions 客户端（stdlib urllib，零重依赖）。

    重试策略：max_retries 次指数退避（backoff_base_s * 2^(n-1)）；
    服务端显式 Retry-After 时优先采用。429/5xx/网络错视为可重试，
    其余 4xx 直接抛错。sleep 可注入保证测试确定性。
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key_env: str = "AGENTLENS_API_KEY",
        timeout_s: float = 60.0,
        max_retries: int = 3,
        backoff_base_s: float = 1.0,
        transport: Transport | None = None,
        sleep: Sleep | None = None,
    ) -> None:
        # 默认指向 NVIDIA 免费托管端点（免费层）；可被环境变量覆盖
        self.base_url = base_url or os.environ.get(
            "AGENTLENS_BASE_URL", "https://integrate.api.nvidia.com/v1"
        )
        self.model = model or os.environ.get(
            "AGENTLENS_MODEL", "nvidia/llama-3.3-nemotron-super-49b-v1"
        )
        self.api_key = os.environ.get(api_key_env, "")
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.timeout_s = timeout_s
        self.fallback_reason = "" if self.api_key else f"missing env {api_key_env}"
        self._transport = transport
        self._sleep = sleep or time.sleep

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def chat(self, messages: list[dict[str, str]]) -> ChatResult:
        if not self.available:
            raise ProviderUnavailable(
                f"真实 provider 不可用：{self.fallback_reason}；请回退 MockProvider"
            )
        body: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0}
        transport = self._transport or http_transport(self.base_url, self.api_key, self.timeout_s)

        start = time.monotonic()
        resp = self._with_backoff(transport, body)
        latency_ms = int((time.monotonic() - start) * 1000)
        usage = resp.get("usage") or {}
        return ChatResult(
            text=str(resp["choices"][0]["message"]["content"]),
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            model=self.model,
            latency_ms=latency_ms,
        )

    def _with_backoff(self, transport: Transport, body: dict[str, Any]) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                return transport(body)
            except _Retryable as e:
                attempt += 1
                if attempt > self.max_retries:
                    raise NetworkError(f"重试 {self.max_retries} 次后仍失败: {e}") from e
                delay = e.retry_after or self.backoff_base_s * (2 ** (attempt - 1))
                self._sleep(delay)


def default_provider() -> BaseProvider:
    """mock-first 入口：有 key 用真实免费节点，否则 mock 并打印一行提示。"""
    real = OpenAICompatibleProvider()
    if real.available:
        return real
    print(f"[lens] 未配置真实模型（{real.fallback_reason}），使用 MockProvider")
    return MockProvider()


def select_provider(name: str) -> BaseProvider:
    """按名字选择 provider：real 缺 key 时自动回落 mock（调用方已打印提示）。"""
    if name == "real":
        real = OpenAICompatibleProvider()
        if real.available:
            return real
        print(f"[lens] --provider real 但未配置 key（{real.fallback_reason}），回落 MockProvider")
    return MockProvider()
