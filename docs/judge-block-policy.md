# 切 block 的前提条件（Judge Gate Policy）

> 本文回答 roadmap 的核心质问：**「中等一致的 judge 凭什么拦工程师？」**
> 结论先行：judge 门槛不达标时，门禁只能 observe；以下数字全部达标才允许切 block。

## 1. 为什么需要这份文档

- LLM-as-judge 与人工的一致性文献区间约 κ≈0.44–0.53（中等一致）；
- κ=0.5 意味着大量「随机一致」被计入——直接拿它拦 PR，等于让一个
  半可靠的评审员拥有否决权；
- 但 judge 不是没用：在**规则 scorer 覆盖不了的任务**上它是唯一自动化判定手段。
  正确姿势是先量化、再上岗。

## 2. 硬性前提（全部满足才允许 mode=block）

| # | 指标 | 阈值 | 测量方式 |
|---|---|---|---|
| P1 | judge–人工 Cohen's κ | **≥ 0.6**（substantial） | `lens kappa-report`，≥200 例人工标注集 |
| P2 | κ bootstrap 95% CI 下界 | **≥ 0.5** | 同上（n_boot=2000） |
| P3 | 误杀率（judge 判错好输出） | **≤ 2%** | 报告页 `false_block_rate`（分母 = 人工判对的例数） |
| P4 | 漏杀率（放过坏输出） | **≤ 10%** | 报告页 `miss_rate`——漏杀只伤检出力，容忍度高于误杀 |
| P5 | position-swap 一致率 | **≥ 95%** | `swap_consistency`（成对模式，24+ 组起步） |
| P6 | 长度偏置三桶极差 | **≤ 15pp** | 报告页 length_bias（short/mid/long 通过率差） |
| P7 | 灰度观察期 | **连续 ≥ 20 次 PR** observe 运行 | CI 历史；期间误杀记录 = 0 |

P3 是一票否决项：一次误杀消耗的信任远大于十次漏杀。

## 3. 分 scorer 适用范围

- `exact_match` / `key_state`（确定性规则）：**不需要本豁免流程**，
  可直接 block——确定性 scorer 没有 κ 问题，只有数据集覆盖问题；
- `llm_judge`：必须走 §2 全部前提；
- 混合任务集：按 task 子集分别声明模式，禁止「一个 judge 全局 block」。

## 4. 复检与回退触发器

切 block 后出现任一情况立即回退 observe 并复检：

1. 工程师申诉成立 ≥ 2 次 / 100 个被拦 PR；
2. 被评 agent 或 judge 模型换版（store 重放重跑 §2 全套数字）；
3. 数据集分布漂移（新任务类型占比 > 20%）。

## 5. 操作闭环

```bash
uv run lens calibrate --out-dir calib            # 池 + 复核页
# （人工）浏览器打开 calib/review.html 批量裁决 → 导出 labels.jsonl
uv run lens kappa-report --pool calib/pool.jsonl --labels calib/labels.jsonl \
    --judge numeric --pairs calib/pairs.jsonl --out reports/kappa.html
# 对照 §2 七项 → 全绿才允许：
uv run lens gate --mode block ...
```

## 6. 当前状态（诚实边界）

- [x] 校准池 210 例 + 成对 24 组（构造真值，seed 可复现）
- [x] 预标注分层抽样 + 复核 HTML（judge 建议不预选，防锚定）
- [x] κ 报告（手算用例验证）/ swap 一致性 / 长度偏置
- [ ] **人工标注会话**（约 210 例 × ~9 秒 ≈ 30 分钟）→ 本文档 §2 数字留空待填
- [ ] 灰度观察期计数启动（依赖仓库推送 GitHub 后）

> 在 §2 出现实测数字前，本项目所有 llm_judge 相关门禁停留在 observe——这是设计，不是未完成。
