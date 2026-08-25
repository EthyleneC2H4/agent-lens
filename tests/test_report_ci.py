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
