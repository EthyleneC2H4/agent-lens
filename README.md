# AgentLens

Agent 回归评测门禁与稳定性度量平台（MVP）。回答一个问题：**这个改动能不能合入？**

> 详细提案与路线图见 `../proposals/02-agent-lens.md`

不是又一个 Langfuse——不做通用 trace dashboard。Langfuse 回答「发生了什么」，AgentLens 回答「能不能合入」。

## 快速上手

```bash
uv sync
uv run pytest -q
uv run lens demo    # 两版本对比评测 → pass 分布 → HTML 报告 → observe/block 双模式门禁
```

## 架构

```
provider.py   mock-first LLM 接入（MockProvider 默认 / OpenAI-compatible 免费节点可选）
runner.py     dataset × n-trials 并发矩阵；轨迹先落盘再评分
store.py      ★ 内容寻址轨迹库（两级 sha256）：同内容去重、judge 可重放重判分
scorers.py    exact_match（数值容差）/ key_state（BFCL V3 state-based mini）/ llm_judge
metrics.py    pass@k 无偏估计器（Codex）/ pass^k（tau-bench 悲观界）/ bootstrap CI
regression.py 版本 case 级 diff + GatePolicy{observe, block}
judge_lab.py  Cohen's κ · position-swap · 长度偏置 —— judge 校准闭环
report.py     自包含 HTML 报告（内联 CSS 零外链）
cli.py        lens run / report / gate / demo
```

## 核心设计

1. **Store trajectory first, judge later**：轨迹按内容寻址落盘，评分是对历史轨迹的重放
   ——换 judge 模型重判分是一等公民操作（SWE-bench `report` 思想在 agent 场景的落地）。
2. **双侧分布夹逼**：单次 pass@1 波动达 pp 量级；pass@k（乐观界）与 pass^k（悲观界）
   一起报，小改进是否真实一目了然。
3. **门禁分级**：observe 模式只报告不阻断；κ 与误杀率达标后才切 block——
   「中等一致的 judge 凭什么拦工程师」的正面回答。

## 差异化口径

多采样统计（Inspect AI epochs）、版本 diff+CI（promptfoo）单项各有先例；
AgentLens 的差异是把「稳定性画像 + 判定解耦重放 + judge 校准」打包成面向 agent 任务的一等公民门禁工作流。

## Roadmap

- W2：GitHub Action 门禁接入（PR 评论评测 diff，超阈值 fail）
- W3–4：200 例人工标注集 → κ 与偏置体检数字；TRAIL 自检 runner（附录位）
- W5–8：高质量轨迹导出喂 AgentRL-Lab SFT 冷启动（eval→RL flywheel）；对抗鲁棒性评测套件
