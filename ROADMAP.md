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

## 1. 当前状态快照（MVP，commit `c79b968`，2026-08-25）

19 个离线确定性测试全绿；`uv run lens demo` EXIT=0（两版本对比评测 → pass 分布 → HTML 报告 → observe/block 双模式门禁）。

| 模块 | 内容 | 状态 |
|---|---|---|
| `provider.py` | mock-first LLM 接入（MockProvider 默认 / OpenAI-compatible 免费节点可选） | ✅ |
| `runner.py` | dataset × n-trials 并发矩阵；轨迹先落盘再评分 | ✅ |
| `store.py` | ★ 内容寻址轨迹库（两级 sha256）：同内容去重、judge 可重放重判分 | ✅ |
| `scorers.py` | exact_match（数值容差）/ key_state（BFCL V3 state-based mini）/ llm_judge | ✅ |
| `metrics.py` | pass@k 无偏估计器（Codex）/ pass^k（tau-bench 悲观界）/ bootstrap CI | ✅ |
| `regression.py` | 版本 case 级 diff + GatePolicy{observe, block} | ✅ |
| `judge_lab.py` | Cohen's κ · position-swap · 长度偏置（judge 校准闭环骨架） | ✅ |
| `report.py` | 自包含 HTML 报告（内联 CSS 零外链） | ✅ |
| `cli.py` | lens run / report / gate / demo | ✅ |

**Mock 边界诚实清单**：
- MockProvider 是默认 provider：demo 数字全部来自脚本化假模型；
- llm_judge 的判定词表是规则 mock，尚未接真实模型；
- judge_lab 的 κ 计算逻辑已就绪，但标注集规模小，尚无可引用的 κ 体检数字；
- GitHub Action 接入未开始。

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

| Phase | 名称 | 依赖 | 现金支出 | 核心产出 |
|---|---|---|---|---|
| A | 真实通路加固 | 无 | ¥0 | 真 provider 冒烟 + token 成本记账 + 超时重试 |
| B | GitHub Action 门禁 | A 可并行 | ¥0（免费额度） | PR 上 observe/block 两态演示 |
| C | judge 校准闭环扩容 | A | ¥0 | ≥200 例标注集 + κ/偏置体检数字 + 切 block 决策规则 |
| D | TRAIL 自检 runner + trace_analyzer | C | ¥0 | 附录位交付物 |
| E | flywheel 导出 + v1.0 封版 | B, C | ¥0 | 轨迹导出对接 AgentRL-Lab + tag v1.0 |
| F | 可选深化 | E | 视情况 | OTel 兼容端点 / 对抗鲁棒性套件 |

## 4. 各 Phase 详情

### Phase A：真实通路加固（纯本地，立即开工）
**目标**：免费端点从「可选」变成「可靠可选」，且离线路径零影响。
- [ ] provider 真实分支联测脚本：Nemotron 端点连通性 / 限流退避 / 超时重试参数化
- [ ] token 成本记账：每次调用的 prompt/completion token 入轨迹记录，报告页汇总成本列
- [ ] runner 失败重试策略显式化（网络错 vs 任务失败分开计数）
- [ ] demo 增加 `--provider real` 旗标（无 key 自动回落 mock 并提示）
**DoD**：pytest 全绿；默认路径行为不变；有 key 时冒烟脚本对 3 个 case 完成一次真模型评测并出报告。

### Phase B：GitHub Action 门禁
**目标**：「这个改动能不能合入」长进 CI。
- [ ] `.github/workflows/lens-gate.yml`：push/PR 触发，跑 `lens gate --policy observe`
- [ ] PR 评论机器人：贴版本 diff 表（case 级 pass 变化 + 双侧分布夹逼指标）；block 模式超阈值时 exit 非 0
- [ ] 报告 artifact 上传；门禁阈值规则文档化（什么算显著退化：pass@k 降 ×pp 且 CI 不重叠等）
- [ ] 在本仓库自身或示例仓库上演示一次完整 PR 流程（截图入 docs/）
**DoD**：陌生 PR 触发评测并在 PR 里看到 diff 表；人为注入一个退化版本能被 block 模式拦截。

### Phase C：judge 校准闭环扩容（本项目最重的差异化证据）
**目标**：把「中等一致的 judge 凭什么拦工程师」用数字回答。
- [ ] 标注集扩容到 ≥200 例：agent 先做预标注 + 分层抽样生成复核队列，人只做批量裁决（工具化，最小人力）
- [ ] judge_lab 出体检报告：κ、position-swap 翻转率、长度偏置回归系数，附置信区间
- [ ] 成文《切 block 的前提条件》：κ 阈值、误杀率上限、灰度观察期建议——写进 README 或 docs/
- [ ] judge 模型可替换实验：换另一个免费节点模型重判分（store 重放能力第一次实战）
**DoD**：一份 judge 校准报告页；决策规则文档；换 judge 重判分演示记录。

### Phase D：TRAIL 自检 runner + trace_analyzer（附录位）
- [ ] TRAIL 式自检 runner：评测系统自身的元评测（scorer 对已知好/坏轨迹的分辨力）
- [ ] trace_analyzer 子系统：失败轨迹的模式聚类（工具错 / 规划错 / 格式错）
**DoD**：两者各有一个可运行入口与测试。

### Phase E：flywheel 导出 + 封版
- [ ] Harbor 式 rollout 导出格式（对接 AgentRL-Lab 的 SFT 冷启动回流）：导出 schema 对齐其 `rollout/schema.py`
- [ ] 端到端演示：AgentLens 挑出高分轨迹 → 导出 JSONL → AgentRL-Lab 侧可加载
- [ ] README 数字区换成实测值；`git tag v1.0`
**DoD**：跨仓飞轮最小闭环演示成功（哪怕只有几条轨迹）。

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
4. **Phase E 跨仓对齐**：AgentRL-Lab 仓库当前不可达，导出 schema 以 Harbor 式 rollout 字段落地方案 +
   映射说明文档交付，字段级对齐标注 pending。

## 7. 新会话上手清单

1. `cd agent-lens && uv sync && uv run pytest -q && uv run lens demo` —— 确认基线全绿（19 tests / EXIT=0）；
2. 通读 `AGENTS.md` 与本文 §1–§2；
3. `git log --oneline` + 本文勾选状态 ⇒ 定位当前 Phase；
4. 从第一个未完成 Phase 开工；涉及真实 API key 时向用户确认环境变量已配置；
5. 收工：pytest 绿 → 更新本文勾选 → commit。
