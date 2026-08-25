# AGENTS.md — AgentLens

## 项目用途
Agent 回归评测门禁与稳定性度量平台 MVP：内容寻址轨迹库（store trajectory first, judge later）、
n-trials 多采样、pass@k/pass^k 双侧分布、bootstrap CI、版本 diff 与 observe/block 双模式门禁。

## 常用命令
```bash
uv sync
uv run pytest -q
uv run ruff check .
uv run lens demo   # 端到端：两版本评测 → 报告 → 门禁判定
```

## 目录导览
- `src/lens/store.py` 内容寻址轨迹库（两级 sha256）
- `src/lens/metrics.py` pass@k（Codex 无偏估计器）/pass^k/bootstrap CI —— 数学实现，改动必须过手算用例
- `src/lens/scorers.py` exact_match / key_state / llm_judge
- `src/lens/regression.py` 版本 diff + GatePolicy(observe/block)
- `src/lens/judge_lab.py` Cohen's κ、position-swap、长度偏置体检

## 代码约定
- Python ≥3.11；类型注解；中文简短 docstring；line-length 100
- Scorer 只吃 Trajectory + task dict —— 判定与执行解耦是硬约束
- 新增 scorer 必须可离线测试；LLM 依赖走 provider 抽象

## Provider 配置（mock-first）
默认 MockProvider（规则确定性输出）。真实模型走 OpenAICompatibleProvider：
- 免费节点示例 `https://integrate.api.nvidia.com/v1`（NVIDIA 免费托管端点），
  key 从环境变量 `AGENTLENS_API_KEY` 读取，缺失自动降级 mock
- **严禁硬编码付费服务端点或密钥**

## 指标层所需资源
demo 全程离线零成本。产出真实 κ 数字需人工标注集：
先用 30–50 例小规模标注集（judge 调用量 ≈ 数百次免费节点请求）。
