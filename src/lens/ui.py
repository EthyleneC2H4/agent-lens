"""只读本机 Web UI —— 内容寻址轨迹库的浏览器视图。

零新依赖（stdlib http.server）；默认绑定 127.0.0.1、GET-only，全部动态内容
html.escape——它是给「看」的，不给「改」。三个视图：
/                runs 列表（创建序）
/run/{run_id}    run 详情：对历史轨迹重放评分（judge later 不变量的只读展示）
/gate?base&cand&mode  两版本 diff + 门禁判定 + 🔥 高置信退化标记

渲染逻辑全部是纯函数，可离线测试；server 只是路由胶水。
"""

from __future__ import annotations

import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

from .regression import GatePolicy, diff_versions, evaluate_gate, regression_summary
from .scorers import ExactMatchScorer, Scorer
from .store import ContentAddressedStore, RunInfo, Trajectory

_CSS = (
    "body{font-family:-apple-system,'PingFang SC',sans-serif;margin:2rem;"
    "color:#1a1a2e;background:#fafafa}"
    "table{border-collapse:collapse;margin:1rem 0;background:#fff;width:100%}"
    "th,td{border:1px solid #ddd;padding:.4rem .7rem;text-align:left;font-size:.92rem}"
    "tr.task td{background:#eef3fb;font-weight:600}"
    ".verdict{font-size:1.15rem;padding:.6rem 1rem;border-radius:6px;display:inline-block}"
    ".pass{background:#e6f6e6}.fail{background:#fde8e8}.warn{background:#fdf6e3}"
    ".foot{color:#888;font-size:.8rem;margin-top:2rem}"
    "code{background:#f0f0f0;padding:.1rem .3rem;border-radius:3px}"
)


def _page(title: str, body: str) -> str:
    return (
        '<!doctype html><html lang="zh"><head><meta charset="utf-8">'
        f"<title>AgentLens · {html.escape(title)}</title>"
        f"<style>{_CSS}</style></head><body>"
        f"<h1>AgentLens · {html.escape(title)}</h1>{body}"
        '<p class="foot">只读视图 · 内容寻址轨迹库 · store trajectory first, judge later</p>'
        "</body></html>"
    )


def _group_replay(
    trajs: list[Trajectory], scorer: Scorer | None = None
) -> dict[str, list[tuple[Trajectory, bool]]]:
    """按任务分组并重放评分（判定与执行解耦：scorer 只吃 Trajectory + task）。"""
    sc = scorer or ExactMatchScorer()
    grouped: dict[str, list[tuple[Trajectory, bool]]] = {}
    for t in trajs:
        ok = sc.score(t, {"gold": str(t.metadata.get("gold", ""))})
        grouped.setdefault(t.task_id, []).append((t, ok))
    return grouped


def render_runs_page(runs: list[RunInfo]) -> str:
    rows = "".join(
        f"<tr><td>{r.seq}</td>"
        f'<td><a href="/run/{quote(r.run_id, safe="")}">{html.escape(r.run_id)}</a></td>'
        f"<td>{r.n_trajectories}</td></tr>"
        for r in sorted(runs, key=lambda x: x.seq)
    )
    body = (
        "<table><tr><th>seq</th><th>run_id</th><th>轨迹数</th></tr>"
        f"{rows}</table><p>点 run_id 进入重放详情；门禁对比见 "
        "<code>/gate?base=&amp;cand=&amp;mode=observe|block</code></p>"
    )
    return _page("runs", body)


def render_run_page(run_id: str, trajs: list[Trajectory], scorer: Scorer | None = None) -> str:
    grouped = _group_replay(trajs, scorer)
    rows = []
    for tid in sorted(grouped):
        pairs = grouped[tid]
        n_pass = sum(ok for _, ok in pairs)
        rows.append(
            f'<tr class="task"><td colspan="4">{html.escape(tid)}'
            f" — {n_pass}/{len(pairs)} 通过</td></tr>"
        )
        for t, ok in pairs:
            trial = int(t.metadata.get("trial", 0))
            rows.append(
                f"<tr><td>t{trial}</td><td>{'✅' if ok else '❌'}</td>"
                f"<td>{t.tokens}</td>"
                f"<td><code>{html.escape(t.output)}</code></td></tr>"
            )
    body = (
        f"<p>{len(trajs)} 条轨迹 · 重放评分口径：exact</p>"
        "<table><tr><th>trial</th><th>判定</th><th>tokens</th><th>output</th></tr>"
        f"{''.join(rows)}</table>"
    )
    return _page(f"run {run_id}", body)


def render_gate_page(
    base_id: str,
    cand_id: str,
    base_results: dict[str, list[bool]],
    cand_results: dict[str, list[bool]],
    mode: str = "observe",
) -> str:
    """两版本 diff + 门禁判定页。base/cand 结果为 task_id → trial 布尔列表。"""
    diffs = diff_versions(base_results, cand_results)
    cand_rate = (
        sum(sum(r) / len(r) for r in cand_results.values()) / len(cand_results)
        if cand_results
        else 0.0
    )
    allowed, violations = evaluate_gate(diffs, GatePolicy(mode=mode), cand_rate)
    ci_map = regression_summary(
        {k: [float(x) for x in v] for k, v in base_results.items()},
        {k: [float(x) for x in v] for k, v in cand_results.items()},
    )
    hot = {
        d.task_id
        for d in diffs
        if d.status == "regressed" and ci_map.get(d.task_id, {}).get("ci_overlap") is False
    }

    if mode == "block":
        verdict = (
            '<span class="verdict fail">⛔ 阻断</span>'
            if not allowed
            else '<span class="verdict pass">✅ 放行</span>'
        )
    else:
        verdict = '<span class="verdict warn">👁 放行（observe 模式，不阻断）</span>'
    vio_html = "".join(f"<li>⚠ {html.escape(v)}</li>" for v in violations)

    rows = []
    for d in sorted(diffs, key=lambda x: x.task_id):
        fire = (
            ' <span title="bootstrap CI 不重叠，非采样噪声">🔥</span>'
            if d.task_id in hot else ""
        )
        ov = ci_map.get(d.task_id, {})
        overlap = ov.get("ci_overlap")
        ov_txt = "—" if overlap is None else ("重叠(可能噪声)" if overlap else "不重叠")
        rows.append(
            f"<tr><td>{html.escape(d.task_id)}{fire}</td>"
            f"<td>{d.base_passes}/{d.trials}</td><td>{d.cand_passes}/{d.trials}</td>"
            f"<td>{html.escape(d.status)}</td><td>{ov_txt}</td></tr>"
        )
    body = (
        f"<p>{verdict} <code>{html.escape(base_id)}</code> → "
        f"<code>{html.escape(cand_id)}</code> · 模式 {html.escape(mode)}"
        f" · 候选通过率 {cand_rate:.3f}</p>"
        + (f"<ul>{vio_html}</ul>" if vio_html else "")
        + "<table><tr><th>case</th><th>base</th><th>cand</th><th>status</th>"
        f"<th>CI 对比</th></tr>{''.join(rows)}</table>"
    )
    return _page(f"gate {base_id} → {cand_id}", body)


# ---------------- HTTP 路由胶水 ----------------


def make_handler(store: ContentAddressedStore, scorer: Scorer | None = None):
    sc = scorer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # 静默默认访问日志，避免污染 CLI 输出
            pass

        def do_GET(self) -> None:  # noqa: N802 —— http.server 固定命名
            u = urlparse(self.path)
            path = unquote(u.path)
            try:
                status, page = self._route(path, parse_qs(u.query))
            except KeyError:
                status = 404
                page = _page("404", "<p>未知 run 或路径。</p>")
            data = page.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _route(self, path: str, qs: dict[str, list[str]]) -> tuple[int, str]:
            if path == "/":
                return 200, render_runs_page(store.list_runs())
            if path.startswith("/run/"):
                rid = path[len("/run/"):]
                known = {r.run_id for r in store.list_runs()}
                if rid not in known:
                    raise KeyError(rid)
                return 200, render_run_page(rid, store.list_by_run(rid), sc)
            if path == "/gate":
                base = qs.get("base", [""])[0]
                cand = qs.get("cand", [""])[0]
                known = {r.run_id for r in store.list_runs()}
                if base not in known or cand not in known:
                    raise KeyError(base or cand)
                mode = qs.get("mode", ["observe"])[0]
                b_group = _group_replay(store.list_by_run(base), sc)
                c_group = _group_replay(store.list_by_run(cand), sc)
                b_res = {k: [ok for _, ok in v] for k, v in b_group.items()}
                c_res = {k: [ok for _, ok in v] for k, v in c_group.items()}
                return 200, render_gate_page(base, cand, b_res, c_res, mode)
            raise KeyError(path)

    return Handler


def serve_ui(
    store: ContentAddressedStore, host: str = "127.0.0.1", port: int = 8517,
    scorer: Scorer | None = None,
) -> ThreadingHTTPServer:
    """构造只读 UI server（调用方负责 serve_forever / shutdown）。"""
    return ThreadingHTTPServer((host, int(port)), make_handler(store, scorer))
