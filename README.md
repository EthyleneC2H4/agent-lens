# AgentLens

Agent 回归评测门禁与稳定性度量平台（v0.9-rc）。回答一个问题：**这个改动能不能合入？**

> 详细提案与路线图见 `ROADMAP.md`；门禁阈值规则见 `docs/gate-policy.md`。

不是又一个 Langfuse——不做通用 trace dashboard。Langfuse 回答「发生了什么」，AgentLens 回答「能不能合入」。

## 快速上手

```bash
uv sync
uv run pytest -q          # 45 个离线确定性测试
uv run lens demo          # 两版本对比评测 → pass 分布 → HTML 报告 → observe/block 双模式门禁
```

## 架构

```
provider.py   mock-first LLM 接入：ChatResult token 记账、指数退避重试（transport 可注入）
runner.py     dataset × n-trials 并发矩阵；失败分类隔离（网络错 vs 任务错）；RunSummary 显式统计
store.py      ★ 内容寻址轨迹库（两级 sha256 + run 级二级索引）：同内容去重、judge 可重放重判分
scorers.py    exact_match（数值容差）/ key_state（BFCL V3 state-based mini）/ llm_judge
metrics.py    pass@k 无偏估计器（Codex）/ pass^k（tau-bench 悲观界）/ bootstrap CI
regression.py 版本 case 级 diff + GatePolicy{observe, block}
judge_lab.py  Cohen's κ · position-swap · 长度偏置 —— judge 校准闭环
calibration.py ★ 校准闭环：210 例构造式已知答案池 → 预标注分层 → 人工复核 HTML → κ 报告
meta_eval.py  TRAIL 式自检：scorer 对已知好/坏轨迹的分辨力满分才允许上岗
trace_analyzer.py 失败轨迹模式聚类：工具错 / 格式近似错 / 规划错 / 空输出
robustness.py 对抗鲁棒性套件：InjecAgent 式注入用例 + utility/ASR 双指标（docs/robustness-suite.md）
export.py     Harbor 式 rollout JSONL 导出（eval→RL flywheel 出口，带溯源哈希）
ci.py         CI 门禁胶水：gate JSON → GitHub PR 评论（幂等 upsert）
report.py     自包含 HTML 报告（内联 CSS 零外链 + 成本记账区 + per-case bootstrap CI）
cli.py        lens demo/run/runs/report/gate/smoke/calibrate/kappa-report/rescore/meta-eval/analyze/export/robustness
```

## 核心设计

1. **Store trajectory first, judge later**：轨迹按内容寻址落盘，评分是对历史轨迹的重放
   ——换 judge 模型重判分是一等公民操作（`lens rescore`）。
2. **双侧分布夹逼**：单次 pass@1 波动达 pp 量级；pass@k（乐观界）与 pass^k（悲观界）
   一起报，小改进是否真实一目了然。
3. **门禁分级**：observe 模式只报告不阻断；κ 与误杀率达标后才切 block——
   七项前提见 `docs/judge-block-policy.md`。
4. **评测器先过体检**：`lens meta-eval` 元评测——scorer 对已知好/坏轨迹分辨力不满分不上岗。

## 实测状态（2026-08-26）

| 项 | 状态 |
|---|---|
| 离线测试 | ✅ 83 个全绿（mock-first，零网络依赖） |
| 门禁管线 | ✅ 本地模拟注入退化版本被 block 拦截；gate JSON 含 CI 噪声甄别字段；GitHub Action workflow 就绪 |
| judge 校准 | 🟡 工具链完备 + 真 judge 预标注实测（swap=1.0@24 组、构造池一致率 90.5%@210 例）；**κ vs 人工待标注会话**（~30 分钟） |
| 真实模型通路 | ✅ 实测通过：`lens smoke` 通过率 1.00、`demo --provider real` EXIT=0（OpenCode Zen free 池 · nemotron-3-ultra-free） |
| 鲁棒性套件 | ✅ 注入用例 + utility/ASR 双指标离线实测；真实 agent 接入待外部 solver |
| flywheel 出口 | ✅ rollout JSONL 导出+回读校验；与 AgentRL-Lab 字段级对齐 pending |

## 差异化口径

多采样统计（Inspect AI epochs）、版本 diff+CI（promptfoo）单项各有先例；
AgentLens 的差异是把「稳定性画像 + 判定解耦重放 + judge 校准 + scorer 自检」
打包成面向 agent 任务的一等公民门禁工作流。

## Roadmap

当前进度与剩余项见 `ROADMAP.md` §3–§4。v1.0 封版条件：
真实 PR 流程演示 observe/block 两态 + κ 校准数字落地 + 真实通路冒烟实测。
