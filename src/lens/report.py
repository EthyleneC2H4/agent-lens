"""自包含 HTML 报告页（内联 CSS，零外链）——只读报告，非通用 dashboard。"""

from __future__ import annotations

from pathlib import Path

from .metrics import bootstrap_ci, pass_caret_k, pass_hat_k


def render_report(
    title: str,
    results: list[list[bool]],
    task_ids: list[str],
    out_path: str | Path,
) -> Path:
    """生成 pass@k / pass^k + CI 的单页 HTML 报告。"""
    ks = sorted({1, 2, 4, min(8, max(len(r) for r in results))})
    rows = []
    for k in ks:
        pk = pass_hat_k(results, k)
        pck = pass_caret_k(results, k)
        per_task = [sum(r) / len(r) for r in results]
        _, lo, hi = bootstrap_ci(per_task, n_boot=1000, seed=k)
        rows.append(
            f"<tr><td>k={k}</td><td>{pk:.3f}</td><td>{pck:.3f}</td>"
            f"<td>{lo:.3f} – {hi:.3f}</td></tr>"
        )
    case_rows = []
    for tid, r in zip(task_ids, results):
        rate = sum(r) / len(r)
        bar = "█" * int(rate * 20)
        cls = " class='fragile'" if 0 < rate < 1 else ""
        case_rows.append(
            f"<tr{cls}><td>{tid}</td><td>{len(r)}</td><td>{rate:.2f}</td><td>{bar}</td></tr>"
        )
    html = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>{title}</title>
<style>
 body{{font-family:ui-sans-serif,system-ui;max-width:760px;
      margin:2rem auto;padding:0 1rem;color:#1a1a2e}}
 h1{{font-size:1.3rem}} table{{border-collapse:collapse;width:100%;
      margin:1rem 0}}
 td,th{{border:1px solid #ddd;padding:6px 10px;text-align:left;
      font-size:.92rem}}
 th{{background:#f4f4fb}}
 tr.fragile td{{background:#fff7e6}}
 .muted{{color:#777;font-size:.85rem}}
</style></head><body>
<h1>{title}</h1>
<p class="muted">pass@k（乐观界 · Codex 无偏估计器）与 pass^k（悲观界 · k 次全过）
夹出真实能力区间；单次 pass@1 的波动可达 pp 量级，门禁必须看分布。</p>
<table><tr><th>指标</th><th>pass@k</th><th>pass^k</th><th>per-task 均值 bootstrap 95% CI</th></tr>
{''.join(rows)}</table>
<table><tr><th>case</th><th>trials</th><th>通过率</th><th></th></tr>
{''.join(case_rows)}</table>
<p class="muted">黄色行为脆弱 case（通过率方差大）——回归门禁的重点观察对象。</p>
</body></html>"""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
