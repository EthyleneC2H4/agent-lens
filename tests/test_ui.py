"""ui.py 的离线测试：纯函数渲染 + 127.0.0.1 路由烟测（零外部网络）。"""

from __future__ import annotations

from lens.store import ContentAddressedStore, RunInfo, Trajectory
from lens.ui import make_handler, render_gate_page, render_run_page, render_runs_page


def _traj(task_id: str, run_id: str, output: str, gold: str, trial: int = 0) -> Trajectory:
    return Trajectory(
        task_id=task_id,
        version="v1",
        run_id=run_id,
        output=output,
        steps=[f"step-{trial}"],
        tokens=5,
        metadata={"gold": gold, "input": f"输入 {task_id}", "trial": trial},
    )


def _store(tmp_path) -> ContentAddressedStore:
    store = ContentAddressedStore(tmp_path / "store")
    for trial in range(2):
        store.put(_traj("t1", "run-a", "384", "384", trial))
        store.put(_traj("t2", "run-a", "wrong", "7", trial))
    return store


# ---------------- 纯函数渲染 ----------------


def test_runs_page_escapes_and_lists():
    runs = [RunInfo(run_id='run-a<script>', n_trajectories=3, seq=0)]
    page = render_runs_page(runs)
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    assert "3" in page and "/run/" in page   # 链接指向 run 详情


def test_run_page_replay_scoring_and_escape():
    trajs = [
        _traj("t1", "r", "384", "384"),
        _traj("t1", "r", "<b>bad</b>", "384", trial=1),
    ]
    page = render_run_page("r", trajs)
    assert "1/2 通过" in page            # 重放评分：1 对 1 错
    assert "✅" in page and "❌" in page
    assert "&lt;b&gt;bad&lt;/b&gt;" in page  # 输出转义，防注入
    assert "<b>bad</b>" not in page.replace("&lt;b&gt;", "")  # 原始标签不出现


def test_gate_page_block_blocks_and_flags_hot_case():
    base = {"a": [True] * 4, "b": [True] * 4}
    cand = {"a": [False] * 4, "b": [True] * 4}   # a 全退化 → CI 必不重叠
    page_block = render_gate_page("base", "cand", base, cand, mode="block")
    assert "阻断" in page_block and "🔥" in page_block and "regressed" in page_block
    page_observe = render_gate_page("base", "cand", base, cand, mode="observe")
    assert "放行" in page_observe                 # observe 永远放行但给警告
    assert "⚠" in page_observe


def test_gate_page_clean_version_passes():
    res = {"a": [True] * 3}
    page = render_gate_page("base", "cand", res, dict(res), mode="block")
    assert "放行" in page and "🔥" not in page


# ---------------- HTTP 路由（127.0.0.1 随机端口，离线） ----------------


def test_http_routes(tmp_path):
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    store = _store(tmp_path)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
            body = r.read().decode()
        assert r.status == 200 and "run-a" in body

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/run/run-a", timeout=5) as r:
            detail = r.read().decode()
        assert r.status == 200 and "384" in detail

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/gate?base=run-a&cand=run-a&mode=block", timeout=5
        ) as r:
            gate_body = r.read().decode()
        assert r.status == 200 and "放行" in gate_body

        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/run/no-such-run", timeout=5)
            raised = False
        except urllib.error.HTTPError as e:
            raised, code = True, e.code
        assert raised and code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
