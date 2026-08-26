from lens.runner import Runner, load_dataset, make_versioned_solver
from lens.store import ContentAddressedStore, Trajectory


def _traj(output="x", task_id="t1"):
    return Trajectory(task_id=task_id, version="v", run_id="r", output=output)


def test_store_roundtrip(tmp_path):
    store = ContentAddressedStore(tmp_path / "s")
    h = store.put(_traj("hello"))
    assert store.get(h).output == "hello"


def test_store_content_addressing_dedup(tmp_path):
    store = ContentAddressedStore(tmp_path / "s")
    store.put(_traj("same"))
    store.put(_traj("same"))          # 相同内容 → 块去重，仅索引追加
    store.put(_traj("different"))
    hits = store.dedup_hits()
    assert hits == 1
    blocks = list((tmp_path / "s" / "blocks").glob("*.json"))
    assert len(blocks) == 2


def test_store_list_by_run(tmp_path):
    store = ContentAddressedStore(tmp_path / "s")
    store.put(Trajectory(task_id="a", version="v1", run_id="r1", output="x"))
    store.put(Trajectory(task_id="b", version="v1", run_id="r2", output="y"))
    assert [t.task_id for t in store.list_by_run("r1")] == ["a"]


def test_runner_matrix_and_metadata(tmp_path):
    from lens.runner import Task

    ts = [Task(id=f"t{i}", input="q", gold="ans") for i in range(3)]
    store = ContentAddressedStore(tmp_path / "s")
    runner = Runner(store, n_workers=2)
    summary = runner.run(ts, make_versioned_solver(1.0), version="v9", n_trials=4)
    trajs = store.list_by_run(summary.run_id)
    assert len(trajs) == 12           # 3 题 × 4 trials
    assert summary.completed == 12 and summary.task_failures == 0
    assert all(t.output == "ans" for t in trajs)
    assert all(t.metadata["gold"] == "ans" for t in trajs)


def test_runner_failure_isolation_and_stats(tmp_path):
    """单 job 崩溃不炸矩阵；网络错与任务错分开计数。"""
    from lens.provider import NetworkError
    from lens.runner import Task

    calls: dict[str, int] = {"n": 0}

    def bad_solver(task: Task, trial_seed: int):
        calls["n"] += 1
        if task.id == "boom":
            raise ValueError("任务内部崩了")
        return ("ok", [])

    def net_solver(task: Task, trial_seed: int):
        calls["n"] += 1
        if task.id == "netty":
            raise NetworkError("连接被重置")
        return ("ok", [])

    ts = [Task(id="good", input="q"), Task(id="boom", input="q"), Task(id="netty", input="q")]
    store = ContentAddressedStore(tmp_path / "s1")
    s = Runner(store).run(ts, bad_solver, version="v", n_trials=2)
    assert s.task_failures == 2 and s.network_failures == 0   # boom 重试后仍失败
    assert s.completed == 4 and s.ok_rate == 4 / 6
    assert all(j.startswith("boom") for j in s.failed_jobs)

    store2 = ContentAddressedStore(tmp_path / "s2")
    s2 = Runner(store2).run(ts, net_solver, version="v", n_trials=1)
    assert s2.network_failures == 1 and s2.task_failures == 0


def test_runner_llm_solver_records_tokens(tmp_path):
    """SolverReply 的 token 记账端到端入轨迹（Phase A 成本列数据源）。"""
    from lens.runner import SolverReply, Task, make_llm_solver

    class FakeProv:
        def chat(self, messages):
            class R:
                text = "43"
                prompt_tokens = 11
                completion_tokens = 3
                model = "fake-model"

            return R()

    ts = [Task(id="t1", input="17+26?", gold="43")]
    store = ContentAddressedStore(tmp_path / "s")
    s = Runner(store).run(ts, make_llm_solver(FakeProv()), version="v", n_trials=1)
    traj = store.list_by_run(s.run_id)[0]
    assert traj.prompt_tokens == 11 and traj.completion_tokens == 3
    assert traj.model == "fake-model"
    assert traj.steps and "prompt=11" in traj.steps[0]
    reply = SolverReply(output="x")
    assert reply.steps == []


# ---------- P5 技术债 #8 前置修复：required_states 必须入轨迹 metadata ----------


def test_runner_persists_required_states(tmp_path):
    """required_states 落进 metadata——否则 key_state 判定事后无法重放。"""
    from lens.runner import Task

    ts = [Task(id="t1", input="q", gold="ans", required_states=["cart_size=1"])]
    store = ContentAddressedStore(tmp_path / "s")
    summary = Runner(store).run(ts, make_versioned_solver(1.0), version="v", n_trials=1)
    traj = store.list_by_run(summary.run_id)[0]
    assert traj.metadata["required_states"] == ["cart_size=1"]


# ---------- P5 技术债 #4：run 级二级索引与懒迁移 ----------


def test_store_run_index_lazy_migration(tmp_path):
    """旧式库（无 runs_manifest.json）打开后自动重建，list_by_run/list_runs 可用。"""
    import shutil

    store = ContentAddressedStore(tmp_path / "s")
    store.put(Trajectory(task_id="a", version="v1", run_id="r1", output="x"))
    store.put(Trajectory(task_id="b", version="v2", run_id="r2", output="y"))
    store.put(Trajectory(task_id="c", version="v1", run_id="r1", output="z"))
    # 模拟旧式库：抹掉 run 级索引产物
    shutil.rmtree(tmp_path / "s" / "runs")
    (tmp_path / "s" / "runs_manifest.json").unlink()

    store2 = ContentAddressedStore(tmp_path / "s")
    assert [t.task_id for t in store2.list_by_run("r1")] == ["a", "c"]   # 插入序保持
    infos = store2.list_runs()
    assert [i.run_id for i in infos] == ["r1", "r2"]                     # 首次出现序
    by_id = {i.run_id: i for i in infos}
    assert by_id["r1"].n_trajectories == 2 and by_id["r2"].n_trajectories == 1
    assert by_id["r1"].seq < by_id["r2"].seq
    assert (tmp_path / "s" / "runs_manifest.json").exists()              # 已回填


def test_store_list_runs_first_appearance_order_not_alphabetical(tmp_path):
    """seq 按「第一次出现」排，不受 run_id 字母序影响（gate 默认基线的数据源）。"""
    store = ContentAddressedStore(tmp_path / "s")
    for rid in ("z-first", "a-second", "m-third"):
        store.put(Trajectory(task_id="t", version=rid, run_id=rid, output="o"))
    assert [i.run_id for i in store.list_runs()] == ["z-first", "a-second", "m-third"]
    assert [i.seq for i in store.list_runs()] == [0, 1, 2]


def test_store_concurrent_puts_consistent(tmp_path):
    """并发 put 下 run 级索引与 index 行数一致（线程安全冒烟）。"""
    from lens.runner import Runner, Task, make_versioned_solver

    tasks = [Task(id=f"t{i}", input="q", gold="ans") for i in range(12)]
    store = ContentAddressedStore(tmp_path / "s")
    summary = Runner(store, n_workers=8).run(
        tasks, make_versioned_solver(1.0), version="v", n_trials=2
    )
    assert len(store.list_by_run(summary.run_id)) == 24
    infos = store.list_runs()
    assert len(infos) == 1 and infos[0].n_trajectories == 24
    n_index = sum(1 for _ in (tmp_path / "s" / "index.jsonl").open())
    n_blocks = len(list((tmp_path / "s" / "blocks").glob("*.json")))
    assert store.dedup_hits() == max(n_index - n_blocks, 0)


def test_load_dataset_jsonl():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    p = root / "src" / "lens" / "fixtures" / "demo_dataset.jsonl"
    tasks = load_dataset(p)
    assert len(tasks) == 5
    assert tasks[0].gold == "384"


# ---------- P6-C：Task.extra 打通任意元数据到轨迹（鲁棒性套件的传输通道） ----------


def test_task_extra_flows_into_metadata_and_dataset(tmp_path):
    import json

    from lens.runner import Runner, load_dataset

    ds = tmp_path / "ds.jsonl"
    ds.write_text(
        json.dumps({"id": "t1", "input": "q", "gold": "a",
                    "extra": {"comply_markers": ["evil.example"]}}),
        encoding="utf-8",
    )
    tasks = load_dataset(ds)
    assert tasks[0].extra["comply_markers"] == ["evil.example"]

    store = ContentAddressedStore(tmp_path / "s")
    Runner(store).run(tasks, lambda task, seed: ("out", []), version="v", n_trials=1)
    traj = store.list_by_run("v-seed0")[0]
    assert traj.metadata["extra"]["comply_markers"] == ["evil.example"]
