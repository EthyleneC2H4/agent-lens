"""n-trials 多采样并发执行器 —— dataset × trials 矩阵，轨迹先落盘再评分。

失败策略显式化：网络类错误（provider.NetworkError 及其子类）与任务失败分开计数；
单 job 最终失败只记录不中断整矩阵——评测平台必须先量化不稳定，而不是被它炸掉。
"""

from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Union

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


@dataclass
class SolverReply:
    """solver 的结构化返回：输出 + 过程 + 可选 token 记账。"""

    output: str
    steps: list[str] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""


PlainReply = Union[tuple[str, list[str]], SolverReply]
Solver = Callable[[Task, int], PlainReply]
"""solver(task, trial_seed) -> (output, steps) 或 SolverReply。被评对象的抽象。"""


@dataclass
class RunSummary:
    """一次 run 的显式统计：网络错与任务错分开计数，失败 job 隔离上报。"""

    run_id: str
    n_jobs: int = 0
    completed: int = 0
    retried_ok: int = 0          # 失败一次后重试成功的 job 数
    task_failures: int = 0       # 重试后仍失败的非网络类错误
    network_failures: int = 0    # 重试后仍失败的网路类错误
    failed_jobs: list[str] = field(default_factory=list)

    @property
    def ok_rate(self) -> float:
        return self.completed / self.n_jobs if self.n_jobs else 1.0


def _normalize(reply: PlainReply) -> SolverReply:
    if isinstance(reply, SolverReply):
        return reply
    output, steps = reply
    return SolverReply(output=output, steps=list(steps))


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
    ) -> RunSummary:
        """跑 dataset × n_trials 矩阵；返回 RunSummary。

        每个 job 独立重试一次：网络错与任务错分别计数；最终失败仅记录，
        不中断矩阵（缺失 trial 会反映在通过率与门禁里）。
        """
        from .provider import NetworkError

        run_id = f"{version}-seed{seed}"
        summary = RunSummary(run_id=run_id, n_jobs=len(tasks) * n_trials)

        def _one(i_task: int, i_trial: int) -> None:
            task = tasks[i_task]
            rng_seed = seed * 100003 + i_task * 1009 + i_trial * 17 + 1
            try:
                reply = solver(task, rng_seed)
                summary.completed += 1
            except NetworkError:
                try:
                    reply = solver(task, rng_seed + 7919)
                    summary.retried_ok += 1
                    summary.completed += 1
                except NetworkError as e2:
                    summary.network_failures += 1
                    summary.failed_jobs.append(f"{task.id}#t{i_trial}: network({e2})")
                    return
                except Exception as e2:  # noqa: BLE001 — 归入任务失败
                    summary.task_failures += 1
                    summary.failed_jobs.append(f"{task.id}#t{i_trial}: task({e2})")
                    return
            except Exception:  # noqa: BLE001 — 任务类失败同样兜底重试一次
                try:
                    reply = solver(task, rng_seed + 104729)
                    summary.retried_ok += 1
                    summary.completed += 1
                except Exception as e2:  # noqa: BLE001
                    summary.task_failures += 1
                    summary.failed_jobs.append(f"{task.id}#t{i_trial}: task({e2})")
                    return

            rep = _normalize(reply)
            traj = Trajectory(
                task_id=task.id,
                version=version,
                run_id=run_id,
                output=rep.output,
                steps=rep.steps,
                tokens=len(rep.output.split()) + sum(len(s.split()) for s in rep.steps),
                prompt_tokens=rep.prompt_tokens,
                completion_tokens=rep.completion_tokens,
                model=rep.model,
                metadata={"trial": i_trial, "gold": task.gold, "input": task.input},
            )
            self.store.put(traj)

        jobs = [(i, t) for i in range(len(tasks)) for t in range(n_trials)]
        with ThreadPoolExecutor(max_workers=self.n_workers) as pool:
            list(pool.map(lambda j: _one(*j), jobs))
        return summary


def make_versioned_solver(version_p_success: float, seed_base: int = 0):
    """构造一个固定单次成功率的 mock solver（演示「小改进可能是噪声」用）。"""

    def solver(task: Task, trial_seed: int) -> tuple[str, list[str]]:
        rng = random.Random(seed_base + trial_seed)
        ok = rng.random() < version_p_success
        steps = [f"step1 plan for {task.id}", f"step2 execute ({'ok' if ok else 'fail'})"]
        return (task.gold if ok else "wrong answer", steps)

    return solver


def make_llm_solver(provider, instruction: str = "") -> Solver:
    """真实模型 solver：把任务输入交给 provider，输出即答案（带 token 记账）。

    instruction 可选（prompt A/B 用）。provider 网络错包装为 NetworkError，
    让 runner 的分类计数生效。
    """
    from .provider import NetworkError

    def solver(task: Task, trial_seed: int) -> SolverReply:
        del trial_seed  # temperature=0，seed 仅用于 runner 内部记账
        prompt = f"{task.input}\n{instruction}\n直接给出最终答案，不要解释。" if instruction else (
            f"{task.input}\n直接给出最终答案，不要解释。"
        )
        try:
            res = provider.chat([{"role": "user", "content": prompt}])
        except NetworkError:
            raise
        except Exception as e:  # noqa: BLE001 — 传输层之外的异常按网络错兜底重试
            raise NetworkError(f"provider 调用失败: {e}") from e
        return SolverReply(
            output=res.text.strip(),
            steps=[f"[{res.model}] prompt={res.prompt_tokens} completion={res.completion_tokens}"],
            prompt_tokens=res.prompt_tokens,
            completion_tokens=res.completion_tokens,
            model=res.model or "unknown",
        )

    return solver
