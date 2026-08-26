"""退化演示 solver：经 Runner 端到端验证——job 全成功、轨迹全落盘、答案恒错。

第一版测试用 model_dump() 把 Task 转成 dict 再调 solver，掩盖了
「runner 传 Task 对象而 solver 用 dict API」的契约破裂（CI 上 cand run 变空、
门禁静默放行）。现在必须走 Runner 全链路。
"""

from __future__ import annotations

import importlib

from lens.runner import Runner, load_dataset
from lens.scorers import ExactMatchScorer
from lens.store import ContentAddressedStore


def _load_factory(spec: str):
    module_name, _, attr = spec.partition(":")
    return getattr(importlib.import_module(module_name), attr)


def test_degraded_solver_end_to_end_via_runner(tmp_path):
    factory = _load_factory("lens.fixtures.degraded_solver:make_degraded_solver")
    store = ContentAddressedStore(tmp_path / "store")
    tasks = load_dataset("src/lens/fixtures/demo_dataset.jsonl")
    summary = Runner(store).run(tasks, factory(), version="cand-degraded", n_trials=2)

    # 契约关键：零 job 失败、5 题 × 2 trials 全部落盘（此前 AttributeError 全崩 → run 为空）
    assert not summary.failed_jobs, f"退化 solver 不应崩任务: {summary.failed_jobs}"
    trajs = store.list_by_run(summary.run_id)
    assert len(trajs) == len(tasks) * 2

    # 重放评分：对有数值 gold 的任务恒答错——门禁演示的退化素材
    sc = ExactMatchScorer()
    for t in trajs:
        gold = str(t.metadata.get("gold", ""))
        if gold.replace(".", "").replace("%", "").isdigit():
            assert not sc.score(t, {"gold": gold}), f"{t.task_id}: 退化 solver 不应答对"
