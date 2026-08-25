# ROADMAP —— AgentLens：MVP → v1.0 阶段性开发路径

> 本文档写给**零上下文接手本仓库的开发者（人类或 agent）**。
> 阅读顺序：`README.md`（定位与架构）→ `AGENTS.md`（工程规约与不变量）→ 本文（做什么、按什么顺序、怎么算完成）。
> 工作方式：按 Phase 顺序推进；每个 Phase 的 DoD 全部满足才算关闭；任务逐项勾选，勾选状态随代码一起提交。

## 0. 一句话与完成态定义

**一句话**：Agent 回归评测门禁与稳定性度量平台——回答「这个改动能不能合入」。不是又一个 Langfuse：Langfuse 回答「发生了什么」，AgentLens 回答「能不能合入」。

**v1.0 完成态（以下全部满足即封版打 tag）**：
1. GitHub Action 门禁在真实 PR 流程中演示 observe / block 两态（示例仓库即可），PR 评论输出版本 diff 表；
2. judge 校准数字落地：≥200 例人工标注集 → Cohen's κ、position-swap 差、长度偏置曲线；并产出成文的决策规则——「κ 与误杀率达到多少才允许从 observe 切 block」；
3. 真实模型通路各一条：被测 agent 与 LLM judge 均跑通免费端点（Nemotron）；
4. 与 AgentRL-Lab 打通一次高质量轨迹导出（eval→RL flywheel 最小演示）；
5. 全流程一键复现 + 自包含 HTML 报告。

## 1. 当前状态快照（2026-08-25 执行轮收工，HEAD 见 git log）

**45 个离线确定性测试全绿；`uv run lens demo` EXIT=0；ruff 全过。**
Phase A/D 完成，B/C/E 工具链就绪——剩余未勾项均需外部条件（GitHub remote、
AGENTLENS_API_KEY、人工标注会话、AgentRL-Lab 仓库可达），见 §4 各 Phase 标注。

| 模块 | 内容 | 状态 |
|---|---|---|
| `provider.py` | mock-first 接入 + ChatResult token 记账 + 参数化重试退避（transport 可注入） | ✅ |
| `runner.py` | dataset × n-trials 并发矩阵；失败分类隔离（RunSummary）；轨迹先落盘再评分 | ✅ |
| `store.py` | ★ 内容寻址轨迹库（两级 sha256）：同内容去重、judge 可重放重判分 | ✅ |
| `scorers.py` | exact_match（数值容差）/ key_state（BFCL V3 state-based mini）/ llm_judge | ✅ |
| `metrics.py` | pass@k 无偏估计器（Codex）/ pass^k（tau-bench 悲观界）/ bootstrap CI | ✅ |
| `regression.py` | 版本 case 级 diff + GatePolicy{observe, block} + gate JSON 输出 | ✅ |
| `judge_lab.py` | Cohen's κ · position-swap · 长度偏置（judge 校准闭环骨架） | ✅ |
| `calibration.py` | ★ 校准闭环：210 例构造池 / 分层抽样 / 复核 HTML / κ 报告 / judge 人格注册表 | ✅ 新增 |
| `meta_eval.py` | TRAIL 式 scorer 自检元评测（分辨力满分才上岗） | ✅ 新增 |
| `trace_analyzer.py` | 失败轨迹模式聚类（工具/格式/规划/空输出） | ✅ 新增 |
| `export.py` | Harbor 式 rollout 导出（溯源哈希 + 回读校验） | ✅ 新增 |
| `ci.py` | gate JSON → GitHub PR 评论渲染与幂等 upsert | ✅ 新增 |
| `report.py` | 自包含 HTML 报告（内联 CSS 零外链 + 成本记账区） | ✅ |
| `cli.py` | demo/run/report/gate/smoke/calibrate/kappa-report/rescore/meta-eval/analyze/export | ✅ |

**Mock 边界诚实清单**：
- MockProvider 是默认 provider：demo 数字全部来自脚本化假模型；
- llm_judge 的判定词表是规则 mock，尚未接真实模型；
- κ 工具链就绪但**无人工标注数字**——切 block 前提文档中的阈值尚未被实测填充；
- GitHub Action workflow 已入库，但真实 PR 流程演示待仓库推送。

**不许退化的招牌不变量**：
- Store trajectory first, judge later：轨迹必须先内容寻址落盘，评分是对历史轨迹的重放——换 judge 重判分是一等公民操作（见 `test_store_runner.py`）；
- 双侧分布夹逼：pass@k（乐观界）与 pass^k（悲观界）必须一起报（见 `test_metrics_regression.py`）；
- 门禁分级：observe 只报告不阻断，切 block 必须以校准数字为前提条件。

## 2. 硬约束（不可协商）

1. **免费资源红线**：禁止一切付费 API。真实模型只走 NVIDIA 托管 Nemotron 免费端点（OpenAI-compatible：base_url=`https://integrate.api.nvidia.com/v1`，key 从环境变量读取，严禁写死或入库）。GitHub Actions 用免费额度。
2. **mock-first**：每个外部依赖必须有确定性测试替身；任何改动后 `uv run pytest -q` 离线全绿、`uv run lens demo` EXIT=0。
3. **人工标注的边界**：κ 标注需要人来定标准答案/偏好——agent 负责把预标注、复核界面、一致性计算全部自动化，把人的工作量压到最小；不得用 LLM 生成「伪人工」标注冒充。
4. **工程规格**：Python ≥3.11；src 布局；uv 管理（uv.lock 入库）；ruff `line-length=100`、`select=["E","F","I","W"]`；CLI 用 Typer（保留 `@app.callback()`）+ rich（注意 markup 吞方括号陷阱，详见 AGENTS.md）。新增依赖需在 commit message 里说明理由。
5. **提交纪律**：`git -c user.name="EthyleneC2H4" -c user.email="ethylene@users.noreply.github.com"`；每 Phase 至少一个 commit；收工时更新本文勾选一并提交。

## 3. 阶段总览

| Phase | 名称 | 依赖 | 现金支出 | 状态 |
|---|---|---|---|---|
| A | 真实通路加固 | 无 | ¥0 | ✅ 完成（ac7f265） |
| B | GitHub Action 门禁 | A 可并行 | ¥0（免费额度） | 🟡 工具链就绪，真实 PR 演示待 remote |
| C | judge 校准闭环扩容 | A | ¥0 | 🟡 工具链完备，κ 待人工标注会话 |
| D | TRAIL 自检 runner + trace_analyzer | C | ¥0 | ✅ 完成（12413ca） |
| E | flywheel 导出 + v1.0 封版 | B, C | ¥0 | 🟡 出口就绪，封版待 B/C 收尾 |
| F | 可选深化 | E | 视情况 | 未开始 |

## 4. 各 Phase 详情

### Phase A：真实通路加固（纯本地，立即开工）——✅ 完成（commit ac7f265）
**目标**：免费端点从「可选」变成「可靠可选」，且离线路径零影响。
- [x] provider 真实分支联测：transport 可注入离线测试（429/5xx/超时分类、Retry-After 优先、指数退避参数化）
- [x] token 成本记账：`ChatResult` 结构化返回（prompt/completion tokens/model/latency）→ Trajectory 字段 → 报告成本区
- [x] runner 失败策略显式化：`RunSummary` 分开计数网络错/任务错；单 job 失败隔离不炸矩阵；顺修 `lens run` 的 `p_success` 潜在 bug
- [x] demo/run 增加 `--provider real` 旗标（缺 key 自动回落 mock 并提示）；新增 `lens smoke` 冒烟命令
**DoD**：pytest 全绿（30→38）；默认路径行为不变；冒烟命令就绪——**真实端点实测待配置 AGENTLENS_API_KEY**。

### Phase B：GitHub Action 门禁——🟡 工具链就绪（c54ede4），真实 PR 演示待仓库推送
**目标**：「这个改动能不能合入」长进 CI。
- [x] `.github/workflows/lens-gate.yml`：push/PR 触发，pytest → 双版本评测 → `gate --out-json` → artifact 上传 → PR 评论（幂等更新）
- [x] PR 评论机器人：`lens.ci.render_comment` + 幂等 upsert（零依赖 urllib，transport 注入可离线测试）；block 超阈值 exit 非 0
- [x] 门禁阈值规则文档化：`docs/gate-policy.md`（case 计数主规则 + CI 非重叠噪声甄别 + observe→block 前提链接）
- [x] `--solver-spec` 动态加载被评 solver（CI 接入任意 Python 仓库的评测对象）
- [x] 本地全流程模拟：注入退化版本（0.8→0.2）被 block 拦截 + 评论渲染契约测试
- [ ] **陌生 PR 触发评测的实录演示（含截图入 docs/）**——本仓库当前无 remote，推送后一次补齐
**DoD**：本地模拟等价测试通过；真实 PR 流程项保持未勾。

### Phase C：judge 校准闭环扩容（本项目最重的差异化证据）——🟡 工具链完备（f40c5e7），κ 数字待人工标注
**目标**：把「中等一致的 judge 凭什么拦工程师」用数字回答。
- [x] 校准池 210 例构造式已知答案（8 类受控错误）+ 成对 24 组；预标注分层抽样（分歧项全保留）；自包含复核 HTML（建议不预选防锚定）
- [x] judge_lab 体检报告：`lens kappa-report` 出 κ+bootstrap CI、误杀/漏杀率、长度偏置、position-swap 一致率（手算用例验证）
- [x] 《切 block 的前提条件》：`docs/judge-block-policy.md` 七项硬性前提（κ≥0.6 / 误杀≤2% / swap≥95% / 灰度 ≥20 PR 等）与回退触发器
- [x] 换 judge 重判分实战 store 重放：`lens rescore`（确定性 mock judge 人格离线演示；真实模型换判待 key）
- [ ] **人工批量裁决会话**（~210 例 × ~9s ≈ 30 分钟）→ κ 实测数字回填决策文档
**DoD**：报告页/决策规则文档/换 judge 重判分演示均落地；「≥200 例人工标注集」的**人**这一环按硬约束 3 不可由 agent 代劳，如实保持未勾。

### Phase D：TRAIL 自检 runner + trace_analyzer（附录位）——✅ 完成（12413ca）
- [x] TRAIL 式自检 runner：`meta_eval.py` 元评测（exact_match/key_state/llm_judge 对已知好/坏轨迹分辨力满分才上岗）+ `lens meta-eval`
- [x] trace_analyzer：`trace_analyzer.py` 失败模式聚类（tool_error/format_error/planning_error/empty/unknown）+ `lens analyze`
**DoD**：两者各有可运行入口与测试（含「坏 scorer 必须被抓」的元自检）。

### Phase E：flywheel 导出 + 封版——🟡 出口就绪（154f488），v1.0 封版待 B/C 收尾
- [x] Harbor 式 rollout 导出：`export.py` 任务级稳定率筛选 + content_hash 溯源 + 回读校验；schema 文档 `docs/export-schema.md`
- [x] 端到端最小演示：run → export → 下游加载校验闭环（`tests/test_export.py`）；AgentRL-Lab 字段级对齐 pending（仓库不可达）
- [x] README 数字区换成实测状态表
- [ ] `git tag v1.0`——封版条件（B 真实 PR 演示 + C κ 数字）满足后再打
**DoD**：飞轮出口侧完成；跨仓闭环演示待 AgentRL-Lab 可达。

### Phase F：可选深化
OTel collector 兼容导出端点；对抗鲁棒性评测套件（InjecAgent 式注入用例 + utility/security 双指标，回应 agent 安全方向 JD 信号）；Web UI（非必需——CLI+HTML 报告已覆盖核心流）。

## 5. 明确不做（v1.0 scope 之外）

通用 trace dashboard / Langfuse 替代品；多租户 SaaS 化；自有评测数据集的大规模构建（200 例封顶）；非 OpenAI-compatible 协议适配。

## 6. 方案修订记录（2026-08-25 执行版调研结论）

零上下文接手后对仓库与环境的实测：基线 19 tests 全绿 / demo EXIT=0；**本仓库无 git remote、环境无
AGENTLENS_API_KEY**。据此对 §4 做如下修订（不改变 DoD 语义，只改变可执行路径）：

1. **Phase A 结构化记账**：token 记账的载体定为 `ChatResult`（text/prompt_tokens/completion_tokens/
   model/latency_ms）——provider 返回结构化结果而非裸字符串，否则 token 无法端到端入轨迹；
   重试退避通过**注入 transport 假件**离线测试（429/5xx/超时分类，尊重 Retry-After）；
   runner 改为**单 job 失败隔离**：一个 case 崩溃不再炸掉整矩阵，失败计数进 RunSummary 显式上报。
2. **Phase B 无 remote 的现实**：「陌生 PR 演示」在本机无法发生——交付物改为：gate 支持 `--out-json`
   机器可读输出 + 零依赖 PR 评论渲染脚本（含同 PR 评论去重更新）+ workflow YAML + 本地全流程模拟
   （注入退化版本被 block 拦截的实录）；真实 PR 截图演示项保留未勾，待仓库推送后一次补齐。
3. **Phase C 标注池来源**：校准池采用**构造式已知答案**任务（数值近似错/格式等价/单位混淆/截断等受控
   错误类型），真值由构造保证——这不违反硬约束 3（不是 LLM 伪冒人工标注，人工仍做批量裁决确认）；
   κ 数字在人工标注会话前保持 pending，工具链与决策规则文档先行落地。换 judge 重判分用确定性
   mock judge 人格（strict/lenient）离线实战 store 重放。
4. **Phase E 跨仓对齐**：~~AgentRL-Lab 仓库当前不可达~~ **2026-08-26 更正**：`../agentrl-lab/src/agentrl/
   rollout/schema.py` 实际就在工作区隔壁（Trajectory{env_name, seed, transitions[{obs,action,reward,
   done,tokens}], total_reward, ...}），字段级适配器 + 回读验证可立即执行，flywheel 闭环从 pending 转为可做。

## 7. 新会话上手清单

1. `cd agent-lens && uv sync && uv run pytest -q && uv run lens demo` —— 确认基线全绿（45 tests / EXIT=0）；
2. 通读 `AGENTS.md` 与本文 §1–§2；
3. `git log --oneline` + 本文勾选状态 ⇒ 定位当前 Phase；
4. 从第一个未完成 Phase 开工；涉及真实 API key 时向用户确认环境变量已配置；
5. 收工：pytest 绿 → 更新本文勾选 → commit。

## 8. 现状总结与待改进清单（2026-08-26 深化轮收工盘点）

### 8.1 完成情况一览

| Phase | 状态 | 缺的外部条件 |
|---|---|---|
| A 真实通路加固 | ✅ 完成 | 仅缺 key 做一次真模型冒烟实测 |
| B GitHub Action 门禁 | 🟡 工具链就绪 | git remote（推送后补真实 PR 演示截图） |
| C judge 校准闭环 | 🟡 工具链完备 | **人工标注会话**（~30 分钟，硬约束 3 不许 agent 代劳） |
| D TRAIL 自检 + trace_analyzer | ✅ 完成 | — |
| E flywheel 导出 | 🟡 出口就绪 | AgentRL-Lab 字段级对齐 → **现已解锁**（见 §6.4） |
| v1.0 tag | ⏸ 未打 | B/C 收尾后 |

### 8.2 代码缺陷与待改进点（按优先级）

1. **复核页进度不持久化**：`render_review_html` 生成的标注页刷新即丢已勾选项——
   加 localStorage 自动保存/恢复（低成本高价值，直接服务人工标注会话）。
2. **report/ci HTML 注入风险**：`render_report`/`render_comment` 用 f-string 直拼 task_id/output，
   含 `<` `|` 等字符会破坏排版甚至注入——加 `html.escape` 与 markdown 表格转义。
3. **gate 基线自动选择脆弱**：无显式 `--base-run` 时按 run_id 字母序取 first/last，
   多轮评测后可能选错基线；且 CI 每 PR 双跑 mock 无基线缓存（跨 PR 复用 main store artifact 未实现）。
4. **store 索引无 compaction**：index.jsonl append-only 无限增长；`_latest_run` 全量扫描 O(n)，
   大 store 下 report/gate 变慢——加 run 级别索引或分段 compaction。
5. **judge 成本未进报告**：LLMJudgeScorer.usage_totals 只活在 scorer 实例里，
   `_group_results` 重放评分后未汇入 render_report 的成本区。
6. **rescore / kappa-report 输出不落盘**：仅 console 表格，不可追溯不可引用——
   补 JSONL/HTML 产物路径参数。
7. **position-swap 是规则包装不是真 pairwise judge**：make_pair_judge 用规则 judge 包装修剪，
   真实场景需要 LLM pairwise prompt（A/B 两候选一问一答），待接 provider 后补真通路。
8. **export reward 只有 0/1**：无部分分数通道（如 key_state 部分命中比例）；schema 文档已预留说法。
9. **CI 平台矩阵单一**：仅在 macOS 本机验证过，workflow 的 ubuntu 路径未真实跑过一次（随 remote 解锁）。
10. **calibrate 复核页无键盘快捷键/自动跳焦**：210 例裁决的人机效率还能再压（nice-to-have）。

> 快赢排序：#1、#2 半天内可完成且收益直接；#3–#6 属于「第一次真实使用就会撞上」的债，
> 建议 flywheel 对齐（§6.4）之后立刻做。
