# 门禁阈值规则（Gate Policy）

> 本文回答：什么算「显著退化」，observe 何时切 block，CI 里每个环节失败意味着什么。

## 1. 判定语义总览

| 层 | 内容 | 失败后果 |
|---|---|---|
| L0 测试 | `uv run pytest -q` 离线全绿 | job 红，与评测无关但先拦 |
| L1 评测 | base / cand 两版本 × n-trials 落盘轨迹 | 缺失 trial 会直接反映在通过率里 |
| L2 门禁 | case 级 diff + 阈值判定 | observe=只报告；block=exit 1 |

## 2. 「显著退化」的默认规则

单次 pass@1 波动可达 pp 量级——所以不看单点，看分布：

1. **case 级计数（主规则）**：`cand_passes < base_passes` 的 case 数
   `> GatePolicy.max_regressed_cases`（默认 0）即违规。
2. **候选下限（辅助规则）**：候选整体通过率 `< min_success_rate` 时违规（可选启用）。
3. **噪声甄别**：case 级 bootstrap CI 重叠说明差异可能只是采样噪声
   （见报告页 per-task CI 列）；CI 不重叠且方向为负 → 高置信退化，
   建议人工复核后计入阻断依据。
4. **双侧分布夹逼**：pass@k 升 + pass^k 平 = 「偶尔能对」的不稳定改进；
   pass^k 才是「每次都对」的能力。门禁以 pass^k 与逐 case 计数为准。

## 3. observe → block 的切换前提

**硬前提**（缺一不可，详见 `docs/judge-block-policy.md`）：

- judge 校准数字达标：κ ≥ 0.6、误杀率 ≤ 2%、position-swap 一致率 ≥ 95%；
- 连续 ≥ 20 次 PR 观察期无误杀记录；
- 规则 scorer（exact_match/key_state）覆盖的任务可直接用 block，
  不受 judge 前提约束——确定性 scorer 不需要校准豁免。

**操作**：workflow 手动触发 `workflow_dispatch` 选 `mode=block`，
或在仓库变量里固化 `MODE=block`。

## 4. 接入自己的被评 agent

workflow 中两处 `lens run` 是接入点。任意 Python 仓库：

```bash
uv run lens run --dataset my_tasks.jsonl --version "base-$SHA" \
    --solver-spec "my_pkg.solvers:make_solver" --n-trials 8 --store-dir .lensstore
```

`solver_spec` 格式 `pkg.module:factory`，工厂签名 `() -> Solver`；
Solver 即 `callable(task, trial_seed) -> (output, steps) | SolverReply`。
网络类错误请抛 `lens.provider.NetworkError` 子类，runner 会与任务失败分开计数。

## 5. 本地模拟（无需 GitHub）

```bash
uv run lens run --version base --n-trials 5 --store-dir /tmp/demo-store
uv run lens run --version cand --n-trials 5 --store-dir /tmp/demo-store
uv run lens gate --store-dir /tmp/demo-store --mode block --out-json gate.json  # 退化时 exit 1
python scripts/pr_comment.py --gate-json gate.json                              # 干跑渲染评论
```

自动化等价测试：`tests/test_report_ci.py::test_injected_regression_blocked_and_comment_renders`
（注入 0.8→0.2 成功率退化，断言 block 拦截与评论 markdown 结构）。

## 6. 已知边界

- 本仓库 CI 用内置 mock solver 演示管线机制（同分布 → diff 全 unchanged 是预期）；
- 真实基线缓存（跨 PR 复用 main 的 store artifact）是后续优化，当前每 PR 双跑成本可接受（离线 mock 零成本）；
- fork PR 的 `pull-requests: write` 权限受限时评论步骤会跳过（GitHub 默认行为），gate 判定本身不受影响。
