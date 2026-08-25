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


def test_load_dataset_jsonl():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    p = root / "src" / "lens" / "fixtures" / "demo_dataset.jsonl"
    tasks = load_dataset(p)
    assert len(tasks) == 5
    assert tasks[0].gold == "384"
