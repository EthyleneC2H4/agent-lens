"""Scorer 协议与规则/judge 两类实现。判定与执行解耦：scorer 只吃轨迹。"""

from __future__ import annotations

import re
from typing import Protocol

from .store import Trajectory


class Scorer(Protocol):
    name: str

    def score(self, traj: Trajectory, task: dict) -> bool: ...


_NUM_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def normalize_number(text: str) -> str | None:
    """归一化数字：剥千分位/货币符号/单位/%，返回规范字符串；非数字返回 None。"""
    import re as _re

    t = _re.sub(r"(?i)\b(usd|cny|eur)\b", "", text)
    for ch in (",", "$", "%", "¥", "€", "£", "元", " "):
        t = t.replace(ch, "")
    t = t.strip().rstrip(".")
    try:
        val = float(t)
    except ValueError:
        return None
    return repr(round(val, 6))


class ExactMatchScorer:
    """exact match：支持数值归一化容差（relative tolerance）。"""

    def __init__(self, numeric_tol: float = 1e-6) -> None:
        self.numeric_tol = numeric_tol
        self.name = "exact_match"

    def score(self, traj: Trajectory, task: dict) -> bool:
        gold = str(task.get("gold", ""))
        out = traj.output.strip()
        if out == gold.strip():
            return True
        gnum, onum = normalize_number(gold), normalize_number(out)
        if gnum is not None and onum is not None:
            gv, ov = float(gnum), float(onum)
            return abs(gv - ov) <= self.numeric_tol * max(1.0, abs(gv))
        return False


class KeyStateScorer:
    """关键状态断言：task['required_states'] 中所有 'k=v' 片段都出现在轨迹步骤里。

    BFCL V3 state-based evaluation 的 mini 版：只看记录下来的世界状态
    （traj.steps），不看最终输出说了什么——防止 agent 「嘴上完成」。
    """

    def __init__(self) -> None:
        self.name = "key_state"

    def score(self, traj: Trajectory, task: dict) -> bool:
        required: list[str] = list(task.get("required_states", []))
        joined = "\n".join(traj.steps)
        return all(state in joined for state in required)


class LLMJudgeScorer:
    """rubric 化 judge —— 走 BaseProvider；MockProvider 给确定性分数。

    判定可重放：对同一批轨迹换 judge 模型重判分是 AgentLens 的一等公民操作。
    judge 自身的 token 消耗在 usage_totals 里累计，供成本汇总。
    """

    def __init__(self, provider=None, rubric: str | None = None) -> None:
        self.provider = provider  # 延迟导入避免环；None 时用 mock
        self.rubric = rubric or "输出是否正确完成任务？只回答 yes 或 no。"
        self.name = "llm_judge"
        self.usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}

    def _chat(self, prompt: str) -> str:
        if self.provider is not None:
            res = self.provider.chat([{"role": "user", "content": prompt}])
            self.usage_totals["prompt_tokens"] += res.prompt_tokens
            self.usage_totals["completion_tokens"] += res.completion_tokens
            self.usage_totals["calls"] += 1
            return res.text
        from .provider import MockProvider

        return MockProvider().chat([{"role": "user", "content": prompt}]).text

    def score(self, traj: Trajectory, task: dict) -> bool:
        gold = str(task.get("gold", ""))
        prompt = (
            f"{self.rubric}\n任务: {task.get('input', '')}\n"
            f"参考答案: {gold}\nAgent 输出: {traj.output}\n回答 yes/no:"
        )
        answer = self._chat(prompt).strip().lower()
        return answer.startswith("yes")
