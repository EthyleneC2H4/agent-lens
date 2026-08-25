"""版本间回归 diff 与门禁规则 —— 回答「这个改动能不能合入」。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .metrics import bootstrap_ci


@dataclass
class CaseDiff:
    task_id: str
    base_passes: int
    cand_passes: int
    trials: int
    status: str = "unchanged"  # improved / regressed / fragile / unchanged

    def classify(self) -> str:
        if self.cand_passes > self.base_passes:
            self.status = "improved"
        elif self.cand_passes < self.base_passes:
            self.status = "regressed"
        elif 0 < self.cand_passes < self.trials:
            self.status = "fragile"  # 通过率方差大的脆弱 case
        else:
            self.status = "unchanged"
        return self.status


@dataclass
class GatePolicy:
    """门禁策略：observe 只报告不阻断；block 超阈值即 fail。

    judge κ 达标前应处于 observe 模式 —— 门禁分级是平台核心语义。
    """

    mode: str = "observe"            # observe / block
    max_regressed_cases: int = 0     # 允许的退化 case 数
    min_success_rate: float | None = None   # 候选版本最低通过率（点估计）
    extra: dict[str, object] = field(default_factory=dict)


def diff_versions(
    base_results: dict[str, list[bool]], cand_results: dict[str, list[bool]]
) -> list[CaseDiff]:
    """按 case 对齐两版本的通过数，产出分类 diff。"""
    diffs = []
    for task_id in sorted(base_results):
        base_trials = base_results[task_id]
        cand_trials = cand_results.get(task_id, [])
        d = CaseDiff(
            task_id=task_id,
            base_passes=sum(base_trials),
            cand_passes=sum(cand_trials),
            trials=max(len(base_trials), len(cand_trials)),
        )
        d.classify()
        diffs.append(d)
    return diffs


def evaluate_gate(
    diffs: list[CaseDiff],
    policy: GatePolicy,
    cand_success_rate: float,
) -> tuple[bool, list[str]]:
    """返回 (是否放行, 违规原因列表)。observe 模式永远放行但给出警告。"""
    violations: list[str] = []
    regressed = [d for d in diffs if d.status == "regressed"]
    if len(regressed) > policy.max_regressed_cases:
        violations.append(
            f"退化 case 数 {len(regressed)} 超过阈值 {policy.max_regressed_cases}: "
            f"{[d.task_id for d in regressed]}"
        )
    if policy.min_success_rate is not None and cand_success_rate < policy.min_success_rate:
        violations.append(
            f"候选版本通过率 {cand_success_rate:.3f} 低于下限 {policy.min_success_rate:.3f}"
        )
    allowed = True if policy.mode == "observe" else not violations
    return allowed, violations


def regression_summary(
    base_task_scores: dict[str, list[float]],
    cand_task_scores: dict[str, list[float]],
) -> dict[str, dict[str, object]]:
    """case 级分数的 bootstrap 区间对比（区间重叠 = 差异可能只是噪声）。"""
    out: dict[str, dict[str, object]] = {}
    for task_id, base_vals in base_task_scores.items():
        cand_vals = cand_task_scores.get(task_id, [])
        if not base_vals or not cand_vals:
            continue
        bp, blo, bhi = bootstrap_ci(base_vals, n_boot=500)
        cp, clo, chi = bootstrap_ci(cand_vals, n_boot=500)
        out[task_id] = {
            "base_point": round(bp, 4),
            "cand_point": round(cp, 4),
            "cand_ci": (round(clo, 4), round(chi, 4)),
            "ci_overlap": not (chi < blo or bhi < clo),
        }
    return out
