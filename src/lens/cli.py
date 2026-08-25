"""AgentLens CLI —— demo：n-trials 矩阵 → pass@k/pass^k+CI → HTML 报告 → 门禁结论。"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import metrics as M
from .regression import GatePolicy, diff_versions, evaluate_gate
from .report import render_report
from .runner import Runner, make_versioned_solver
from .store import ContentAddressedStore

app = typer.Typer(help="AgentLens：Agent 回归评测门禁与稳定性度量平台（MVP）")
console = Console()

HERE = Path(__file__).resolve().parent
DEMO_DATASET = HERE / "fixtures" / "demo_dataset.jsonl"
SMOKE_DATASET = HERE / "fixtures" / "smoke_dataset.jsonl"


@app.callback()
def _root() -> None:
    """AgentLens 命令组。"""


def _cost_totals(store: ContentAddressedStore, run_id: str) -> dict[str, object]:
    """聚合一个 run 的 token 记账（真实端点为回传值，mock 为估算值）。"""
    trajs = store.list_by_run(run_id)
    pt = sum(t.prompt_tokens for t in trajs)
    ct = sum(t.completion_tokens for t in trajs)
    models = sorted({t.model for t in trajs if t.model})
    return {
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "calls": len(trajs),
        "model": models[0] if models else "",
    }


@app.command()
def demo(
    provider: str = typer.Option("mock", help="mock 或 real（real 缺 key 自动回落 mock）"),
) -> None:
    """端到端演示：两个版本各跑 n-trials → 报告 → diff → 门禁判定。"""
    import tempfile

    from .provider import select_provider
    from .runner import load_dataset, make_llm_solver

    tasks = load_dataset(DEMO_DATASET)
    tmp = Path(tempfile.mkdtemp(prefix="lens-demo-"))
    store = ContentAddressedStore(tmp / "store")
    runner = Runner(store)
    prov = select_provider(provider)

    if provider == "real" and type(prov).__name__ == "OpenAICompatibleProvider":
        # 真实通路：base=朴素指令，cand=引导推理指令——一次真实的 prompt A/B
        console.rule("[bold]1. base 版本（朴素指令 · 真实模型）[/bold]")
        runner.run(tasks, make_llm_solver(prov), version="v0.1-base", n_trials=1)
        console.rule("[bold]2. 候选版本（推理指令 · 真实模型）[/bold]")
        cand_run = runner.run(
            tasks,
            make_llm_solver(prov, instruction="先一步步推理，最后一行只输出最终答案。"),
            version="v0.2-cand",
            n_trials=1,
        )
    else:
        console.rule("[bold]1. base 版本（单次成功率 0.6）[/bold]")
        runner.run(tasks, make_versioned_solver(0.6), version="v0.1-base", n_trials=4)
        console.rule("[bold]2. 候选版本（单次成功率 0.75 —— 小改进？）[/bold]")
        cand_run = runner.run(tasks, make_versioned_solver(0.75), version="v0.2-cand", n_trials=4)

    trajs = store.list_by_run(cand_run.run_id)
    grouped, order = _group_results(trajs)
    results = [grouped[tid] for tid in order]
    path = render_report(
        "AgentLens demo：v0.2-cand",
        results,
        order,
        tmp / "report.html",
        cost_totals=_cost_totals(store, cand_run.run_id),
    )
    console.print(f"HTML 报告: {path}")
    console.print(
        f"run 统计: jobs={cand_run.n_jobs} ok={cand_run.completed} "
        f"retried={cand_run.retried_ok} task_fail={cand_run.task_failures} "
        f"net_fail={cand_run.network_failures}"
    )

    table = Table(title="pass 分布（乐观界与悲观界夹出真实区间）")
    table.add_column("k")
    table.add_column("pass@k")
    table.add_column("pass^k")
    for k in (1, 2, 4):
        table.add_row(
            str(k),
            f"{M.pass_hat_k(results, k):.3f}",
            f"{M.pass_caret_k(results, k):.3f}",
        )
    console.print(table)

    console.rule("[bold]3. 门禁判定（先观察后阻断的分级语义）[/bold]")
    for mode in ("observe", "block"):
        allowed, violations = _gate_core(store, "v0.1-base", "v0.2-cand", mode)
        verdict = "[green]放行[/green]" if allowed else "[red]阻断[/red]"
        extra = f"（{len(violations)} 项违规）" if violations else ""
        console.print(f"mode={mode} → {verdict}{extra}")
    console.print(f"\n[green]demo 完成（产物在 {tmp}）[/green]")


def _gate_core(store: ContentAddressedStore, base_id: str, cand_id: str, mode: str):
    base_r, _ = _group_results(store.list_by_run(base_id))
    cand_r, _ = _group_results(store.list_by_run(cand_id))
    diffs = diff_versions(base_r, cand_r)
    cand_rate = sum(sum(r) / len(r) for r in cand_r.values()) / len(cand_r) if cand_r else 0.0
    return evaluate_gate(diffs, GatePolicy(mode=mode), cand_rate)


@app.command()
def run(
    dataset: str = str(DEMO_DATASET),
    version: str = "v0.1",
    n_trials: int = 4,
    store_dir: str = ".lensstore",
    provider: str = typer.Option("mock", help="mock 或 real（real 缺 key 自动回落 mock）"),
) -> None:
    """对数据集跑 n-trials 多采样评测并落盘轨迹。"""
    from .provider import select_provider
    from .runner import load_dataset, make_llm_solver

    tasks = load_dataset(dataset)
    prov = select_provider(provider)
    solver = (
        make_llm_solver(prov)
        if type(prov).__name__ == "OpenAICompatibleProvider"
        else make_versioned_solver(0.6)
    )
    store = ContentAddressedStore(store_dir)
    runner = Runner(store)
    summary = runner.run(tasks, solver, version=version, n_trials=n_trials)
    console.print(
        f"run_id={summary.run_id}，{len(tasks)} 题 × {n_trials} trials 已落盘 → {store_dir}"
    )
    if summary.failed_jobs:
        console.print(
            f"[yellow]⚠ {len(summary.failed_jobs)} 个 job 失败：{summary.failed_jobs}[/yellow]"
        )


@app.command()
def smoke(
    store_dir: str = ".lensstore",
    out: str = "reports/smoke.html",
) -> None:
    """真实通路冒烟：3 个 case 走免费端点完成评测并出报告。

    无 key 时以退出码 2 失败（不静默回落——冒烟的意义就是验证真通路）。
    """
    from .provider import OpenAICompatibleProvider
    from .runner import load_dataset, make_llm_solver

    prov = OpenAICompatibleProvider(timeout_s=30, max_retries=2, backoff_base_s=2.0)
    if not prov.available:
        console.print(f"[red]冒烟失败：{prov.fallback_reason}——请设置环境变量后重试[/red]")
        raise typer.Exit(2)
    console.print(f"端点 {prov.base_url} · 模型 {prov.model}")
    tasks = load_dataset(SMOKE_DATASET)
    store = ContentAddressedStore(store_dir)
    summary = Runner(store).run(tasks, make_llm_solver(prov), version="smoke", n_trials=1)
    trajs = store.list_by_run(summary.run_id)
    grouped, order = _group_results(trajs)
    results = [grouped[tid] for tid in order]
    cost = _cost_totals(store, summary.run_id)
    path = render_report("AgentLens 冒烟：真实免费端点", results, order, out, cost_totals=cost)
    rate = sum(sum(r) for r in results) / max(sum(len(r) for r in results), 1)
    console.print(f"通过率 {rate:.2f} · tokens={cost['prompt_tokens']}+{cost['completion_tokens']}")
    console.print(f"报告: {path}")
    if summary.network_failures or summary.task_failures:
        console.print("[yellow]⚠ 存在失败 job，详见 RunSummary[/yellow]")
        raise typer.Exit(1)


@app.command()
def report(
    store_dir: str = ".lensstore",
    run_id: str | None = None,
    out: str = "reports/demo.html",
) -> None:
    """读取最近一次（或指定）run 的结果，产出 pass@k/pass^k + CI 的 HTML 报告。"""
    store = ContentAddressedStore(store_dir)
    if not store.index_path.exists():
        console.print("[red]store 为空：请先运行 lens run[/red]")
        raise typer.Exit(1)
    rid = run_id or _latest_run_id(store)
    trajs = store.list_by_run(rid)
    grouped, order = _group_results(trajs)
    results = [grouped[tid] for tid in order]
    path = render_report(
        "AgentLens 评测报告", results, order, out, cost_totals=_cost_totals(store, rid)
    )
    console.print(f"报告已生成: {path}")
    for k in (2, 4):
        console.print(
            f"pass@{k}={M.pass_hat_k(results, k):.3f}  pass^{k}={M.pass_caret_k(results, k):.3f}"
        )


@app.command()
def gate(
    store_dir: str = ".lensstore",
    base_run: str = "",
    cand_run: str = "",
    mode: str = "observe",
    out_json: str | None = typer.Option(None, help="机器可读门禁结果输出路径（CI 用）"),
) -> None:
    """两版本 diff + 门禁判定（observe/block 双模式）。"""
    import json

    store = ContentAddressedStore(store_dir)
    runs = _all_run_ids(store)
    base_id = base_run or (runs[0] if len(runs) >= 1 else "")
    cand_id = cand_run or (runs[-1] if len(runs) >= 1 else "")
    if not base_id or not cand_id:
        console.print("[red]需要至少一个已存在的 run（先 lens run）[/red]")
        raise typer.Exit(1)
    base_r, _ = _group_results(store.list_by_run(base_id))
    cand_r, _ = _group_results(store.list_by_run(cand_id))
    diffs = diff_versions(base_r, cand_r)
    cand_rate = (
        sum(sum(r) / len(r) for r in cand_r.values()) / len(cand_r) if cand_r else 0.0
    )
    allowed, violations = evaluate_gate(diffs, GatePolicy(mode=mode), cand_rate)
    table = Table(title=f"diff {base_id} → {cand_id}")
    table.add_column("case")
    table.add_column("base")
    table.add_column("cand")
    table.add_column("status")
    for d in diffs:
        table.add_row(
            d.task_id,
            f"{d.base_passes}/{d.trials}",
            f"{d.cand_passes}/{d.trials}",
            d.status,
        )
    console.print(table)
    for v in violations:
        console.print(f"[yellow]⚠ {v}[/yellow]")
    verdict = "[green]放行[/green]" if allowed else "[red]阻断[/red]"
    console.print(f"门禁模式={mode} → {verdict}")

    if out_json:
        payload = {
            "allowed": allowed,
            "mode": mode,
            "base_run": base_id,
            "cand_run": cand_id,
            "cand_success_rate": round(cand_rate, 4),
            "violations": violations,
            "diffs": [
                {
                    "task_id": d.task_id,
                    "base_passes": d.base_passes,
                    "cand_passes": d.cand_passes,
                    "trials": d.trials,
                    "status": d.status,
                }
                for d in diffs
            ],
        }
        Path(out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(out_json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        console.print(f"JSON 结果: {out_json}")
    if not allowed:
        raise typer.Exit(1)


def _all_run_ids(store: ContentAddressedStore) -> list[str]:
    return sorted({t.run_id for t in _latest_run(store, all=True)})


def _latest_run_id(store: ContentAddressedStore) -> str:
    ids = _all_run_ids(store)
    return ids[-1] if ids else ""


def _latest_run(store: ContentAddressedStore, all: bool = False):  # noqa: A002
    del all
    trajs = []
    if store.index_path.exists():
        import json

        for line in store.index_path.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            from .store import Trajectory

            trajs.append(Trajectory.model_validate(rec))
    return trajs


def _group_results(trajs):
    """按任务聚合并重放评分（judge later：轨迹先行落盘的直接收益）。"""
    from .scorers import ExactMatchScorer

    scorer = ExactMatchScorer()
    grouped: dict[str, list[bool]] = {}
    order: list[str] = []
    for t in trajs:
        if t.task_id not in grouped:
            grouped[t.task_id] = []
            order.append(t.task_id)
        ok = scorer.score(t, {"gold": str(t.metadata.get("gold", ""))})
        grouped[t.task_id].append(ok)
    return grouped, order


if __name__ == "__main__":
    app()
