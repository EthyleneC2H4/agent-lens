"""高质量轨迹导出 —— eval→RL flywheel 的 AgentLens 侧出口。

Harbor 式 rollout JSONL：task + trajectory + reward + source 溯源。
只导出「稳定答对」的任务轨迹（SFT 冷启动要质量不要侥幸）；
字段级对齐 AgentRL-Lab `rollout/schema.py` 的工作见 docs/export-schema.md（pending）。
"""

from __future__ import annotations

import json
from pathlib import Path

from .scorers import Scorer
from .store import ContentAddressedStore, Trajectory


def build_rollout(traj: Trajectory, reward: float, content_hash: str) -> dict[str, object]:
    """单条轨迹 → Harbor 式 rollout 记录。"""
    return {
        "id": f"{traj.task_id}#t{traj.metadata.get('trial', 0)}",
        "task": {
            "id": traj.task_id,
            "input": traj.metadata.get("input", ""),
            "gold": traj.metadata.get("gold", ""),
        },
        "trajectory": {"output": traj.output, "steps": traj.steps},
        "reward": reward,
        "source": {
            "platform": "agent-lens",
            "run_id": traj.run_id,
            "version": traj.version,
            "content_hash": content_hash,   # 内容寻址溯源：可回 store 验证
            "model": traj.model,
        },
    }


def export_rollouts(
    store: ContentAddressedStore,
    run_id: str,
    scorer: Scorer,
    min_task_pass_rate: float = 0.75,
) -> tuple[list[dict[str, object]], dict[str, float]]:
    """挑出稳定高分任务的轨迹，构造 rollout 记录列表。

    返回 (rollouts, per_task_rate)。通过率 < min_task_pass_rate 的任务整体剔除
    ——偶发答对（pass@1 高 pass^k 低）的轨迹会污染 SFT 冷启动集。
    """
    trajs = store.list_by_run(run_id)
    by_task: dict[str, list[tuple[Trajectory, str]]] = {}
    for t in trajs:
        h = store.put(t)   # 内容寻址：已有块零拷贝，仅拿哈希做溯源
        by_task.setdefault(t.task_id, []).append((t, h))

    per_task_rate = {
        tid: sum(
            scorer.score(t, {"gold": str(t.metadata.get("gold", ""))})
            for t, _ in pairs
        ) / len(pairs)
        for tid, pairs in by_task.items()
    }
    def _reward(t: Trajectory) -> float:
        return 1.0 if scorer.score(t, {"gold": str(t.metadata.get("gold", ""))}) else 0.0

    rollouts = [
        build_rollout(t, _reward(t), h)
        for tid, pairs in sorted(by_task.items())
        if per_task_rate[tid] >= min_task_pass_rate
        for t, h in pairs
    ]
    return rollouts, per_task_rate


def write_jsonl(records: list[dict[str, object]], out_path: str | Path) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return out


def load_jsonl_rollouts(path: str | Path) -> list[dict[str, object]]:
    """下游（AgentRL-Lab）侧加载校验：schema 必需字段齐全。"""
    required = {"id", "task", "trajectory", "reward", "source"}
    records = []
    with Path(path).open(encoding="utf-8") as f:
        for ln in f:
            if not ln.strip():
                continue
            rec = json.loads(ln)
            missing = required - set(rec)
            if missing:
                raise ValueError(f"rollout 缺字段 {missing}: id={rec.get('id')}")
            records.append(rec)
    return records
