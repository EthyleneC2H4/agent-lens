"""报告渲染与 CI 交付物（gate JSON / PR 评论 markdown）测试。"""

import json

from lens.report import render_report
from lens.store import Trajectory


def _write_report(tmp_path, **kw):
    return render_report(
        "t", [[True, False], [True, True]], ["a", "b"], tmp_path / "r.html", **kw
    )


def test_report_without_cost_section(tmp_path):
    html = _write_report(tmp_path).read_text(encoding="utf-8")
    assert "成本记账" not in html
    assert "pass^k" in html and "fragile" in html


def test_report_with_cost_totals(tmp_path):
    html = _write_report(
        tmp_path,
        cost_totals={
            "prompt_tokens": 120,
            "completion_tokens": 45,
            "calls": 6,
            "model": "nemotron-x",
        },
    ).read_text(encoding="utf-8")
    assert "成本记账" in html and "120" in html and "nemotron-x" in html


def test_trajectory_token_fields_roundtrip(tmp_path):
    from lens.store import ContentAddressedStore

    store = ContentAddressedStore(tmp_path / "s")
    traj = Trajectory(
        task_id="t",
        version="v",
        run_id="r",
        output="o",
        prompt_tokens=10,
        completion_tokens=4,
        model="m1",
    )
    h = store.put(traj)
    back = store.get(h)
    assert (back.prompt_tokens, back.completion_tokens, back.model) == (10, 4, "m1")


def test_gate_json_payload_shape(tmp_path, monkeypatch):
    """gate --out-json 输出机器可读结构（CI 评论脚本的契约）。"""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    store_dir = tmp_path / "store"
    env_extra = {"PYTHONPATH": str(root / "src")}
    base_cmd = [sys.executable, "-m", "lens.cli"]

    def run(args):
        return subprocess.run(
            base_cmd + args, capture_output=True, text=True,
            env={**__import__("os").environ, **env_extra}, cwd=tmp_path,
        )

    ds = root / "src" / "lens" / "fixtures" / "demo_dataset.jsonl"
    assert run(["run", "--dataset", str(ds), "--version", "base", "--n-trials", "2",
                "--store-dir", str(store_dir)]).returncode == 0
    assert run(["run", "--dataset", str(ds), "--version", "cand", "--n-trials", "2",
                "--store-dir", str(store_dir)]).returncode == 0
    out_json = tmp_path / "gate.json"
    r = run(["gate", "--store-dir", str(store_dir), "--mode", "observe",
             "--out-json", str(out_json)])
    assert r.returncode == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["allowed"] is True and payload["mode"] == "observe"
    assert len(payload["diffs"]) == 5 and {"task_id", "status"} <= set(payload["diffs"][0])


# ---------- Phase B：CI 门禁模拟（无 remote 环境下的本地全流程） ----------


def _build_store_with_regression(tmp_path):
    """构造 base(0.8 成功率) vs cand(0.2) 的注入退化 store。"""
    from lens.runner import Runner, Task, make_versioned_solver
    from lens.store import ContentAddressedStore

    tasks = [Task(id=f"case-{i:02d}", input="q", gold="ans") for i in range(6)]
    store = ContentAddressedStore(tmp_path / "store")
    runner = Runner(store)
    runner.run(tasks, make_versioned_solver(0.8, seed_base=11), version="base", n_trials=5)
    runner.run(tasks, make_versioned_solver(0.2, seed_base=23), version="cand", n_trials=5)
    return store, ("base-seed0", "cand-seed0")


def _gate_payload(store, run_ids, mode):
    from lens.regression import GatePolicy, diff_versions, evaluate_gate
    from lens.scorers import ExactMatchScorer

    base_run, cand_run = run_ids

    def results(run_id):
        grouped = {}
        for t in store.list_by_run(run_id):
            grouped.setdefault(t.task_id, []).append(
                ExactMatchScorer().score(t, {"gold": str(t.metadata.get("gold", ""))})
            )
        return grouped

    diffs = diff_versions(results(base_run), results(cand_run))
    cand_r = results(cand_run)
    rate = sum(sum(v) / len(v) for v in cand_r.values()) / len(cand_r)
    allowed, violations = evaluate_gate(diffs, GatePolicy(mode=mode), rate)
    return {
        "allowed": allowed,
        "mode": mode,
        "base_run": base_run,
        "cand_run": cand_run,
        "cand_success_rate": round(rate, 4),
        "violations": violations,
        "diffs": [
            {"task_id": d.task_id, "base_passes": d.base_passes,
             "cand_passes": d.cand_passes, "trials": d.trials, "status": d.status}
            for d in diffs
        ],
    }


def test_injected_regression_blocked_and_comment_renders(tmp_path):
    """注入退化版本：observe 放行、block 阻断；评论含 regressed 标记。"""
    from lens.ci import MARKER, render_comment

    store, run_ids = _build_store_with_regression(tmp_path)

    observe = _gate_payload(store, run_ids, "observe")
    block = _gate_payload(store, run_ids, "block")
    assert observe["allowed"] is True and observe["violations"]
    assert block["allowed"] is False

    md = render_comment(block)
    assert md.startswith(MARKER)
    assert "阻断" in md and "regressed" in md
    assert "| case | base | cand | status |" in md
    # 幂等 upsert 契约：同一 marker 评论被 PATCH 而非重复 POST
    calls = []

    def fake_transport(method, url, token, body):
        calls.append((method, url))
        if method == "GET":
            return [{"id": 42, "body": MARKER + "\n旧评论"}]
        return {"html_url": "https://example/comment/42"}

    from lens.ci import upsert_comment

    upsert_comment("o/r", 1, "tok", md, transport=fake_transport)
    assert [m for m, _ in calls] == ["GET", "PATCH"]
    assert calls[1][1].endswith("/issues/comments/42")


def test_pr_comment_script_dry_run(tmp_path):
    """scripts/pr_comment.py 干跑：无 token 时只打印 markdown，退出码 0。"""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    store, run_ids = _build_store_with_regression(tmp_path)
    payload = _gate_payload(store, run_ids, "block")
    gate_json = tmp_path / "gate.json"
    gate_json.write_text(json.dumps(payload), encoding="utf-8")
    script = root / "scripts" / "pr_comment.py"
    r = subprocess.run(
        [sys.executable, str(script), "--gate-json", str(gate_json)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "阻断" in r.stdout and "干跑模式" in r.stdout
