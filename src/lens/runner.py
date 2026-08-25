"""n-trials 多采样并发执行器 —— dataset × trials 矩阵，轨迹先落盘再评分。"""

from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from .store import ContentAddressedStore, Trajectory


class Task(BaseModel):
    id: str
    input: str
    gold: str = ""
    required_states: list[str] = []


def load_dataset(path: str | Path) -> list[Task]:
    """JSONL 数据集（id/input/gold/required_states）。"""
    tasks = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                tasks.append(Task.model_validate_json(line))
    if not tasks:
        raise ValueError(f"数据集为空: {path}")
    return tasks


Solver = Callable[[Task, int], tuple[str, list[str]]]
"""solver(task, trial_seed) -> (output, steps)。被评对象的抽象。"""


class Runner:
    def __init__(self, store: ContentAddressedStore, n_workers: int = 4) -> None:
        self.store = store
        self.n_workers = n_workers

    def run(
        self,
        tasks: list[Task],
        solver: Solver,
        version: str,
        n_trials: int,
        seed: int = 0,
    ) -> str:
        """跑 dataset × n_trials 矩阵；返回 run_id。失败重试 1 次（网络类抖动兜底）。"""
        run_id = f"{version}-seed{seed}"

        def _one(i_task: int, i_trial: int) -> None:
            task = tasks[i_task]
            rng_seed = seed * 100003 + i_task * 1009 + i_trial * 17 + 1
            output, steps = _with_retry(solver, task, rng_seed)
            traj = Trajectory(
                task_id=task.id,
                version=version,
                run_id=run_id,
                output=output,
                steps=steps,
                tokens=len(output.split()) + sum(len(s.split()) for s in steps),
                metadata={"trial": i_trial, "gold": task.gold, "input": task.input},
            )
            self.store.put(traj)

        jobs = [(i, t) for i in range(len(tasks)) for t in range(n_trials)]
        with ThreadPoolExecutor(max_workers=self.n_workers) as pool:
            list(pool.map(lambda j: _one(*j), jobs))
        return run_id


def _with_retry(solver: Solver, task: Task, rng_seed: int):
    try:
        return solver(task, rng_seed)
    except Exception:  # noqa: BLE001 — 失败重试一次后仍抛出
        return solver(task, rng_seed)


def make_versioned_solver(version_p_success: float, seed_base: int = 0):
    """构造一个固定单次成功率的 mock solver（演示「小改进可能是噪声」用）。"""

    def solver(task: Task, trial_seed: int) -> tuple[str, list[str]]:
        rng = random.Random(seed_base + trial_seed)
        ok = rng.random() < version_p_success
        steps = [f"step1 plan for {task.id}", f"step2 execute ({'ok' if ok else 'fail'})"]
        return (task.gold if ok else "wrong answer", steps)

    return solver
