"""内容寻址轨迹库 —— store trajectory first, judge later 的存储层。

两级哈希：内容块按 sha256 寻址存为独立 JSON 文件，清单索引（JSONL）
记录 run_id/version → 块哈希的映射。同一内容天然去重；judge 可对
历史轨迹重放评分而无需重新执行 agent。
"""

from __future__ import annotations

import hashlib
import json
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


def _hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ContentAddressedStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.blocks_dir = self.root / "blocks"
        self.index_path = self.root / "index.jsonl"
        self.blocks_dir.mkdir(parents=True, exist_ok=True)

    def put(self, traj: Trajectory) -> str:
        """写入轨迹，返回内容哈希；相同内容自动去重。"""
        payload = traj.model_dump_json()
        h = _hash(payload)
        block = self.blocks_dir / f"{h}.json"
        if not block.exists():
            block.write_text(payload, encoding="utf-8")
        with self.index_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"hash": h, **traj.model_dump()}) + "\n")
        return h

    def get(self, content_hash: str) -> Trajectory:
        block = self.blocks_dir / f"{content_hash}.json"
        if not block.exists():
            raise KeyError(f"未知内容块 {content_hash}")
        return Trajectory.model_validate_json(block.read_text(encoding="utf-8"))

    def list_by_run(self, run_id: str) -> list[Trajectory]:
        out = []
        if not self.index_path.exists():
            return out
        for line in self.index_path.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec["run_id"] == run_id:
                out.append(Trajectory.model_validate(rec))
        return out

    def dedup_hits(self) -> int:
        """索引条数 - 唯一块数 = 去重命中数（内容寻址的直接收益）。"""
        if not self.index_path.exists():
            return 0
        n_index = sum(1 for _ in self.index_path.open(encoding="utf-8"))
        n_blocks = len(list(self.blocks_dir.glob("*.json")))
        return max(n_index - n_blocks, 0)
