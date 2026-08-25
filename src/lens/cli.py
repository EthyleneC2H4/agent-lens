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


@app.callback()
def _root() -> None:
    """AgentLens 命令组。"""


@app.command()
def demo() -> None:
    """端到端演示：两个版本各跑 n-trials → 报告 → diff → 门禁判定。"""
    import tempfile

    from .runner import load_dataset

    tasks = load_dataset(DEMO_DATASET)
    tmp = Path(tempfile.mkdtemp(prefix="lens-demo-"))
    store = ContentAddressedStore(tmp / "store")
    runner = Runner(store)

    console.rule("[bold]1. base 版本（单次成功率 0.6）[/bold]")
    runner.run(tasks, make_versioned_solver(0.6), version="v0.1-base", n_trials=4)
    console.rule("[bold]2. 候选版本（单次成功率 0.75 —— 小改进？）[/bold]")
    cand_run = runner.run(tasks, make_versioned_solver(0.75), version="v0.2-cand", n_trials=4)

    trajs = store.list_by_run(cand_run)
    grouped, order = _group_results(trajs)
    results = [grouped[tid] for tid in order]
    path = render_report("AgentLens demo：v0.2-cand", results, order, tmp / "report.html")
    console.print(f"HTML 报告: {path}")

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
    console.print(f"\n[green]demo 完成（全程离线，产物在 {tmp}）[/green]")


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
) -> None:
    """对数据集跑 n-trials 多采样评测并落盘轨迹（mock solver）。"""
    from .runner import load_dataset

    tasks = load_dataset(dataset)
    solver = make_versioned_solver(p_success=0.6)
    store = ContentAddressedStore(store_dir)
    runner = Runner(store)
    run_id = runner.run(tasks, solver, version=version, n_trials=n_trials)
    console.print(f"run_id={run_id}，{len(tasks)} 题 × {n_trials} trials 已落盘 → {store_dir}")


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
    trajs = store.list_by_run(run_id) if run_id else _latest_run(store)
    grouped, order = _group_results(trajs)
    results = [grouped[tid] for tid in order]
    path = render_report("AgentLens 评测报告", results, order, out)
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
) -> None:
    """两版本 diff + 门禁判定（observe/block 双模式）。"""
    store = ContentAddressedStore(store_dir)
    runs = sorted({t.run_id for t in _latest_run(store, all=True)})
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


def _latest_run(store: ContentAddressedStore, all: bool = False):  # noqa: A002
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
