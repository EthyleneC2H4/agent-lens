"""Phase C：judge 校准闭环 —— 池构造 / 分层 / κ 报告（手算数字）/ swap 一致性。"""

import pytest

from lens.calibration import (
    CalibItem,
    build_pairs,
    build_pool,
    kappa_report,
    make_noisy_judge,
    make_pair_judge,
    render_kappa_html,
    render_review_html,
    resolve_judge,
    stratified_queue,
    swap_consistency,
)


def test_pool_size_deterministic_and_categories():
    p1, p2 = build_pool(seed=0), build_pool(seed=0)
    assert len(p1) >= 200                      # ≥200 例硬指标
    assert p1 == p2                            # 同 seed 完全可复现
    cats = {i.category for i in p1}
    assert {"arith_add", "pct_format", "truncation", "refusal"} <= cats
    # 构造真值自洽：正确变体的输出必须等于参考答案（数值类）
    for it in p1[:20]:
        if it.truth and it.category == "arith_add":
            assert it.agent_output == it.gold


def test_stratified_queue_keeps_all_disagreements():
    pool = build_pool(seed=0)
    judge = resolve_judge("numeric")
    queue, prelabels, stats = stratified_queue(pool, judge, queue_size=240)
    assert len(queue) == len(prelabels) > 0
    diverged_ids = {
        it.id for it in pool if judge(it.task_input, it.gold, it.agent_output) != it.truth
    }
    queued_ids = {it.id for it in queue}
    assert diverged_ids <= queued_ids          # 分歧项一个不丢
    assert any(b.key.endswith("diverge") for b in stats)


def test_review_html_structure():
    import re

    pool = build_pool(seed=0)[:10]
    html = render_review_html(pool, ["yes", "no"] * 5)
    assert html.count("<fieldset") == 10
    assert not re.search(r'<input type="radio"[^>]*\bchecked', html)  # 建议不预选（防锚定）
    assert "exportLabels" in html


def _mini_pool() -> list[CalibItem]:
    mk = lambda i, out, t: CalibItem(id=f"x{i}", category="c", task_input="q",  # noqa: E731
                                     gold="384", agent_output=out, truth=t)
    return [mk(1, "384", True), mk(2, "385", False), mk(3, "384", True), mk(4, "383", False)]


def test_kappa_report_hand_computed_numbers():
    """手算用例：κ(judge_exact, human) = 0.5；FP=0/FN=1。"""
    pool = _mini_pool()
    labels = [
        {"item_id": "x1", "human_label": True},
        {"item_id": "x2", "human_label": False},
        {"item_id": "x3", "human_label": False},   # 与构造真值相悖（故意）
        {"item_id": "x4", "human_label": False},
    ]
    rep = kappa_report(pool, labels, judge=resolve_judge("exact"), n_boot=50)

    jvh = rep["judge_vs_human"]
    assert jvh["kappa"] == pytest.approx(0.5)      # (0.75-0.5)/0.5 手算值
    assert jvh["fp"] == 0 and jvh["fn"] == 1
    assert jvh["false_block_rate"] == 0.0
    assert jvh["miss_rate"] == 1.0

    hvt = rep["human_vs_truth"]
    assert hvt["fp"] == 1                          # 人工把 x3 判错 → 标注自查可见
    lo, hi = rep["kappa_ci95"]
    assert lo <= 0.5 <= hi or lo <= hi             # CI 结构有效
    assert set(rep["length_bias"]) == {"short", "mid", "long"}
    html = render_kappa_html(rep)
    assert "Judge vs 人工" in html and "0.5" in html


def test_swap_consistency_perfect_for_clean_pair_judge():
    pairs = build_pairs(n=12, seed=0)
    clean = make_pair_judge(resolve_judge("numeric"))
    assert swap_consistency(pairs, clean) == 1.0   # 差距明确时无位置翻转

    flaky = make_pair_judge(make_noisy_judge(resolve_judge("numeric"), 0.3, seed=3))
    assert swap_consistency(pairs, flaky) < 1.0


def test_resolve_judge_specs():
    assert resolve_judge("exact")("q", "384", "384") is True
    assert resolve_judge("numeric")("q", "37.5%", "37.5 %") is True     # 格式宽容
    assert resolve_judge("noisy:p=0.0")("q", "384", "384") is True
    assert resolve_judge("noisy:p=1.0,seed=1")("q", "384", "384") is False  # 全翻转
    with pytest.raises(ValueError):
        resolve_judge("unknown")
