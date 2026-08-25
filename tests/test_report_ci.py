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


# ---------- P5 技术债 #3：gate 基线自动选择改创建序 ----------


def test_gate_baseline_auto_picks_creation_order(tmp_path):
    """无显式 --base-run 时按「创建序」取首/末 run，字母序陷阱不再选错基线。"""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    store_dir = tmp_path / "store"
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    base_cmd = [sys.executable, "-m", "lens.cli"]
    ds = root / "src" / "lens" / "fixtures" / "demo_dataset.jsonl"

    def run(args):
        return subprocess.run(
            base_cmd + args, capture_output=True, text=True, env=env, cwd=tmp_path
        )

    # 插入序 z-first → a-second → m-third（字母序会选中 a-second 当基线——错）
    for version in ("z-first", "a-second", "m-third"):
        assert run(["run", "--dataset", str(ds), "--version", version,
                    "--n-trials", "2", "--store-dir", str(store_dir)]).returncode == 0
    out_json = tmp_path / "gate.json"
    r = run(["gate", "--store-dir", str(store_dir), "--mode", "observe",
             "--out-json", str(out_json)])
    assert r.returncode == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["base_run"] == "z-first-seed0"
    assert payload["cand_run"] == "m-third-seed0"
    assert payload["baseline_auto"] is True
    # lens runs 列表命令可枚举三个 run（创建序）
    r = run(["runs", "--store-dir", str(store_dir)])
    assert r.returncode == 0
    for rid in ("z-first-seed0", "a-second-seed0", "m-third-seed0"):
        assert rid in r.stdout


def test_gate_explicit_runs_marks_not_auto(tmp_path):
    """显式指定 --base-run/--cand-run 时 baseline_auto=False。"""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    store_dir = tmp_path / "store"
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    base_cmd = [sys.executable, "-m", "lens.cli"]
    ds = root / "src" / "lens" / "fixtures" / "demo_dataset.jsonl"

    def run(args):
        return subprocess.run(
            base_cmd + args, capture_output=True, text=True, env=env, cwd=tmp_path
        )

    for version in ("b-base", "c-cand"):
        assert run(["run", "--dataset", str(ds), "--version", version,
                    "--n-trials", "2", "--store-dir", str(store_dir)]).returncode == 0
    out_json = tmp_path / "gate.json"
    r = run(["gate", "--store-dir", str(store_dir), "--mode", "observe",
             "--base-run", "b-base-seed0", "--cand-run", "c-cand-seed0",
             "--out-json", str(out_json)])
    assert r.returncode == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["baseline_auto"] is False


# ---------- P5 技术债 #5：judge 用量入报告成本区 ----------


def test_group_results_returns_judge_usage(tmp_path):
    """_group_results 对 LLMJudgeScorer 返回 usage_totals 拷贝；规则 scorer 为 None。"""
    from lens.cli import _group_results
    from lens.scorers import LLMJudgeScorer
    from lens.store import ContentAddressedStore

    store = ContentAddressedStore(tmp_path / "s")
    store.put(Trajectory(task_id="t", version="v", run_id="r", output="384",
                         metadata={"gold": "384"}))
    trajs = store.list_by_run("r")

    grouped, order, usage = _group_results(trajs, LLMJudgeScorer())
    assert order == ["t"] and grouped["t"] == [True]      # mock judge 语义判对
    assert usage is not None and usage["calls"] == 1 and usage["prompt_tokens"] > 0

    _, _, usage_rule = _group_results(trajs)              # 默认 exact_match
    assert usage_rule is None


def test_report_with_judge_totals_row(tmp_path):
    html = render_report(
        "t", [[True], [False]], ["a", "b"], tmp_path / "r.html",
        cost_totals={"prompt_tokens": 10, "completion_tokens": 5, "calls": 2,
                     "model": "m"},
        judge_totals={"prompt_tokens": 7, "completion_tokens": 3, "calls": 2},
    ).read_text(encoding="utf-8")
    assert "<td>llm_judge（重放评分）</td>" in html and ">7<" in html and ">3<" in html


def test_render_report_rejects_empty_results(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="结果为空"):
        render_report("t", [], [], tmp_path / "r.html")


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


# ---------- 缺陷修复回归：HTML/markdown 转义 + 复核页进度持久化 ----------


def test_report_escapes_html_in_ids_and_title(tmp_path):
    """task_id 含 <script> 等字符时不得注入 HTML。"""
    html = render_report(
        "t<title>&", [[True], [False]], ["a<b>", "x|y"], tmp_path / "r.html"
    ).read_text(encoding="utf-8")
    assert "<script>" not in html and "&lt;b&gt;" in html and "t&lt;title&gt;" in html


def test_render_comment_escapes_pipes_and_newlines():
    from lens.ci import render_comment

    payload = {
        "allowed": False, "mode": "block", "base_run": "b", "cand_run": "c",
        "cand_success_rate": 0.5,
        "violations": ["退化 case 数 1 超过阈值 0: ['bad|case\nrow']"],
        "diffs": [{"task_id": "weird|id", "base_passes": 1, "cand_passes": 0,
                   "trials": 1, "status": "regressed"}],
    }
    md = render_comment(payload)
    table_lines = [ln for ln in md.splitlines() if ln.startswith("|")]
    assert all(ln.count("|") >= 4 for ln in table_lines)      # 竖线不破表
    assert "weird\\|id" in md
    assert "bad\\|case row" in md                              # 换行被压平


def test_review_html_has_progress_persistence():
    """复核页必须带 localStorage 进度保存/恢复（刷新不丢标注）。"""
    from lens.calibration import build_pool, render_review_html

    pool = build_pool(seed=0)[:3]
    html = render_review_html(pool, ["yes"] * 3)
    for marker in ("localStorage.setItem", "restoreProgress", "clearProgress",
                   "lens-review-progress"):
        assert marker in html, f"缺少进度持久化标记: {marker}"
