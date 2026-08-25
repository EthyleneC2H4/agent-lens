"""TRAIL 式自检 runner —— 评测系统自身的元评测。

scorer 对已知好/坏轨迹的分辨力必须满分才允许上岗；这是「评测器先过体检，
再去评测别人」的元评测闭环（TRAIL: Trajectory-level Audit & Integrity）。
"""

from __future__ import annotations

from dataclasses import dataclass

from .scorers import ExactMatchScorer, KeyStateScorer, LLMJudgeScorer
from .store import Trajectory


@dataclass
class ScorerCase:
    """一条自检用例：轨迹 + 任务字典 + 期望判定。"""

    desc: str
    traj: Trajectory
    task: dict
    expect_pass: bool


@dataclass
class ScorerCheck:
    """一个 scorer 的自检结果。discrimination = min(TPR, TNR)。"""

    scorer: str
    n_cases: int
    tpr: float            # 已知好轨迹被判通过的比例
    tnr: float            # 已知坏轨迹被判失败的比例
    failures: list[str]

    @property
    def ok(self) -> bool:
        return not self.failures


def _traj(output: str, steps: list[str] | None = None) -> Trajectory:
    return Trajectory(task_id="selfcheck", version="v", run_id="meta", output=output,
                      steps=steps or [])


def exact_match_suite() -> tuple[list[ScorerCase], ExactMatchScorer]:
    """exact_match 自检集：数值容差的两侧都要卡住。"""
    cases = [
        ScorerCase("文本全等", _traj("HELLO WORLD"), {"gold": "HELLO WORLD"}, True),
        ScorerCase("数值等价（千分位+货币+单位）",
                   _traj("1,234.50 元"), {"gold": "1234.5"}, True),
        ScorerCase("数值容差内", _traj("3.1415926"), {"gold": "3.14159"}, True),
        ScorerCase("错误数字", _traj("385"), {"gold": "384"}, False),
        ScorerCase("错误文本", _traj("wrong answer"), {"gold": "right answer"}, False),
        ScorerCase("空输出", _traj(""), {"gold": "42"}, False),
    ]
    return cases, ExactMatchScorer()


def key_state_suite() -> tuple[list[ScorerCase], KeyStateScorer]:
    """key_state 自检集：「嘴上完成」必须被识破。"""
    cases = [
        ScorerCase("状态齐全", _traj("done", ["add_to_cart", "cart_size=1"]),
                   {"required_states": ["cart_size=1"]}, True),
        ScorerCase("只在输出里说（没做）",
                   _traj("cart_size=1 已加购", ["click buy_now"]),
                   {"required_states": ["cart_size=1"]}, False),
        ScorerCase("缺关键状态", _traj("ok", ["step1", "step2"]),
                   {"required_states": ["cart_size=1", "paid=true"]}, False),
        ScorerCase("多状态全命中",
                   _traj("", ["a", "cart_size=2", "paid=true"]),
                   {"required_states": ["cart_size=2", "paid=true"]}, True),
    ]
    return cases, KeyStateScorer()


def llm_judge_suite(provider=None) -> tuple[list[ScorerCase], LLMJudgeScorer]:
    """llm_judge 自检集：MockProvider 语义判定的确定性边界。"""
    cases = [
        ScorerCase("语义相符", _traj("384"), {"gold": "384", "input": "128+256?"}, True),
        ScorerCase("语义不符", _traj("999"), {"gold": "384", "input": "128+256?"}, False),
    ]
    return cases, LLMJudgeScorer(provider=provider)


DEFAULT_SUITES = (exact_match_suite, key_state_suite)


def run_meta_eval(suites=None) -> list[ScorerCheck]:
    """跑全部自检套件；llm_judge 用 mock provider（离线确定性）。"""
    suites = suites or DEFAULT_SUITES
    checks = []
    all_suites = [*suites, llm_judge_suite]
    for suite in all_suites:
        cases, scorer = suite()
        hits = [scorer.score(c.traj, c.task) for c in cases]
        tpr_n = sum(1 for c, h in zip(cases, hits) if c.expect_pass)
        tnr_n = len(cases) - tpr_n
        tp = sum(1 for c, h in zip(cases, hits) if c.expect_pass and h)
        tn = sum(1 for c, h in zip(cases, hits) if not c.expect_pass and not h)
        failures = [
            f"{c.desc}: expect={'pass' if c.expect_pass else 'fail'} got={h}"
            for c, h in zip(cases, hits) if h != c.expect_pass
        ]
        checks.append(ScorerCheck(
            scorer=scorer.name, n_cases=len(cases),
            tpr=tp / tpr_n if tpr_n else 1.0,
            tnr=tn / tnr_n if tnr_n else 1.0,
            failures=failures,
        ))
    return checks
