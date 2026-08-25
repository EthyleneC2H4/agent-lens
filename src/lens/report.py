"""自包含 HTML 报告页（内联 CSS，零外链）——只读报告，非通用 dashboard。"""

from __future__ import annotations

import html
from pathlib import Path

from .metrics import bootstrap_ci, pass_caret_k, pass_hat_k


def _esc(text: object) -> str:
    return html.escape(str(text))


def render_report(
    title: str,
    results: list[list[bool]],
    task_ids: list[str],
    out_path: str | Path,
    cost_totals: dict[str, int] | None = None,
    judge_totals: dict[str, int] | None = None,
) -> Path:
    """生成 pass@k / pass^k + CI 的单页 HTML 报告。

    cost_totals 为被评 agent 侧成本汇总；judge_totals 为重放评分时
    LLMJudgeScorer 的用量（judge 成本与 agent 成本分行呈现，不混算）。
    """
    if not results:
        raise ValueError("结果为空：没有可渲染的评测结果（先运行评测并确认轨迹落盘）")
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
            f"<tr{cls}><td>{_esc(tid)}</td><td>{len(r)}</td><td>{rate:.2f}</td>"
            f"<td>{bar}</td></tr>"
        )
    cost_html = ""
    if cost_totals:
        pt = cost_totals.get("prompt_tokens", 0)
        ct = cost_totals.get("completion_tokens", 0)
        calls = cost_totals.get("calls", 0)
        model = cost_totals.get("model", "mock")
        judge_row = ""
        if judge_totals:
            jpt = judge_totals.get("prompt_tokens", 0)
            jct = judge_totals.get("completion_tokens", 0)
            jcalls = judge_totals.get("calls", 0)
            judge_row = (
                f"<tr><td>llm_judge（重放评分）</td><td>{jcalls}</td><td>{jpt}</td>"
                f"<td>{jct}</td><td>{jpt + jct}</td></tr>"
            )
        cost_html = (
            f"<h2>成本记账</h2><table>"
            f"<tr><th>model</th><th>LLM calls</th><th>prompt tokens</th>"
            f"<th>completion tokens</th><th>total</th></tr>"
            f"<tr><td>{_esc(model)}</td><td>{calls}</td><td>{pt}</td><td>{ct}</td>"
            f"<td>{pt + ct}</td></tr>{judge_row}</table>"
        )
    html_out = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>{_esc(title)}</title>
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
<h1>{_esc(title)}</h1>
<p class="muted">pass@k（乐观界 · Codex 无偏估计器）与 pass^k（悲观界 · k 次全过）
夹出真实能力区间；单次 pass@1 的波动可达 pp 量级，门禁必须看分布。</p>
<table><tr><th>指标</th><th>pass@k</th><th>pass^k</th><th>per-task 均值 bootstrap 95% CI</th></tr>
{''.join(rows)}</table>
<table><tr><th>case</th><th>trials</th><th>通过率</th><th></th></tr>
{''.join(case_rows)}</table>
{cost_html}
<p class="muted">黄色行为脆弱 case（通过率方差大）——回归门禁的重点观察对象。</p>
</body></html>"""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_out, encoding="utf-8")
    return out
