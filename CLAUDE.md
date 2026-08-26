# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

AgentLens：Agent 回归评测门禁与稳定性度量平台，回答一个问题——**这个改动能不能合入？**
不做通用 trace dashboard（Langfuse 回答「发生了什么」，本项目回答「能不能合入」）。
文档阅读顺序：`README.md`（定位）→ `AGENTS.md`（工程规约）→ `ROADMAP.md`（阶段进度与勾选状态，§2 是不可协商的硬约束）→ `docs/`（gate-policy / judge-block-policy / export-schema）。

## 常用命令

```bash
uv sync                      # 安装依赖（uv 管理，uv.lock 入库）
uv run pytest -q             # 全量离线确定性测试（mock-first，零网络依赖）
uv run pytest tests/test_store_runner.py::test_store_roundtrip   # 单个测试
uv run ruff check .          # lint（line-length=100，select E/F/I/W）
uv run lens demo             # 端到端演示：两版本评测→报告→门禁（必须 EXIT=0）
```

CLI 子命令（Typer）：`demo / run / ui / runs / report / gate / smoke / calibrate / kappa-report / rescore / meta-eval / analyze / export / robustness`。

本地门禁模拟（无需 GitHub，见 docs/gate-policy.md §5）：

```bash
uv run lens run --version base --n-trials 5 --store-dir /tmp/demo-store
uv run lens run --version cand --n-trials 5 --store-dir /tmp/demo-store
uv run lens gate --store-dir /tmp/demo-store --mode block --out-json gate.json  # 退化时 exit 1
uv run python scripts/pr_comment.py --gate-json gate.json                       # 干跑渲染 PR 评论
```

数据集是 JSONL（字段 `id/input/gold/required_states`，可选 `extra` 携带任意元数据），内置样例在 `src/lens/fixtures/`。
接入任意被评 agent 用 `lens run --solver-spec "pkg.module:factory"`（工厂 `() -> Solver`，
Solver 即 `callable(task, trial_seed) -> (output, steps) | SolverReply`）；网络类错误抛
`lens.provider.NetworkError` 子类，runner 会与任务失败分开计数。

## 架构（数据流）

核心管线：**执行 → 内容寻址落盘 → 重放评分 → 双侧统计 → 门禁**

```
runner.py     dataset × n-trials 并发矩阵；单 job 失败隔离（NetworkError 与任务错分开计入 RunSummary）
    ↓ Trajectory
store.py      ★ 内容寻址轨迹库（两级 sha256：blocks/ 内容块 + index.jsonl 清单），同内容天然去重
    ↓ list_by_run 重放
scorers.py    exact_match（数值容差）/ key_state（BFCL V3 state-based mini）/ llm_judge
    ↓ list[list[bool]]（每题 n 个布尔结果）
metrics.py    pass@k（Codex 无偏估计器 · 乐观界）+ pass^k（tau-bench 悲观界）+ bootstrap CI
    ↓
regression.py case 级 diff（improved/regressed/fragile/unchanged）+ GatePolicy{observe, block}
    ↓
report.py（自包含 HTML，内联 CSS 零外链）／ ci.py（gate JSON → GitHub PR 评论幂等 upsert，MARKER 去重）
```

外围闭环模块：
- `calibration.py` ★ judge 校准闭环：210 例构造式已知答案池 → 预标注分层抽样（分歧项全保留）→ 人工复核 HTML → κ 报告
- `judge_lab.py` Cohen's κ、position-swap 一致率、长度偏置体检
- `meta_eval.py` TRAIL 式元评测：scorer 对已知好/坏轨迹分辨力满分才允许上岗（含 security）
- `robustness.py` 对抗鲁棒性套件：InjecAgent 式注入用例 + utility/ASR 双指标；`lens robustness --max-asr` 超阈值 exit 1（阈值语义见 docs/robustness-suite.md）
- `trace_analyzer.py` 失败轨迹聚类（工具错/格式近似错/规划错/空输出）
- `export.py` rollout JSONL 导出（eval→RL flywheel 出口；`--format agentrl` 对齐 AgentRL-Lab schema）
- `provider.py` mock-first LLM 接入：MockProvider 默认（确定性规则输出）；OpenAICompatibleProvider 走真实模型（transport 可注入以便离线测重试退避）

## 不许退化的招牌不变量

1. **Store trajectory first, judge later**：轨迹必须先内容寻址落盘，评分是对历史轨迹的重放——换 judge 重判分（`lens rescore`）是一等公民操作，不是重新跑评测。
2. **双侧分布夹逼**：pass@k（乐观界）与 pass^k（悲观界）必须一起报；单次 pass@1 波动达 pp 量级，门禁看分布不看点值。
3. **门禁分级**：observe 只报告不阻断；`llm_judge` 切 block 必须先过 `docs/judge-block-policy.md` 七项前提（κ≥0.6 / 误杀≤2% / swap≥95% 等）。规则 scorer（exact_match/key_state）不受此约束，可直接 block。
4. **判定与执行解耦（硬约束）**：Scorer 只吃 Trajectory + task dict；新增 scorer 必须可离线测试，LLM 依赖一律走 provider 抽象。

## 工程规约（摘自 AGENTS.md / ROADMAP §2）

- **mock-first**：每个外部依赖必须有确定性测试替身；任何改动后 `uv run pytest -q` 全绿且 `uv run lens demo` EXIT=0 才算完成。
- **免费资源红线**：禁止一切付费 API。真实模型只走**免费模型池**（OpenAI-compatible）：默认 NVIDIA 端点，`AGENTLENS_BASE_URL`/`AGENTLENS_MODEL` 可覆盖（当前实测 OpenCode Zen free 池，仅 `-free` 模型）；key 只从环境变量 `AGENTLENS_API_KEY` 读取，缺失自动降级 mock（`lens smoke` 例外：缺 key 直接 exit 2）。严禁硬编码端点或密钥。
- **人工标注边界**：κ 数字需要真人标注会话——agent 负责把预标注、复核界面、一致性计算全部自动化，把人的工作量压到最小；不得用 LLM 生成「伪人工」标注冒充。
- **指标数学**：`metrics.py` 改动必须过手算用例验证（见 `tests/test_metrics_regression.py`）。
- **代码风格**：Python ≥3.11；src 布局；类型注解；中文简短 docstring；CLI 用 Typer（保留 `@app.callback()`）+ rich（注意 markup 吞方括号陷阱）；新增依赖需在 commit message 里说明理由。
- **提交身份**：ROADMAP §2.5 规定了固定的 `-c user.name/-c user.email` 提交身份，提交前以该节为准。
- **收工纪律**：pytest 绿 → 更新 `ROADMAP.md` 勾选状态 → 一并提交；每 Phase 至少一个 commit。

## 当前状态与已知债

- 94 个离线测试全绿；Phase A/B/D/F 完成（B 含真实 PR 两态演示：[PR#1 observe](https://github.com/EthyleneC2H4/agent-lens/pull/1) 放行 / [PR#2 block](https://github.com/EthyleneC2H4/agent-lens/pull/2) 阻断，截图在 `docs/evidence/`），E 出口就绪（AgentRL-Lab 对齐 + OTel）——仅剩 Phase C 的 κ 人工标注数字。
- 唯一未竟项卡用户动作：人工标注会话（~30 分钟，产出 κ vs 人工数字；硬约束 3 不许 LLM 代劳——LLM 代标仅可作管线彩排，不得回填决策数字）。κ 数字落地后即可打 v1.0 tag。
- gate 有 fail-closed 守卫（同 run 自 diff 拒判 / 空 base 或 cand 证据拒放行）；演示过程曾暴露「solver 契约破裂 → cand run 为空 → 门禁静默放行」缺陷，回归测试钉死在 `tests/test_degraded_solver.py` 与 `tests/test_report_ci.py`。
- 数据集 JSONL 支持 `extra` 字段（任意元数据随轨迹落盘至 `metadata.extra`，
  事后重放任意 scorer 口径的传输通道）。
