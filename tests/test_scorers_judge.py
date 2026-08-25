import pytest

from lens.judge_lab import (
    agreement_rate,
    cohens_kappa,
    length_bias_check,
    position_swap_check,
)
from lens.scorers import ExactMatchScorer, KeyStateScorer, LLMJudgeScorer
from lens.store import Trajectory


def test_exact_match_numeric_tolerance():
    s = ExactMatchScorer()
    t = Trajectory(task_id="t", version="v", run_id="r", output="1,234.50 元")
    assert s.score(t, {"gold": "1234.5"}) is True


def test_exact_match_text_mismatch():
    s = ExactMatchScorer()
    t = Trajectory(task_id="t", version="v", run_id="r", output="wrong")
    assert s.score(t, {"gold": "right"}) is False


def test_key_state_checks_steps_not_output():
    s = KeyStateScorer()
    task = {"required_states": ["cart_size=1"]}
    did_it = Trajectory(
        task_id="t", version="v", run_id="r",
        output="已加入购物车", steps=["click add_to_cart", "cart_size=1"],
    )
    said_it = Trajectory(
        task_id="t", version="v", run_id="r",
        output="cart_size=1 已加购", steps=["click buy_now"],
    )
    assert s.score(did_it, task) is True
    assert s.score(said_it, task) is False  # 只在输出里「说」不算数


def _traj(output):
    return Trajectory(task_id="t", version="v", run_id="r", output=output)


def test_mock_judge_semantic_yes_no():
    j = LLMJudgeScorer(provider=None)
    assert j.score(_traj("384"), {"gold": "384"}) is True     # MockProvider 相等判 yes
    assert j.score(_traj("999"), {"gold": "384"}) is False


def test_kappa_perfect_and_random():
    human = [True, False, True, True, False]
    assert cohens_kappa(human, human) == pytest.approx(1.0)
    flipped = [not x for x in human]
    assert cohens_kappa(human, flipped) < 0   # 完全反向为负一致


def test_kappa_disagreement_penalty():
    a = [True] * 8 + [False] * 2
    b = [True] * 8 + [False, True]
    kappa = cohens_kappa(a, b)
    assert 0 < kappa < agreement_rate(a, b)   # κ 剔除随机一致的惩罚效应


def test_position_swap_and_length_bias():
    fwd = [True, True, False, True, False, True]
    swp = [True, True, True, True, False, False]
    rate = position_swap_check(fwd, swp)
    assert 0 <= rate <= 1
    bias = length_bias_check([True, False, True, True], [5, 500, 30, 60])
    assert set(bias) == {"short", "mid", "long"}
