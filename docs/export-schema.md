# Rollout 导出 Schema（eval→RL flywheel 出口）

> AgentLens 侧的轨迹出口格式：把评测中「稳定答对」的高质量轨迹回流给
> AgentRL-Lab 做 SFT 冷启动。字段设计遵循 Harbor 式 rollout 惯例。

## 记录结构（JSONL，一行一条）

```json
{
  "id": "case-01-add#t0",
  "task":   { "id": "case-01-add", "input": "计算 128+256 并只输出数字", "gold": "384" },
  "trajectory": { "output": "384", "steps": ["step1 ...", "step2 ..."] },
  "reward": 1.0,
  "source": {
    "platform": "agent-lens",
    "run_id": "v0.2-cand-seed0",
    "version": "v0.2-cand",
    "content_hash": "<sha256>",
    "model": "nvidia/llama-3.3-nemotron-super-49b-v1"
  }
}
```

| 字段 | 必需 | 说明 |
|---|---|---|
| `id` | ✅ | `{task_id}#t{trial}`，同 run 内唯一 |
| `task` | ✅ | 任务输入与参考答案（gold 可为空串） |
| `trajectory` | ✅ | output + steps（过程状态序列） |
| `reward` | ✅ | 当前口径：重放评分 0/1；未来可挂连续奖励 |
| `source.content_hash` | ✅ | 内容寻址溯源——可回 AgentLens store 验证轨迹未篡改 |
| `reward_detail` | ➖ 可选 | `{"key_state_fraction": x}`——required_states 命中比例（部分分数通道）；无状态断言的任务省略该键 |

## 筛选规则（质量优先）

- 任务级通过率 ≥ `--min-rate`（默认 0.75）才导出该任务全部 trial；
- 侥幸型任务（pass@1 中等、pass^k 低）整体剔除——SFT 冷启动不要「偶尔对」的示范。

## 与 AgentRL-Lab 的对接

- 加载校验入口已内置：`lens.export.load_jsonl_rollouts`（必需字段缺失即抛错），
  下游可直接复用或按其 `rollout/schema.py` 写适配器；
- **pending**：AgentRL-Lab 仓库可达后做一次字段级映射核对
  （预期差异点：`task.input` vs 其 prompt/messages 结构、reward 是否要求分段）；
- 最小闭环演示：`lens run → lens export → load_jsonl_rollouts 回读`
  （测试见 `tests/test_export.py::test_rollout_schema_roundtrip`）。

## OTel collector 兼容出口（OTLP/JSON traces）

`lens export --fmt otlp` 把一个 run 的全部轨迹渲染为 OTLP/JSON
（`resourceSpans → scopeSpans → spans`），可被 OpenTelemetry Collector 的
`/v1/traces` HTTP 端点直收，也可落盘后用任意 OTLP 解析工具消费：

```bash
uv run lens export --store-dir .lensstore --fmt otlp --out traces.otlp.json
uv run lens export --store-dir .lensstore --fmt otlp \
    --otel-push http://127.0.0.1:4318/v1/traces   # 推送失败 exit 1
```

映射规则与诚实边界：

| 项 | 值 |
|---|---|
| 粒度 | 一条轨迹 = 一个 span（`SPAN_KIND_INTERNAL`） |
| traceId/spanId | `sha256("{run_id}:{task_id}:{trial}")` 前/中段截取——确定性可复现 |
| span name | task_id |
| startTimeUnixNano | **占位值**：trial 序号 × 1e9——轨迹库不存墙钟时间，只保证单调稳定，不是真实时刻 |
| endTimeUnixNano | start + max(1, tokens) 毫秒级推导（同样非墙钟） |
| attributes | `lens.task_id / run_id / version / trial(int) / pass(bool) / gold / tokens(int) / model` |
| status | 重放评分通过 → `STATUS_CODE_OK`；否则 `ERROR` + message「scorer 判定失败」 |
| resource | `service.name=agent-lens`；scope 名 `lens.export` |

- 判定走重放评分（judge later 不变量），与 rollout 出口同一口径；
- intValue 按 OTLP JSON 规范编码为字符串；
- 推送显式带 User-Agent（网关 bot 防护教训），网络失败不抛出、返回原因由 CLI 决定退出码。
