"""Phase E：rollout 导出（flywheel 出口）——筛选 / schema / 回读校验。"""

import pytest

from lens.export import build_rollout, export_rollouts, load_jsonl_rollouts, write_jsonl
from lens.runner import Runner, Task
from lens.scorers import ExactMatchScorer
from lens.store import ContentAddressedStore, Trajectory


def _store_with_mixed_tasks(tmp_path):
    """t_good 全对，t_flip 一半对（侥幸型），t_bad 全错。"""
    tasks = [
        Task(id="t_good", input="q", gold="ans"),
        Task(id="t_flip", input="q", gold="ans"),
        Task(id="t_bad", input="q", gold="ans"),
    ]
    store = ContentAddressedStore(tmp_path / "s")
    runner = Runner(store)

    def solver(task: Task, trial_seed: int):
        rate = {"t_good": 1.0, "t_flip": 0.5, "t_bad": 0.0}[task.id]
        import random

        ok = random.Random(trial_seed).random() < rate
        return ("ans" if ok else "wrong", [])

    runner.run(tasks, solver, version="v", n_trials=4)
    return store


def test_export_filters_unstable_tasks(tmp_path):
    store = _store_with_mixed_tasks(tmp_path)
    rollouts, rates = export_rollouts(
        store, "v-seed0", ExactMatchScorer(), min_task_pass_rate=0.75
    )
    assert rates["t_good"] == 1.0 and rates["t_bad"] == 0.0 and 0 < rates["t_flip"] < 1
    exported_ids = {r["id"].split("#")[0] for r in rollouts}
    assert exported_ids == {"t_good"}          # 侥幸型与全错型都被剔除


def test_rollout_schema_roundtrip(tmp_path):
    traj = Trajectory(
        task_id="t1", version="v1", run_id="r1", output="42",
        steps=["s1"], model="m", metadata={"trial": 2, "gold": "42", "input": "q"},
    )
    rec = build_rollout(traj, 1.0, content_hash="abc123")
    assert rec["source"]["content_hash"] == "abc123"
    assert rec["task"]["gold"] == "42" and rec["reward"] == 1.0

    path = write_jsonl([rec], tmp_path / "out" / "r.jsonl")
    back = load_jsonl_rollouts(path)
    assert len(back) == 1 and back[0]["trajectory"]["output"] == "42"


def test_loader_rejects_missing_fields(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"id": "x"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="缺字段"):
        load_jsonl_rollouts(bad)
