import pytest

from lens.metrics import bootstrap_ci, pass_at_k, pass_caret_k, pass_hat_k
from lens.regression import (
    CaseDiff,
    GatePolicy,
    diff_versions,
    evaluate_gate,
)


def test_pass_at_k_hand_computed():
    assert pass_at_k(2, 1, 1) == pytest.approx(0.5)   # = c/n
    assert pass_at_k(5, 3, 2) == pytest.approx(0.9)   # 1 - C(2,2)/C(5,2)
    assert pass_at_k(4, 4, 4) == 1.0
    assert pass_at_k(4, 0, 3) == 0.0


def test_pass_at_k_bounds_and_errors():
    with pytest.raises(ValueError):
        pass_at_k(2, 0, 3)
    with pytest.raises(ValueError):
        pass_at_k(2, -1, 1)


def test_pass_caret_k_monotone_decreasing():
    results = [[True, True, False], [False, False, False], [True, True, True]]
    vals = [pass_caret_k(results, k) for k in (1, 2, 3)]
    assert all(a >= b for a, b in zip(vals, vals[1:]))
    assert vals[0] == pytest.approx(sum(sum(r) / len(r) for r in results) / 3)


def test_pass_hat_k_optimistic_vs_pass_caret_pessimistic():
    results = [[True, False] * 3]
    assert pass_hat_k(results, 6) == 1.0            # 至少一次通过
    assert pass_caret_k(results, 6) < 1.0           # 全过不可能


def test_bootstrap_ci_contains_point():
    point, lo, hi = bootstrap_ci([0.2, 0.4, 0.5, 0.6, 0.8], n_boot=1000, seed=3)
    assert lo <= point <= hi
    assert 0 <= lo <= hi <= 1


def test_diff_versions_classifies():
    base = {"t1": [True, True], "t2": [True, False], "t3": [False, False]}
    cand = {"t1": [True, False], "t2": [True, True], "t3": [False, False]}
    diffs = {d.task_id: d for d in diff_versions(base, cand)}
    assert diffs["t1"].status == "regressed"
    assert diffs["t2"].status == "improved"
    assert diffs["t3"].status == "unchanged"


def test_gate_observe_always_allows_block_blocks():
    diffs = [CaseDiff(task_id="t", base_passes=2, cand_passes=0, trials=2)]
    diffs[0].classify()
    allowed, violations = evaluate_gate(diffs, GatePolicy(mode="observe"), cand_success_rate=0.0)
    assert allowed is True
    allowed_b, violations_b = evaluate_gate(diffs, GatePolicy(mode="block"), cand_success_rate=0.0)
    assert allowed_b is False
    assert any("退化" in v for v in violations_b)
