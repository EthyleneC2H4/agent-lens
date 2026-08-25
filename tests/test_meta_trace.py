"""Phase D：TRAIL 自检 meta-eval + 失败轨迹聚类。"""

from lens.meta_eval import run_meta_eval
from lens.store import Trajectory
from lens.trace_analyzer import analyze_failures, classify_failure


def test_meta_eval_all_scorers_fully_discriminating():
    checks = run_meta_eval()
    assert {c.scorer for c in checks} == {"exact_match", "key_state", "llm_judge"}
    for c in checks:
        assert c.ok, f"{c.scorer} 失格: {c.failures}"
        assert c.tpr == 1.0 and c.tnr == 1.0   # 已知好/坏必须全部分辨


def test_meta_eval_catches_injected_broken_scorer():
    """元评测的自检：判定反转的坏 scorer 必须被自检抓住。"""
    from lens.meta_eval import ScorerCase, exact_match_suite
    from lens.scorers import ExactMatchScorer

    class BrokenScorer(ExactMatchScorer):
        def __init__(self) -> None:
            super().__init__()
            self.name = "broken"

        def score(self, traj, task):
            return not super().score(traj, task)

    def broken_suite() -> tuple[list[ScorerCase], BrokenScorer]:
        cases, _ = exact_match_suite()
        return cases, BrokenScorer()

    checks = run_meta_eval(suites=[broken_suite])
    broken = next(c for c in checks if c.scorer == "broken")
    assert not broken.ok and broken.failures


def test_classify_failure_buckets():
    def traj(output, steps=None):
        return Trajectory(task_id="t", version="v", run_id="r", output=output,
                          steps=steps or [])

    assert classify_failure(traj(""), "42") == "empty"
    assert classify_failure(traj("抱歉我无法完成"), "42") == "empty"
    assert classify_failure(traj("ans", ["call_api error 500"]), "42") == "tool_error"
    assert classify_failure(traj("answer"), "ANSWER") == "format_error"   # 大小写近似
    assert classify_failure(traj("wrong answer", ["plan", "act"]), "42") == "planning_error"
    assert classify_failure(traj("???", []), "42") == "unknown"


def test_analyze_failures_groups_and_skips_passes():
    trajs = [
        Trajectory(task_id="ok-1", version="v", run_id="r", output="42",
                   metadata={"gold": "42"}),
        Trajectory(task_id="tool-1", version="v", run_id="r",
                   steps=["search timeout"], output="x", metadata={"gold": "42"}),
        Trajectory(task_id="fmt-1", version="v", run_id="r", output="answer",
                   metadata={"gold": "ANSWER"}),
    ]
    buckets = analyze_failures(trajs)
    assert set(buckets) == {"tool_error", "format_error"}
    assert buckets["tool_error"].task_ids == ["tool-1"]
