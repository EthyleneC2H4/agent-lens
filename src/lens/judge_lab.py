"""Judge 校准实验室 —— Cohen's κ、position-swap、长度对照。

「judge-人工 κ 只有中等一致，凭什么阻断合入？」→ 门禁分级：
observe 先行，κ 与误杀率达标才切 block。本模块产出校准数字。
"""

from __future__ import annotations

from statistics import fmean


def cohens_kappa(a: list[bool], b: list[bool]) -> float:
    """Cohen's κ：剔除随机一致后的判定一致度。

    κ = (p_o - p_e) / (1 - p_e)。文献区间：LLM judge 与人工约 0.44–0.53。
    """
    if len(a) != len(b) or not a:
        raise ValueError("两列标签需等长且非空")
    p_o = fmean([x == y for x, y in zip(a, b)])
    pa, pb = fmean(a), fmean(b)
    p_e = pa * pb + (1 - pa) * (1 - pb)
    if abs(1 - p_e) < 1e-12:
        return 1.0 if p_o == 1.0 else 0.0
    return (p_o - p_e) / (1 - p_e)


def agreement_rate(a: list[bool], b: list[bool]) -> float:
    if len(a) != len(b) or not a:
        raise ValueError("两列标签需等长且非空")
    return fmean([x == y for x, y in zip(a, b)])


def position_swap_check(
    judge_scores_forward: list[bool], judge_scores_swapped: list[bool]
) -> float:
    """position bias 体检：交换候选顺序后 judge 判定的一致率。

    一致率低说明 judge 在看位置不看质量；文献提醒：质量差距极大时
    去偏反而伤判别力，需配合人工抽检。
    """
    return agreement_rate(judge_scores_forward, judge_scores_swapped)


def length_bias_check(
    scores: list[bool], lengths: list[int], quantiles: tuple[float, float] = (0.33, 0.66)
) -> dict[str, float]:
    """verbosity bias 体检：短/中/长三桶的通过率应大致平坦。"""
    if len(scores) != len(lengths) or not scores:
        raise ValueError("scores 与 lengths 需等长且非空")
    srt = sorted(lengths)
    q1, q2 = srt[int(quantiles[0] * (len(srt) - 1))], srt[int(quantiles[1] * (len(srt) - 1))]
    buckets: dict[str, list[bool]] = {"short": [], "mid": [], "long": []}
    for s, ln in zip(scores, lengths):
        key = "short" if ln <= q1 else "mid" if ln <= q2 else "long"
        buckets[key].append(s)
    return {k: round(fmean(v), 4) if v else float("nan") for k, v in buckets.items()}
