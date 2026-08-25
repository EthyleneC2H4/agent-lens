"""pass@k / pass^k 双侧分布与 bootstrap 置信区间。

- pass@k：Codex 论文（arXiv:2107.03374）无偏估计器，组合数形式——
  「k 次采样中至少一次通过」的无偏估计。
- pass^k（tau-bench, ICLR 2025）：「同任务 k 次独立全部通过」的样本均值，
  悲观界。两者夹出真实能力区间。
- 单次 pass@1 的波动可达 pp 量级 → 门禁必须看分布，不看点值。
"""

from __future__ import annotations

import random
from math import comb
from statistics import fmean


def pass_at_k(n: int, c: int, k: int) -> float:
    """Codex 无偏估计器。n=采样数，c=通过数，k=预算。

    pass@k = 1 - C(n-c, k) / C(n, k)；等价数值稳定形式用连乘实现。
    """
    if k > n:
        raise ValueError(f"k={k} 不能大于采样数 n={n}")
    if c < 0 or c > n:
        raise ValueError("c 必须在 [0, n] 内")
    if n - c < k:
        return 1.0
    # 数值稳定连乘：prod_{i=0}^{k-1} (n-c-i)/(n-i)
    p = 1.0
    for i in range(k):
        p *= (n - c - i) / (n - i)
    return 1.0 - p


def pass_hat_k(results: list[list[bool]], k: int) -> float:
    """数据集级 pass@k：每题 n 个布尔结果，逐题估计后取均值。"""
    if not results:
        raise ValueError("空数据集")
    per_task = []
    for task_results in results:
        n = len(task_results)
        c = sum(task_results)
        per_task.append(pass_at_k(n, c, min(k, n)))
    return fmean(per_task)


def pass_caret_k(results: list[list[bool]], k: int) -> float:
    """pass^k：每题 k 次独立全过的概率估计（子集枚举均值，tau^2-bench 口径）。

    对每题在 n 次结果上枚举全部 C(n,k) 个大小为 k 的子集，
    全过子集占比即为该任务 pass^k 的无偏估计。
    """
    if not results:
        raise ValueError("空数据集")
    per_task = []
    for task_results in results:
        n = len(task_results)
        kk = min(k, n)
        total = comb(n, kk)
        all_pass = sum(
            1 for subset_idx in _combinations(range(n), kk)
            if all(task_results[i] for i in subset_idx)
        )
        per_task.append(all_pass / total)
    return fmean(per_task)


def _combinations(items, r):
    # 小规模直接用 itertools；显式封装便于测试桩替换
    from itertools import combinations

    yield from combinations(items, r)


def bootstrap_ci(
    values: list[float], n_boot: int = 2000, alpha: float = 0.05, seed: int = 0
) -> tuple[float, float, float]:
    """bootstrap 百分位居中置信区间。返回 (point, ci_low, ci_high)。"""
    if not values:
        raise ValueError("空样本")
    rng = random.Random(seed)
    point = fmean(values)
    means = []
    for _ in range(n_boot):
        sample = [rng.choice(values) for _ in range(len(values))]
        means.append(fmean(sample))
    means.sort()
    lo = means[int((alpha / 2) * n_boot)]
    hi = means[min(int((1 - alpha / 2) * n_boot), n_boot - 1)]
    return point, lo, hi
