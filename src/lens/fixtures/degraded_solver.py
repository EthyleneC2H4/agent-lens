"""门禁演示用退化 solver——恒定输出错误答案，制造全量退化 case。

只用于真实 PR 门禁演示分支（scripts/bootstrap-remote.sh 生成的 demo/*），
让 block 态真的红、observe 态评论里真的有退化清单。禁止用于正式评测。

契约注意：runner 传给 solver 的 task 是 Task 对象（属性访问），不是 dict。
"""

from __future__ import annotations

from typing import Any


def make_degraded_solver():
    """工厂签名 () -> Solver；返回 callable(task, trial_seed)。"""

    def solve(task: Any, trial_seed: int) -> tuple[str, list[str]]:
        inp = str(getattr(task, "input", "") or "")
        return (
            f"[degraded] 无法作答：{inp[:20]}",
            ["退化演示 solver：跳过推理，直接输出错误答案"],
        )

    return solve
