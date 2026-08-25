"""内容寻址轨迹库 —— store trajectory first, judge later 的存储层。

两级哈希：内容块按 sha256 寻址存为独立 JSON 文件，清单索引（JSONL）
记录 run_id/version → 块哈希的映射。同一内容天然去重；judge 可对
历史轨迹重放评分而无需重新执行 agent。

run 级二级索引（runs/<hash16>.json + runs_manifest.json）：list_by_run
只读本 run 的哈希清单，不再全量扫描 index；旧式库首次访问时懒重建。
写路径在进程内锁下做读-改-写，保证并发 put 一致；私有 helper 不自行加锁。
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field


class Trajectory(BaseModel):
    """被评对象的一条轨迹：任务输入、agent 输出与过程信息。"""

    task_id: str
    version: str                      # 被评对象版本号（git sha / prompt 版本等）
    run_id: str                       # 一次评测批次
    output: str
    steps: list[str] = Field(default_factory=list)
    tokens: int = 0                   # agent 侧输出规模代理（词数），兼容旧字段
    prompt_tokens: int = 0            # LLM 调用记账：输入 token（真实端点回传，mock 为估算）
    completion_tokens: int = 0        # LLM 调用记账：输出 token
    model: str = ""                   # 产出该轨迹的模型名（mock / 真实端点模型）
    metadata: dict[str, object] = Field(default_factory=dict)


@dataclass
class RunInfo:
    """run 级元信息：seq 为该 run 首次出现的顺序（0 起），与字母序无关。"""

    run_id: str
    n_trajectories: int
    seq: int


def _hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _run_file_stem(run_id: str) -> str:
    # run_id 可能含路径不安全字符：文件名取其哈希前缀，真名存文件内容
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]


class ContentAddressedStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.blocks_dir = self.root / "blocks"
        self.index_path = self.root / "index.jsonl"
        self._runs_dir = self.root / "runs"
        self._manifest_path = self.root / "runs_manifest.json"
        self.blocks_dir.mkdir(parents=True, exist_ok=True)
        self._runs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._manifest: dict[str, dict[str, int]] | None = None   # 懒加载缓存
        self._run_hashes: dict[str, list[str]] = {}               # 进程内缓存

    # ---------------- 写路径 ----------------

    def put(self, traj: Trajectory) -> str:
        """写入轨迹，返回内容哈希；相同内容自动去重。"""
        payload = traj.model_dump_json()
        h = _hash(payload)
        block = self.blocks_dir / f"{h}.json"
        with self._lock:
            if not block.exists():
                block.write_text(payload, encoding="utf-8")
            # 先记 run 账再落 index 行：懒重建扫不到本行，避免首条轨迹被双计
            self._record_run(traj.run_id, h)
            with self.index_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"hash": h, **traj.model_dump()}) + "\n")
        return h

    def _record_run(self, run_id: str, h: str) -> None:
        """锁内调用：manifest 计数 +1、run 文件追加哈希。"""
        m = self._load_manifest()
        ent = m.get(run_id)
        if ent is None:
            ent = {"seq": max((e["seq"] for e in m.values()), default=-1) + 1, "n": 0}
            m[run_id] = ent
        ent["n"] += 1
        self._manifest_path.write_text(
            json.dumps(m, ensure_ascii=False), encoding="utf-8"
        )
        hashes = self._hashes_for(run_id)
        hashes.append(h)
        self._write_run_file(run_id, hashes)

    def _write_run_file(self, run_id: str, hashes: list[str]) -> None:
        path = self._runs_dir / f"{_run_file_stem(run_id)}.json"
        path.write_text(
            json.dumps({"run_id": run_id, "hashes": hashes}, ensure_ascii=False),
            encoding="utf-8",
        )

    def _read_run_file(self, run_id: str) -> list[str] | None:
        path = self._runs_dir / f"{_run_file_stem(run_id)}.json"
        if not path.exists():
            return None
        rec = json.loads(path.read_text(encoding="utf-8"))
        return list(rec.get("hashes", []))

    def _load_manifest(self) -> dict[str, dict[str, int]]:
        """懒加载 manifest；缺失或损坏时扫 index 全量重建（含 run 文件回填）。"""
        if self._manifest is not None:
            return self._manifest
        if self._manifest_path.exists():
            try:
                self._manifest = json.loads(
                    self._manifest_path.read_text(encoding="utf-8")
                )
                return self._manifest
            except json.JSONDecodeError:
                pass  # 损坏即重建
        order: dict[str, int] = {}
        counts: dict[str, int] = {}
        hashes_by_run: dict[str, list[str]] = {}
        if self.index_path.exists():
            for line in self.index_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                rid = rec["run_id"]
                if rid not in order:
                    order[rid] = len(order)
                    counts[rid] = 0
                    hashes_by_run[rid] = []
                counts[rid] += 1
                hashes_by_run[rid].append(rec["hash"])
        self._manifest = {rid: {"seq": order[rid], "n": counts[rid]} for rid in order}
        self._manifest_path.write_text(json.dumps(self._manifest), encoding="utf-8")
        for rid, hs in hashes_by_run.items():
            self._run_hashes[rid] = hs
            self._write_run_file(rid, hs)
        return self._manifest

    def _hashes_for(self, run_id: str) -> list[str]:
        """锁内或只读场景调用：run 哈希清单，缓存缺失时自愈重建。"""
        cached = self._run_hashes.get(run_id)
        if cached is not None:
            return cached
        from_file = self._read_run_file(run_id)
        if from_file is not None:
            self._run_hashes[run_id] = from_file
            return from_file
        hashes: list[str] = []
        if self.index_path.exists():
            for line in self.index_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec["run_id"] == run_id:
                    hashes.append(rec["hash"])
        self._run_hashes[run_id] = hashes
        self._write_run_file(run_id, hashes)
        return hashes

    # ---------------- 读路径 ----------------

    def get(self, content_hash: str) -> Trajectory:
        block = self.blocks_dir / f"{content_hash}.json"
        if not block.exists():
            raise KeyError(f"未知内容块 {content_hash}")
        return Trajectory.model_validate_json(block.read_text(encoding="utf-8"))

    def list_by_run(self, run_id: str) -> list[Trajectory]:
        with self._lock:
            hashes = list(self._hashes_for(run_id))   # 快照，避免并发 put 中迭代
        return [self.get(h) for h in hashes]

    def list_runs(self) -> list[RunInfo]:
        """全部 run 元信息，按首次出现序（gate 默认基线选择的数据源）。"""
        with self._lock:
            m = dict(self._load_manifest())
        entries = sorted(m.items(), key=lambda kv: kv[1]["seq"])
        return [
            RunInfo(run_id=rid, n_trajectories=v["n"], seq=v["seq"])
            for rid, v in entries
        ]

    def dedup_hits(self) -> int:
        """索引条数 - 唯一块数 = 去重命中数（内容寻址的直接收益）。"""
        if not self.index_path.exists():
            return 0
        n_index = sum(1 for _ in self.index_path.open(encoding="utf-8"))
        n_blocks = len(list(self.blocks_dir.glob("*.json")))
        return max(n_index - n_blocks, 0)
