# ROADMAP §8.2 技术债批次实施计划（P5 深化轮）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 清偿 ROADMAP §8.2 中可本地执行的缺陷 #3/#4/#5/#6/#7/#8/#10（#9 卡 git remote，不动），全离线 TDD，收工时 ROADMAP/README/CLAUDE.md 状态同步。

**Architecture:** 保持「store trajectory first, judge later」不变量；store 增加 run 级二级索引（runs/*.json + runs_manifest.json，锁内 RMW，旧库懒重建）；CLI 基线自动选择改为插入序并显式回显；judge 用量从 scorer 实例穿透到报告成本区；rescore/kappa-report 产物落盘；MockProvider 增加 pairwise 分支支撑真 LLM pair judge 的离线确定性测试；导出增加 reward_detail 部分分数通道（顺修 runner 丢 required_states 的隐性缺陷）；复核页键盘快捷键。

**Tech Stack:** Python ≥3.11 / pydantic v2 / Typer+rich / pytest / ruff(E,F,I,W) line-length=100。

**Spec:** ROADMAP.md §8.2（缺陷清单）+ §2（硬约束）+ docs/export-schema.md。

## Global Constraints

- 禁止付费 API；真实模型只走 NVIDIA 免费端点，key 仅环境变量 `AGENTLENS_API_KEY`
- mock-first：任何改动后 `uv run pytest -q` 全绿、`uv run lens demo` EXIT=0、`uv run ruff check .` 干净
- Scorer 只吃 Trajectory + task dict；判定与执行解耦不可破坏
- 中文简短 docstring；类型注解；ruff line-length=100
- 提交身份按 ROADMAP §2.5：`git -c user.name="EthyleneC2H4" -c user.email="ethylene@users.noreply.github.com"`
- 收工更新 ROADMAP 勾选一并提交；新增依赖需在 commit message 说明理由（本批次零新依赖）

---

### Task 1: store run 级二级索引与懒迁移（§8.2-#4）

**Files:**
- Modify: `src/lens/store.py`
- Test: `tests/test_store_runner.py`

**Interfaces:**
- Produces: `RunInfo(run_id: str, n_trajectories: int, seq: int)` dataclass；
  `ContentAddressedStore.list_runs() -> list[RunInfo]`（按首次出现序）；
  `list_by_run()` 改走 run 文件（签名/语义不变：返回该 run 全部轨迹，插入序）。
- 内部：`runs_manifest.json`（run_id → {seq,n}）、`runs/<sha16>.json`（run_id+hashes）、
  `threading.Lock` 保护 put 路径 RMW；manifest 缺失时扫 index 一次重建（含 run 文件回填）。

- [x] Step 1 写失败测试：legacy 手工库（只有 blocks/+index.jsonl）打开后 `list_by_run` 正确且 manifest 自动重建；插入序≠字母序的三个 run `list_runs()` seq 单调；Runner 并发（n_workers=8, 12题×2trials）后 `list_runs()[0].n_trajectories == 24` 且与 index 行数一致。
- [x] Step 2 运行确认 FAIL（AttributeError: list_runs）
- [x] Step 3 实现 store.py（锁 + manifest + run 文件 + `_hashes_for` 自愈路径；文件名用 sha256(rid)[:16] 防路径注入）
- [x] Step 4 测试通过 + 全量 pytest 绿
- [x] Step 5 commit `feat(p5-store): run 级二级索引……`

### Task 2: gate 基线选择加固 + `lens runs`（§8.2-#3）

**Files:**
- Modify: `src/lens/cli.py`（删 `_latest_run`/`_all_run_ids` 直扫 index 的旁路，改走 `store.list_runs()`；gate 自动选择改为首/末创建序并打印所选 id；out_json 增加 `baseline_auto`；新增 `runs` 子命令表格：seq/run_id/n_trajectories）
- Test: `tests/test_report_ci.py`

**Interfaces:**
- Consumes: `store.list_runs()`
- Produces: gate 默认 base=list_runs()[0]、cand=[-1]（创建序，不再字母序）；`lens runs` 列表命令。

- [x] Step 1 失败测试：以 z-first/a-second/m-third 三个版本（字母序≠插入序）跑三次 `lens run` 后调 `lens gate --mode observe --out-json`，断言 payload `base_run=="z-first-seed0"` 且 `cand_run=="m-third-seed0"` 且 `baseline_auto is True`；显式 `--base-run/--cand-run` 时 `baseline_auto is False`。
- [x] Step 2 FAIL → Step 3 实现 → Step 4 PASS + `lens runs` 冒烟 → Step 5 commit `feat(p5-gate): 基线自动选择改创建序……`

### Task 3: judge 用量入报告成本区（§8.2-#5）

**Files:**
- Modify: `src/lens/cli.py`（`_group_results(trajs, scorer=None)` 返回三元组 `(grouped, order, judge_usage|None)`，LLMJudgeScorer 时取 `usage_totals` 拷贝；demo/smoke/report 三处解包更新；`report` 命令加 `--scorer exact|llm_judge`）
- Modify: `src/lens/report.py`（`render_report` 加 `judge_totals: dict|None` 参数 → 成本表追加 `llm_judge` 行；空 results 抛 `ValueError("结果为空…")` 防 max() 崩溃）
- Test: `tests/test_report_ci.py`

- [x] Step 1 失败测试：render_report 带 judge_totals 渲染出 `llm_judge` 行与 token 数；空 results 抛 ValueError；`report --scorer llm_judge` 子进程端到端产出含 llm_judge 成本区的报告。
- [x] Step 2 FAIL → Step 3 实现 → Step 4 PASS → Step 5 commit `feat(p5-report): judge 用量入成本区……`

### Task 4: rescore / kappa-report 产物落盘（§8.2-#6）

**Files:**
- Modify: `src/lens/cli.py`（两命令各加 `--out-json`；rescore 输出 {run_id,n,judges[],agreement,kappa,flips[]}；kappa-report 输出最终 report dict 含 swap_consistency）
- Test: `tests/test_report_ci.py`（子进程模式，沿用现有 gate-json 测试范式）

- [x] Step 1 失败测试：临时 store 上 `lens rescore --out-json` 产出合法 JSON 且 κ 字段在；mini pool/labels 文件经 `lens kappa-report --out-json` 回读断言 `human_vs_truth/judge_vs_human/length_bias` 键齐。
- [x] Step 2 FAIL → Step 3 实现 → Step 4 PASS → Step 5 commit `feat(p5-cli): rescore/kappa-report 产物落盘……`

### Task 5: 真 pairwise LLM judge + MockProvider pairwise 分支（§8.2-#7）

**Files:**
- Modify: `src/lens/provider.py`（MockProvider.chat 增加 pairwise 分支：prompt 同时含 `参考答案:`、`候选 A:`、`候选 B:` 行时做数值比较返回 A/B/tie——本地小数解析，不 import calibration 防环）
- Modify: `src/lens/calibration.py`（`make_llm_pair_judge(provider, rubric=...)`：单 prompt 双候选，解析 A/B/tie，未解析视为 tie）
- Modify: `src/lens/cli.py`（`kappa-report --pair-mode wrap|llm` 默认 wrap；llm 走 `select_provider("real")` 缺 key 自动回落 mock 并提示）
- Test: `tests/test_provider_real.py`、`tests/test_calibration.py`

**Interfaces:**
- Produces: `make_llm_pair_judge(provider) -> judge_pair(task_input, gold, first, second) -> "A"|"B"|"tie"`，可直接喂 `swap_consistency`。

- [x] Step 1 失败测试：FakeProvider 按位置返回 A/B → swap_consistency 检出顺序翻转；MockProvider 数值分支（A 好→"A"，交换后→"B"，同对同错→"tie"）；`kappa-report --pair-mode llm --pairs …` 离线端到端出 swap_consistency。
- [x] Step 2 FAIL → Step 3 实现 → Step 4 PASS → Step 5 commit `feat(p5-calib): 真 pairwise judge 通路……`

### Task 6: 导出部分奖励通道 + runner 补 required_states（§8.2-#8）

**Files:**
- Modify: `src/lens/runner.py`（metadata 增加 `"required_states": task.required_states`——修复状态断言事后不可重放的隐性丢失）
- Modify: `src/lens/export.py`（`build_rollout(..., reward_detail=None)` 可选字段；`export_rollouts` 对有 required_states 的轨迹计算 `reward_detail={"key_state_fraction": frac}`）
- Modify: `docs/export-schema.md`（字段表补可选 reward_detail）
- Test: `tests/test_export.py`、`tests/test_store_runner.py`

- [x] Step 1 失败测试：带 required_states 的任务轨迹落盘后 metadata 含之；导出产物对部分命中任务给 `reward_detail.key_state_fraction==0.5`；纯 exact 任务无该键；AgentRL 转换不受额外字段影响。
- [x] Step 2 FAIL → Step 3 实现 → Step 4 PASS → Step 5 commit `feat(p5-export): reward_detail 部分分数通道……`

### Task 7: 复核页键盘快捷键与焦点管理（§8.2-#10）

**Files:**
- Modify: `src/lens/calibration.py`（`_REVIEW_TMPL` JS：currentIdx 指针、`j/↓` 下一题 `k/↑` 上一题、`1/0` 判正确/错误并自动跳下一题、`scrollIntoView` 居中、当前位置指示；注意模板 `.format()` 所有字面花括号须 `{{}}` 转义；程序化设 checked 后手动调 persistProgress/updateDone）
- Test: `tests/test_calibration.py`（标记字符串断言：keydown/scrollIntoView/位置指示）

- [x] Step 1 失败测试（标记断言）→ Step 2 FAIL → Step 3 实现 → Step 4 PASS → Step 5 commit `feat(p5-review): 复核页键盘快捷键……`

### Task 8: 收工——文档同步与全量验证

**Files:**
- Modify: `ROADMAP.md`（§8.2 各项标 ✅ 与日期；快赢排序注记更新；§1 快照测试数）
- Modify: `README.md`（架构行 store/cli 描述微调 + 实测状态表测试数）
- Modify: `CLAUDE.md`（当前状态数字）

- [x] Step 1 `uv run pytest -q` 全绿（预期 45+~14 新增）
- [x] Step 2 `uv run ruff check .` 干净
- [x] Step 3 `uv run lens demo` EXIT=0；`lens meta-eval` 合格；本地 gate 模拟链路冒烟
- [x] Step 4 文档同步 + commit `docs(p5-收尾): ……`

## Self-Review

- 覆盖检查：§8.2 十项中 #1/#2 已完成、#9 外部阻塞（如实保留未勾），其余 #3–#8、#10 各有对应 Task ✓
- 类型一致性：`list_runs()->list[RunInfo]` 在 Task 2 消费；`make_llm_pair_judge` 返回签名与 `swap_consistency` 形参约定一致 ✓
- 占位符扫描：无 TBD/TODO ✓
