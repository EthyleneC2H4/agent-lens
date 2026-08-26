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


# ---------- P5 技术债 #8：reward_detail 部分分数通道 ----------


def test_export_reward_detail_key_state_fraction(tmp_path):
    """key_state 部分命中比例进 reward_detail；纯 exact 任务无该键；主奖励口径不变。"""
    from lens.store import ContentAddressedStore as S

    store = S(tmp_path / "s")
    store.put(Trajectory(
        task_id="t_ks", version="v", run_id="r", output="ans", steps=["cart_size=1"],
        metadata={"trial": 0, "gold": "ans", "input": "q",
                  "required_states": ["cart_size=1", "paid=true"]},
    ))
    store.put(Trajectory(
        task_id="t_plain", version="v", run_id="r", output="ans",
        metadata={"trial": 0, "gold": "ans", "input": "q"},
    ))
    rollouts, _rates = export_rollouts(store, "r", ExactMatchScorer(), min_task_pass_rate=0.75)
    by_task = {rec["id"].split("#")[0]: rec for rec in rollouts}
    assert by_task["t_ks"]["reward_detail"] == {"key_state_fraction": 0.5}
    assert by_task["t_ks"]["reward"] == 1.0              # 主奖励仍是重放评分 0/1
    assert "reward_detail" not in by_task["t_plain"]

    path = write_jsonl(rollouts, tmp_path / "r.jsonl")
    back = load_jsonl_rollouts(path)                      # 回读校验容忍可选字段
    assert len(back) == 2


# ---------- flywheel：AgentRL-Lab schema 字段级对齐 ----------


def _harbor_record() -> dict:
    traj = Trajectory(
        task_id="t1", version="v1", run_id="r1", output="42",
        steps=["plan", "compute"], model="m",
        metadata={"trial": 0, "gold": "42", "input": "q"},
    )
    return build_rollout(traj, 1.0, content_hash="abc123")


def test_agentrl_contract_fields_exact():
    """转换产物字段必须与 AgentRL-Lab rollout/schema.Trajectory 完全一致。"""
    from lens.export import AGENTRL_TRAJECTORY_FIELDS, to_agentrl_trajectory

    rec = to_agentrl_trajectory(_harbor_record())
    assert set(rec) == set(AGENTRL_TRAJECTORY_FIELDS)
    last = rec["transitions"][-1]
    assert last["done"] is True and last["reward"] == 1.0
    assert rec["transitions"][0]["obs"] == "q"      # 首步 obs 携带任务输入
    assert rec["total_obs_tokens"] == sum(t["tokens"] for t in rec["transitions"])


def test_agentrl_cross_repo_roundtrip(tmp_path):
    """邻居仓库存在时：用其 load_trajectories 真实回读（flywheel 最小闭环）。"""
    import sys
    from pathlib import Path

    from lens.export import export_agentrl_format, write_jsonl

    sibling = Path(__file__).resolve().parents[2] / "agentrl-lab" / "src"
    if not (sibling / "agentrl" / "rollout" / "schema.py").exists():
        pytest.skip("agentrl-lab 邻居仓库不在工作区")

    path = write_jsonl(export_agentrl_format([_harbor_record()]), tmp_path / "r.jsonl")

    sys.path.insert(0, str(sibling))
    try:
        from agentrl.rollout.schema import load_trajectories

        trajs = load_trajectories(path)
    finally:
        sys.path.remove(str(sibling))
    assert len(trajs) == 1
    assert trajs[0].env_name == "agentlens/t1"
    assert trajs[0].total_reward == 1.0
    assert trajs[0].actions()[-1] == "42"
    assert trajs[0].rewards() == [0.0, 0.0, 1.0]    # 两步过程 + 终步
