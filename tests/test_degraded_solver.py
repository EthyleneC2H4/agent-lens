"""退化演示 solver：能被 --solver-spec 同款机制加载，且对 demo 数据集全量答错。"""

from __future__ import annotations

import importlib

from lens.runner import load_dataset


def _load_factory(spec: str):
    module_name, _, attr = spec.partition(":")
    return getattr(importlib.import_module(module_name), attr)


def test_degraded_solver_loads_and_fails_all():
    factory = _load_factory("lens.fixtures.degraded_solver:make_degraded_solver")
    solve = factory()
    tasks = load_dataset("src/lens/fixtures/demo_dataset.jsonl")
    assert tasks, "demo 数据集不应为空"
    for task in tasks:
        output, steps = solve(task.model_dump(), 0)
        assert isinstance(output, str) and output
        assert isinstance(steps, list)
        # 关键性质：对有数值 gold 的任务恒答错——门禁演示的退化素材
        gold = str(task.gold)
        if gold.isdigit():
            assert gold not in output, f"{task.id}: 退化 solver 不应答对"
