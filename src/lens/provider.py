"""Provider 抽象（mock-first）——所有 LLM 调用统一入口。

- MockProvider：确定性规则输出，无网络，测试与 demo 默认。
- OpenAICompatibleProvider：可选真实模型路径。免费节点示例：
  NVIDIA 托管免费端点 https://integrate.api.nvidia.com/v1
  （model 如 nvidia/llama-3.3-nemotron-super-49b-v1）。
  key 只从环境变量读取；缺失时降级 mock 并提示。
  严禁硬编码付费服务端点或密钥。
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Protocol


class BaseProvider(Protocol):
    def chat(self, messages: list[dict[str, str]]) -> str: ...


class MockProvider:
    """确定性 mock：对 prompt 做稳定哈希映射到固定词表。

    规则：prompt 含参考答案与 agent 输出时，做包含式匹配返回 yes/no，
    使 LLMJudgeScorer 在离线测试中有语义而非纯随机。
    """

    _WORDS = ("yes", "no", "maybe", "ok")

    def chat(self, messages: list[dict[str, str]]) -> str:
        text = "\n".join(m.get("content", "") for m in messages)
        gold_line = next((ln for ln in text.splitlines() if "参考答案" in ln), "")
        out_line = next((ln for ln in text.splitlines() if "Agent 输出" in ln), "")
        if gold_line and out_line:  # judge 形态的 prompt：做包含式语义判定
            gold = gold_line.split(":", 1)[-1].strip()
            out = out_line.split(":", 1)[-1].strip()
            return "yes" if (gold and out and gold.strip() == out.strip()) else "no"
        digest = hashlib.sha256(text.encode()).hexdigest()
        return self._WORDS[int(digest[:8], 16) % len(self._WORDS)]


class OpenAICompatibleProvider:
    """OpenAI-compatible chat completions 客户端（stdlib urllib，零重依赖）。"""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key_env: str = "AGENTLENS_API_KEY",
    ) -> None:
        # 默认指向 NVIDIA 免费托管端点（免费层）；可被环境变量覆盖
        self.base_url = base_url or os.environ.get(
            "AGENTLENS_BASE_URL", "https://integrate.api.nvidia.com/v1"
        )
        self.model = model or os.environ.get(
            "AGENTLENS_MODEL", "nvidia/llama-3.3-nemotron-super-49b-v1"
        )
        self.api_key = os.environ.get(api_key_env, "")
        self._fallback_reason = "" if self.api_key else f"missing env {api_key_env}"

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def chat(self, messages: list[dict[str, str]]) -> str:
        if not self.available:
            raise RuntimeError(
                f"真实 provider 不可用：{self._fallback_reason}；请回退 MockProvider"
            )
        import json
        import urllib.request

        req = urllib.request.Request(  # noqa: S310
            f"{self.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(
                {"model": self.model, "messages": messages, "temperature": 0}
            ).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
            payload: dict[str, Any] = json.load(resp)
        return str(payload["choices"][0]["message"]["content"])


def default_provider() -> BaseProvider:
    """mock-first 入口：有 key 用真实免费节点，否则 mock 并打印一行提示。"""
    real = OpenAICompatibleProvider()
    if real.available:
        return real
    print(f"[lens] 未配置真实模型（{real._fallback_reason}），使用 MockProvider")
    return MockProvider()
