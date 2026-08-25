"""失败轨迹模式聚类 —— 把「挂了」拆成可行动的失败类型。

启发式分类：工具错 / 格式错（差一点就对）/ 规划错 / 空输出拒答 / 未知。
只做离线规则，不依赖 LLM；输入是 store 里的历史轨迹。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .scorers import ExactMatchScorer
from .store import Trajectory

TOOL_ERROR_PAT = re.compile(
    r"traceback|exception|error|failed to|timeout|timed out|\b5\d\d\b|\b4\d\d\b",
    re.IGNORECASE,
)
REFUSAL_PAT = re.compile(r"无法|抱歉|不能|sorry|i can't|i cannot|无输出", re.IGNORECASE)


@dataclass
class FailureBucket:
    category: str
    desc: str
    task_ids: list[str] = field(default_factory=list)


CATEGORIES: dict[str, str] = {
    "empty": "空输出/拒答——prompt 或上下文问题",
    "tool_error": "工具/环境错误——步骤里出现异常痕迹",
    "format_error": "格式近似错——归一化后与参考答案接近",
    "planning_error": "规划错——无工具异常且答案方向跑偏",
    "unknown": "未知——需要人工看轨迹",
}


def _is_format_near_miss(output: str, gold: str) -> bool:
    """剥掉大小写/标点/空白后相等 → 差一点就对。"""
    def norm(s: str) -> str:
        return re.sub(r"[^\w]+", "", s).lower()
    return bool(gold.strip()) and norm(output) == norm(gold) and output.strip() != gold.strip()


def classify_failure(traj: Trajectory, gold: str) -> str:
    """单条失败轨迹的类别判定（按优先级早停）。"""
    if not traj.output.strip() or REFUSAL_PAT.search(traj.output):
        return "empty"
    if any(TOOL_ERROR_PAT.search(s) for s in traj.steps):
        return "tool_error"
    if _is_format_near_miss(traj.output, gold):
        return "format_error"
    if traj.steps and not TOOL_ERROR_PAT.search("\n".join(traj.steps)):
        # 有完整步骤但答案跑偏 → 规划层走错了路
        return "planning_error"
    return "unknown"


def analyze_failures(trajs: list[Trajectory]) -> dict[str, FailureBucket]:
    """对一批轨迹重放评分（store-first），把失败轨迹聚成桶。"""
    scorer = ExactMatchScorer()
    buckets = {k: FailureBucket(category=k, desc=v) for k, v in CATEGORIES.items()}
    n_fail = 0
    for t in trajs:
        ok = scorer.score(t, {"gold": str(t.metadata.get("gold", ""))})
        if ok:
            continue
        n_fail += 1
        cat = classify_failure(t, str(t.metadata.get("gold", "")))
        buckets[cat].task_ids.append(t.task_id)
    if n_fail == 0:
        return {}
    return {k: v for k, v in buckets.items() if v.task_ids}
