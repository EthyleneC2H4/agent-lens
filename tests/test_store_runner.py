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
    run_id = runner.run(ts, make_versioned_solver(1.0), version="v9", n_trials=4)
    trajs = store.list_by_run(run_id)
    assert len(trajs) == 12           # 3 题 × 4 trials
    assert all(t.output == "ans" for t in trajs)
    assert all(t.metadata["gold"] == "ans" for t in trajs)


def test_load_dataset_jsonl():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    p = root / "src" / "lens" / "fixtures" / "demo_dataset.jsonl"
    tasks = load_dataset(p)
    assert len(tasks) == 5
    assert tasks[0].gold == "384"
