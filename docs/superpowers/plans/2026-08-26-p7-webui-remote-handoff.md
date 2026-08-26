# P7 收官批次实现计划：Web UI / remote 引导 / 标注交接 / 封版同步

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 清完 ROADMAP 上全部 agent 可做项——OTel 导出（已完成 d81596f）、只读 Web UI、git remote 引导脚本、人工标注会话交接、文档同步与封版判定。

**Architecture:** Web UI 走 stdlib `http.server`（零新依赖），渲染逻辑全部是纯函数可离线测试；server 只是路由胶水。remote 演示卡在仓库创建这一步用户动作，交付 ready-to-run 引导脚本。标注交接按硬约束 3 只准备到「打开浏览器即可标」。

**Tech Stack:** Python ≥3.11 stdlib（http.server / urllib.parse）、pytest 离线确定性测试、Typer CLI。

**Spec:** ROADMAP §4 Phase B/C/F 未勾项；docs/gate-policy.md §2.3 噪声甄别语义。

## Global Constraints

- 免费资源红线：零新依赖、零付费服务；UI 绑定 `127.0.0.1`，GET-only。
- mock-first：`uv run pytest -q` 全绿 + `uv run lens demo` EXIT=0 才算完成。
- 提交身份：`git -c user.name="EthyleneC2H4" -c user.email="ethylene@users.noreply.github.com"`。
- 不得用 LLM 生成「伪人工」标注冒充 κ 数字。
- v1.0 tag 仅在 B 真实 PR 演示 + C κ 人工数字都落地后才打；本批次预计仍不满足，如实保持未打。

---

### Task 1: OTel 导出端点 ✅（commit d81596f）

- [x] `to_otlp_document` / `push_to_collector` + CLI `--fmt otlp --otel-push`
- [x] docs/export-schema.md OTLP 映射表
- [x] 9 个 export 测试全绿

### Task 2: 只读 Web UI（`lens ui`）

**Files:**
- Create: `src/lens/ui.py`
- Modify: `src/lens/cli.py`（新增 ui 子命令）
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `store.list_runs()/list_by_run()`；`diff_versions/evaluate_gate/GatePolicy/regression_summary`（regression.py）；`ExactMatchScorer`。
- Produces: `render_runs_page(runs) -> str`、`render_run_page(run_id, trajs) -> str`、`render_gate_page(base_id, cand_id, base_results, cand_results, mode) -> str`（均为纯函数）；`make_handler(store)` → Handler 类；`serve_ui(store, host, port)` → ThreadingHTTPServer。

- [ ] **Step 1: 写失败测试**（tests/test_ui.py）：runs 页转义注入串；run 详情页重放评分计数与输出转义；gate 页 block 拦截文案 + 🔥 高置信标记 + observe 放行；HTTP 路由烟测（127.0.0.1 随机端口 GET / 、/run/<id>、未知 run 404）。
- [ ] **Step 2: 跑红** `uv run pytest tests/test_ui.py -q` → ModuleNotFoundError。
- [ ] **Step 3: 实现 src/lens/ui.py**（纯函数渲染 + make_handler/serve_ui）+ cli `ui` 子命令。
- [ ] **Step 4: 全绿** pytest + ruff + demo EXIT=0。
- [ ] **Step 5: commit**

### Task 3: git remote 引导脚本（真实 PR 门禁演示的最后一公里）

**Files:**
- Create: `scripts/bootstrap-remote.sh`

内容：校验 gh 可用则 `gh repo create EthyleneC2H4/agent-lens --public/--private --source . --push`；否则打印手工路径（GitHub 网页建空仓 → `git remote add origin` → push main + `cand-regressed-demo` 分支）→ 打印 PR 创建 URL 与 workflow 触发说明。演示分支构造方式写入脚本注释（demo store 注入退化 cand 后 `--out-json` 留档）。

- [ ] **Step 1:** 写脚本 + `bash -n` 语法检查 + shellcheck 若可用。
- [ ] **Step 2:** commit（README 提及一行）。

### Task 4: 人工标注会话交接

- [ ] `uv run lens calibrate` 产出最新 calib/ 三件套；
- [ ] macOS `open calib/review.html` 直接把人送到复核界面；终端打印 j/k、1/0 快捷键说明与 ~30 分钟预估；
- [ ] labels.jsonl 就位后流程说明（κ 回填命令）写进交接输出。**agent 不代标**（硬约束 3）。

### Task 5: 收工同步与封版判定

- [ ] ROADMAP §1/§3/§4/§8 勾选与状态刷新（F 的 OTel/Web UI 勾掉；B/C 保持外部条件标注）；
- [ ] README 架构表补 ui.py 行、实测状态表刷新测试数；
- [ ] CLAUDE.md 测试数与子命令清单同步；
- [ ] 全量验证：pytest / ruff / demo / meta-eval / smoke（source ~/.agentlens-env）；
- [ ] 最终 commit；v1.0 tag 判定如实输出（预期：不打，B/C 未闭环）。
