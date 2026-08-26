"""高质量轨迹导出 —— eval→RL flywheel 的 AgentLens 侧出口。

Harbor 式 rollout JSONL：task + trajectory + reward + source 溯源。
只导出「稳定答对」的任务轨迹（SFT 冷启动要质量不要侥幸）；
AgentRL-Lab `rollout/schema.py` 字段级对齐见 to_agentrl_trajectory 与
docs/export-schema.md。reward_detail 为可选部分分数通道（key_state 命中比例）。
"""

from __future__ import annotations

import json
from pathlib import Path

from .scorers import Scorer
from .store import ContentAddressedStore, Trajectory


def build_rollout(
    traj: Trajectory,
    reward: float,
    content_hash: str,
    reward_detail: dict[str, float] | None = None,
) -> dict[str, object]:
    """单条轨迹 → Harbor 式 rollout 记录。reward_detail 为可选部分分数通道。"""
    rec = {
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
    if reward_detail is not None:
        rec["reward_detail"] = reward_detail
    return rec


def _key_state_fraction(traj: Trajectory) -> dict[str, float] | None:
    """部分分数通道：required_states 的命中比例；无状态断言的任务返回 None。"""
    req = [str(s) for s in (traj.metadata.get("required_states") or [])]
    if not req:
        return None
    joined = "\n".join(traj.steps)
    frac = sum(1 for s in req if s in joined) / len(req)
    return {"key_state_fraction": round(frac, 4)}


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
        build_rollout(t, _reward(t), h, reward_detail=_key_state_fraction(t))
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


# ---------------- AgentRL-Lab 兼容出口（rollout/schema.py 字段级对齐） ----------------

AGENTRL_TRAJECTORY_FIELDS = frozenset(
    {"env_name", "seed", "transitions", "total_reward", "total_obs_tokens", "metadata"}
)


def to_agentrl_trajectory(rec: dict[str, object]) -> dict[str, object]:
    """Harbor 式记录 → AgentRL-Lab `rollout/schema.Trajectory` 兼容字典。

    映射规则：steps 逐条转 Transition(action=步骤, done=False)，末条追加
    Transition(action=最终输出, reward, done=True)；obs 仅首步携带任务输入
    （对齐其「obs 存压缩后观测原文」语义）；tokens 用词数近似。
    """
    task = rec["task"]
    assert isinstance(task, dict)
    traj = rec["trajectory"]
    assert isinstance(traj, dict)
    source = rec["source"]
    assert isinstance(source, dict)
    steps: list[str] = list(traj.get("steps") or [])  # type: ignore[arg-type]
    output = str(traj.get("output", ""))
    reward = float(rec["reward"])  # type: ignore[arg-type]

    transitions = [
        {
            "obs": str(task.get("input", "")) if i == 0 else "",
            "action": s,
            "reward": 0.0,
            "done": False,
            "tokens": len(str(s).split()),
        }
        for i, s in enumerate(steps)
    ]
    transitions.append(
        {
            "obs": "",
            "action": output,
            "reward": reward,
            "done": True,
            "tokens": len(output.split()),
        }
    )
    return {
        "env_name": f"agentlens/{task['id']}",
        "seed": None,
        "transitions": transitions,
        "total_reward": reward,
        "total_obs_tokens": sum(int(t["tokens"]) for t in transitions),  # type: ignore[index]
        "metadata": {"platform": source.get("platform"), "run_id": source.get("run_id"),
                     "version": source.get("version"), "content_hash": source.get("content_hash")},
    }


def export_agentrl_format(
    records: list[dict[str, object]],
) -> list[dict[str, object]]:
    """整批转换并做字段契约自检（缺字段/多字段即抛错）。"""
    out = [to_agentrl_trajectory(r) for r in records]
    for t in out:
        extra = set(t) - AGENTRL_TRAJECTORY_FIELDS
        if extra:
            raise ValueError(f"AgentRL-Lab schema 多出字段: {extra}")
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


# ---------------- OTel collector 兼容出口（OTLP JSON traces） ----------------


def to_otlp_document(
    store: ContentAddressedStore,
    run_id: str,
    scorer: Scorer,
) -> dict[str, object]:
    """run 全部轨迹 → OTLP/JSON resourceSpans 文档（collector /v1/traces 可直收）。

    映射：每条轨迹一个 span；traceId/spanId 由内容寻址派生
    （sha256(run_id:task_id:trial)，确定性可复现）。诚实边界：轨迹库不存墙钟时间，
    startTimeUnixNano 用 trial 序号推导的占位值——只保证单调与稳定，不是真实时刻。
    判定走重放评分（judge later 不变量）；pass→STATUS_CODE_OK，否则 ERROR。
    """
    import hashlib

    trajs = sorted(
        store.list_by_run(run_id),
        key=lambda t: (t.task_id, int(t.metadata.get("trial", 0))),
    )
    spans = []
    for t in trajs:
        trial = int(t.metadata.get("trial", 0))
        h = hashlib.sha256(f"{t.run_id}:{t.task_id}:{trial}".encode()).hexdigest()
        ok = scorer.score(t, {"gold": str(t.metadata.get("gold", ""))})
        start_nano = trial * 1_000_000_000   # 占位时间：序号推导，非墙钟
        attrs = [
            {"key": "lens.task_id", "value": {"stringValue": t.task_id}},
            {"key": "lens.run_id", "value": {"stringValue": t.run_id}},
            {"key": "lens.version", "value": {"stringValue": t.version}},
            {"key": "lens.trial", "value": {"intValue": str(trial)}},
            {"key": "lens.pass", "value": {"boolValue": ok}},
            {"key": "lens.gold", "value": {"stringValue": str(t.metadata.get("gold", ""))}},
            {"key": "lens.tokens", "value": {"intValue": str(t.tokens)}},
            {"key": "lens.model", "value": {"stringValue": t.model or ""}},
        ]
        status = (
            {"code": "STATUS_CODE_OK"} if ok
            else {"code": "STATUS_CODE_ERROR", "message": "scorer 判定失败"}
        )
        spans.append({
            "traceId": h[:32],
            "spanId": h[32:48],
            "name": t.task_id,
            "kind": "SPAN_KIND_INTERNAL",
            "startTimeUnixNano": str(start_nano),
            "endTimeUnixNano": str(start_nano + max(1, t.tokens) * 1_000_000),
            "attributes": attrs,
            "status": status,
        })
    return {
        "resourceSpans": [{
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": "agent-lens"}},
                ]
            },
            "scopeSpans": [{
                "scope": {"name": "lens.export"},
                "spans": spans,
            }],
        }]
    }


def push_to_collector(
    document: dict[str, object], url: str, timeout_s: float = 10.0
) -> tuple[bool, str]:
    """OTLP JSON POST 到 collector（如 http://127.0.0.1:4318/v1/traces）。

    显式 User-Agent（网关 bot 防护教训）；网络失败返回 (False, 原因) 不抛出。
    """
    import urllib.request

    req = urllib.request.Request(  # noqa: S310
        url,
        data=json.dumps(document, ensure_ascii=False).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "agentlens/0.9 (otel export)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
            code = resp.status
            return code in (200, 202), f"HTTP {code}"
    except Exception as e:  # noqa: BLE001 — 推送失败如实上报给调用方决定退出码
        return False, f"{type(e).__name__}: {e}"[:200]
