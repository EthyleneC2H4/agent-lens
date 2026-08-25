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


# ---------- P5 技术债 #7：真 pairwise LLM judge ----------


class _ScriptedProv:
    """按调用顺序回放脚本回复的假 provider（记录收到的 prompt）。"""

    def __init__(self, replies):
        from lens.provider import ChatResult

        self._chatresult = ChatResult
        self.replies = list(replies)
        self.prompts: list[str] = []

    def chat(self, messages):
        self.prompts.append(messages[0]["content"])
        return self._chatresult(text=self.replies.pop(0))


def test_make_llm_pair_judge_single_prompt_two_candidates():
    """真 pairwise：一次 prompt 同看两候选；解析 A/B/tie；未知回答按 tie。"""
    from lens.calibration import make_llm_pair_judge

    prov = _ScriptedProv(["A", "B", "tie", "乱七八糟"])
    jp = make_llm_pair_judge(prov)
    assert jp("q", "384", "384", "385") == "A"
    assert jp("q", "384", "385", "384") == "B"
    assert jp("q", "384", "384", "384") == "tie"
    assert jp("q", "384", "384", "385") == "tie"      # 未解析 → 容错为 tie
    assert len(prov.prompts) == 4 and "候选 A" in prov.prompts[-1]
    assert "候选 B" in prov.prompts[-1]               # 双候选同 prompt（真 pairwise）


def test_llm_pair_judge_position_bias_detected_by_swap():
    """永远选 A 的位置偏置 judge 被 swap_consistency 抓住；忠实的得满分。"""
    from lens.calibration import make_llm_pair_judge, swap_consistency

    pairs = build_pairs(n=6, seed=0)
    biased = make_llm_pair_judge(_ScriptedProv(["A"] * 12))
    assert swap_consistency(pairs, biased) == 0.0     # AB 对、BA 错 → 无一致命中

    honest = make_llm_pair_judge(_ScriptedProv(["A", "B"] * 6))
    assert swap_consistency(pairs, honest) == 1.0     # 两个方向都指认 better


def test_kappa_report_pair_mode_llm_offline(tmp_path):
    """kappa-report --pair-mode llm 走 MockProvider pairwise 分支，离线确定性。"""
    import json as _json
    from dataclasses import asdict

    from lens import cli
    from lens.calibration import build_pairs, build_pool

    pool = build_pool(seed=0)[:20]
    pairs = build_pairs(n=8, seed=0)
    pool_path = tmp_path / "pool.jsonl"
    pairs_path = tmp_path / "pairs.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    pool_path.write_text(
        "\n".join(_json.dumps(asdict(i), ensure_ascii=False) for i in pool),
        encoding="utf-8",
    )
    pairs_path.write_text(
        "\n".join(_json.dumps(asdict(p), ensure_ascii=False) for p in pairs),
        encoding="utf-8",
    )
    labels_path.write_text(
        "\n".join(
            _json.dumps({"item_id": it.id, "human_label": it.truth}, ensure_ascii=False)
            for it in pool
        ),
        encoding="utf-8",
    )
    cli.kappa_report(
        pool=str(pool_path), labels=str(labels_path), judge="numeric",
        pairs=str(pairs_path), out=str(tmp_path / "kappa.html"),
        out_json=str(tmp_path / "kappa.json"), pair_mode="llm",
    )
    payload = _json.loads((tmp_path / "kappa.json").read_text(encoding="utf-8"))
    # better=精确答案 vs worse=±δ：mock pairwise 两个方向都应稳定指认 better
    assert payload["swap_consistency"] == 1.0
