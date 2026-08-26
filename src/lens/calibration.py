"""Judge 校准闭环 —— 构造式已知答案池、预标注分层、复核 UI、κ 体检。

诚实边界：池中每条 item 的真值由**构造过程**保证（正确/受控损坏两种变体），
不是 LLM 伪冒的人工标注；κ 的最终数字必须以人工复核标签为准，
真值基线仅用于预标注分层与管线自检。人工只做批量裁决（HTML 复核表）。
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean


@dataclass
class CalibItem:
    """一条校准用例：任务输入 + 参考答案 + agent 输出 + 构造真值。"""

    id: str
    category: str
    task_input: str
    gold: str
    agent_output: str
    truth: bool          # 构造保证的真值：输出是否正确完成任务


@dataclass
class PairItem:
    """成对比较用例：better 明显优于 worse（gold 为构造真值参考答案）。"""

    id: str
    gold: str
    better: str
    worse: str
    task_input: str


# ---------------- 池构造（确定性，seed 固定可复现） ----------------


def build_pool(seed: int = 0) -> list[CalibItem]:
    """≥200 例构造式校准池：8 类受控错误形态，覆盖 FP 与 FN 两种风险。"""
    rng = random.Random(seed)
    items: list[CalibItem] = []

    def add(cat: str, inp: str, gold: str, out: str, truth: bool) -> None:
        items.append(
            CalibItem(
                id=f"{cat}-{len(items) + 1:04d}",
                category=cat,
                task_input=inp,
                gold=gold,
                agent_output=out,
                truth=truth,
            )
        )

    # 1. 加法：正确 / 近似错（±1 / 数字对调 / 符号翻转）
    for i in range(50):
        a, b = rng.randint(11, 999), rng.randint(11, 999)
        gold = str(a + b)
        if i % 2 == 0:
            add("arith_add", f"计算 {a}+{b} 并只输出数字", gold, gold, True)
        else:
            corrupt = rng.choice([str(a + b + 1), str(a + b - 1),
                                  gold[::-1], str(-(a + b))])
            add("arith_add", f"计算 {a}+{b} 并只输出数字", gold, corrupt, False)

    # 2. 乘法：正确 / 因数错位
    for i in range(40):
        a, b = rng.randint(3, 19), rng.randint(3, 19)
        gold = str(a * b)
        if i % 2 == 0:
            add("arith_mul", f"计算 {a}*{b} 并只输出数字", gold, gold, True)
        else:
            add("arith_mul", f"计算 {a}*{b} 并只输出数字", gold,
                str((a + 1) * b if i % 4 == 1 else a * (b - 1)), False)

    # 3. 百分比：等价格式（考 judge 的格式宽容度 → FP 风险）/ 缺 % 号
    for i in range(30):
        num = rng.choice([12.5, 25, 37.5, 50, 62.5, 75, 87.5])
        gold = f"{num}%"
        if i % 3 == 0:
            add("pct_format", f"{num}/100 的百分比形式", gold, f"{num} %", True)
        elif i % 3 == 1:
            add("pct_format", f"{num}/100 的百分比形式", gold, gold, True)
        else:
            add("pct_format", f"{num}/100 的百分比形式", gold, str(num), False)

    # 4. 大写转换：正确 / 部分大写
    words = ["hello world", "agent lens", "foo bar baz", "good morning"]
    for i in range(25):
        w = words[i % len(words)]
        gold = w.upper()
        if i % 2 == 0:
            add("upper_case", f"把 '{w}' 转大写输出", gold, gold, True)
        else:
            add("upper_case", f"把 '{w}' 转大写输出", gold, w.title(), False)

    # 5. 数列最值：正确 / 次大值混淆
    for i in range(25):
        seq = rng.sample(range(1, 100), 6)
        gold = str(max(seq))
        if i % 2 == 0:
            add("max_list", f"数列 {', '.join(map(str, seq))} 中最大的数？只输出数字",
                gold, gold, True)
        else:
            add("max_list", f"数列 {', '.join(map(str, seq))} 中最大的数？只输出数字",
                gold, str(sorted(seq)[-2]), False)

    # 6. 截断输出：全部错误（长答案被截断）
    for i in range(20):
        a, b = rng.randint(101, 999), rng.randint(101, 999)
        gold_full = f"结果是 {a + b}，因为 {a} 加 {b} 等于 {a + b}"
        cut = rng.randint(len(gold_full) // 3, len(gold_full) - 3)
        add("truncation", f"计算 {a}+{b} 并给出答案", f"{a + b}",
            gold_full[:cut], (a + b) in gold_full[:cut].split())

    # 7. 拒答/空输出：全部错误
    refusals = ["抱歉，我无法完成该任务。", "", "（无输出）"]
    for i in range(20):
        a, b = rng.randint(5, 99), rng.randint(5, 99)
        add("refusal", f"计算 {a}+{b} 并只输出数字", str(a + b), refusals[i % 3], False)

    return items


def build_pairs(n: int = 24, seed: int = 0) -> list[PairItem]:
    """成对比较池：better=正确答案，worse=受控损坏——position-swap 体检数据源。"""
    rng = random.Random(seed + 1)
    pairs = []
    for i in range(n):
        a, b = rng.randint(21, 500), rng.randint(21, 500)
        gold = str(a + b)
        pairs.append(PairItem(
            id=f"pair-{i:03d}",
            gold=gold,
            better=gold,
            worse=str(a + b + rng.choice([1, -1, 10])),
            task_input=f"计算 {a}+{b} 并只输出数字",
        ))
    return pairs


# ---------------- Judge 注册表（确定性人格 + 可选真实 llm） ----------------


def _norm_num(text: str) -> float | None:
    t = text.strip().rstrip("%").replace(",", "").replace(" ", "")
    try:
        return float(t)
    except ValueError:
        return None


def judge_exact(inp: str, gold: str, out: str) -> bool:
    """严格人格：strip 后全字符串相等。"""
    del inp
    return out.strip() == gold.strip()


def judge_numeric(inp: str, gold: str, out: str) -> bool:
    """宽容人格：数值等价即通过（% 后缀与空格差异不误杀）。"""
    del inp
    g, o = _norm_num(gold), _norm_num(out)
    return g is not None and o is not None and abs(g - o) < 1e-9


def make_noisy_judge(base: object, p_flip: float, seed: int):
    """在基础 judge 上按固定概率确定性翻转（模拟中等一致 LLM judge）。"""
    rng = random.Random(seed)

    def wrapped(inp: str, gold: str, out: str) -> bool:
        verdict = base(inp, gold, out)
        return not verdict if rng.random() < p_flip else verdict

    return wrapped


def resolve_judge(spec: str):
    """'exact' | 'numeric' | 'noisy:p=0.15,seed=7' → judge(inp,gold,out)->bool。"""
    if spec == "exact":
        return judge_exact
    if spec == "numeric":
        return judge_numeric
    if spec.startswith("noisy:"):
        kw = dict(kv.split("=") for kv in spec.split(":", 1)[1].split(","))
        base_name = kw.get("base", "numeric")
        base = resolve_judge(base_name)
        return make_noisy_judge(base, float(kw.get("p", 0.15)), int(kw.get("seed", 7)))
    raise ValueError(f"未知 judge 规格: {spec}")


JUDGE_PERSONAS = ("exact", "numeric")


# ---------------- 预标注 + 分层抽样 → 复核队列 ----------------


@dataclass
class QueueBucket:
    key: str
    n_pool: int
    n_queued: int


def stratified_queue(
    pool: list[CalibItem],
    judge,
    queue_size: int = 240,
) -> tuple[list[CalibItem], list[str], list[QueueBucket]]:
    """预标注后分层抽样：分歧 item 全保留，一致 item 按比例入队。

    返回 (复核队列, 预标注标签, 分桶统计)。judge 建议随 HTML 展示但不预选
    （避免锚定偏置）。
    """
    prelabels = [judge(it.task_input, it.gold, it.agent_output) for it in pool]
    buckets: dict[tuple[str, bool], list[int]] = {}
    for idx, (it, pre) in enumerate(zip(pool, prelabels)):
        buckets.setdefault((it.category, pre != it.truth), []).append(idx)

    disagree = sorted(i for k, v in buckets.items() if k[1] for i in v)
    agree = sorted(i for k, v in buckets.items() if not k[1] for i in v)
    n_agree_quota = max(queue_size - len(disagree), 0)
    if n_agree_quota >= len(agree):
        sampled_agree = list(agree)
    elif agree:
        step = len(agree) / n_agree_quota
        sampled_agree = [agree[min(int(i * step), len(agree) - 1)] for i in range(n_agree_quota)]
    else:
        sampled_agree = []
    queued_idx = sorted(set(disagree + sampled_agree))

    stats = [
        QueueBucket(key=f"{cat}/{'diverge' if div else 'match'}", n_pool=len(v),
                    n_queued=sum(1 for i in queued_idx if i in set(v)))
        for (cat, div), v in sorted(buckets.items())
    ]
    queue = [pool[i] for i in queued_idx]
    return queue, ["yes" if prelabels[i] else "no" for i in queued_idx], stats


# ---------------- 复核 UI（自包含 HTML，人工批量裁决 → JSONL 导出） ----------------


_REVIEW_TMPL = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>AgentLens judge 标注复核</title>
<style>
 body{{font-family:ui-sans-serif,system-ui;max-width:860px;margin:2rem auto;padding:0 1rem}}
 fieldset{{margin:.8rem 0;border:1px solid #ccc;border-radius:6px}}
 fieldset.current{{outline:2px solid #6c8cff}}
 legend{{font-weight:600}} .cat{{color:#777;font-size:.85rem}}
 .io{{white-space:pre-wrap;background:#f7f7fb;padding:.4rem .6rem;border-radius:4px}}
 label{{margin-right:1rem}}
 .bar{{position:sticky;top:0;background:#fff;padding:.6rem 0;border-bottom:1px solid #eee}}
 button{{padding:.5rem 1rem;font-size:1rem}}
 .warn{{color:#b00}} .pos{{color:#555;font-size:.9rem;margin-left:.6rem}}
 kbd{{background:#f0f0f5;border-radius:3px;padding:0 .3rem;font-size:.85em}}
</style></head><body>
<h1>Judge 标注复核（{n_items} 例 · 预计 ~{minutes} 分钟）</h1>
<p>逐条判断：<b>Agent 输出是否正确完成了任务？</b>忽略 judge 建议，按你自己的判断选择。
快捷键：<kbd>j</kbd>/<kbd>k</kbd> 移动，<kbd>1</kbd>=正确 <kbd>0</kbd>=错误（裁决后自动跳下一题）。
完成后点「导出标注」，得到 labels.jsonl。</p>
<div class="bar">已裁决 <b id="done">0</b>/{n_items}
 <span class="pos" id="pos"></span>
 <span class="warn" id="warn"></span>
 <button onclick="exportLabels()">导出标注 labels.jsonl</button>
 <button onclick="clearProgress()">清除进度</button></div>
{items}
<script>
const PROGRESS_KEY = 'lens-review-progress';
const fieldsets = [...document.querySelectorAll('fieldset')];
let cur = 0;
function updateDone(){{
  document.getElementById('done').textContent =
    fieldsets.filter(f => f.querySelector('input:checked')).length;
}}
function persistProgress(){{
  // 刷新不丢进度：勾选状态实时写入 localStorage
  const state = {{}};
  for (const f of fieldsets) {{
    const sel = f.querySelector('input:checked');
    if (sel) state[f.dataset.id] = sel.value;
  }}
  try {{ localStorage.setItem(PROGRESS_KEY, JSON.stringify(state)); }} catch (e) {{}}
}}
function restoreProgress(){{
  try {{
    const saved = JSON.parse(localStorage.getItem(PROGRESS_KEY) || '{{}}');
    for (const [id, val] of Object.entries(saved)) {{
      const el = document.querySelector(`input[name="${{id}}"][value="${{val}}"]`);
      if (el) el.checked = true;
    }}
  }} catch (e) {{}}
}}
function setCurrent(i){{
  if (!fieldsets.length) return;
  cur = Math.max(0, Math.min(i, fieldsets.length - 1));
  for (const f of fieldsets) f.classList.remove('current');
  const f = fieldsets[cur];
  f.classList.add('current');
  document.getElementById('pos').textContent = `第 ${{cur + 1}}/${{fieldsets.length}} 例`;
  f.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
}}
function answerCurrent(val){{
  const sel = fieldsets[cur].querySelector(`input[value="${{val}}"]`);
  if (sel) sel.checked = true;
  persistProgress();
  updateDone();
  if (cur < fieldsets.length - 1) setCurrent(cur + 1);
}}
function clearProgress(){{
  try {{ localStorage.removeItem(PROGRESS_KEY); }} catch (e) {{}}
  for (const r of document.querySelectorAll('input[type=radio]')) r.checked = false;
  document.getElementById('warn').textContent = '';
  updateDone();
}}
function exportLabels(){{
  const rows=[]; let missing=0;
  for (const f of fieldsets) {{
    const sel = f.querySelector('input:checked');
    if (!sel) {{ missing++; continue; }}
    rows.push(JSON.stringify({{item_id: f.dataset.id, human_label: sel.value === '1'}}));
  }}
  if (missing) document.getElementById('warn').textContent = `还有 ${{missing}} 例未裁决`;
  const blob = new Blob([rows.join("\\n")], {{type: 'application/jsonl'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = 'labels.jsonl'; a.click();
}}
document.addEventListener('keydown', (e) => {{
  if (e.key === 'j' || e.key === 'ArrowDown') {{ e.preventDefault(); setCurrent(cur + 1); }}
  else if (e.key === 'k' || e.key === 'ArrowUp') {{ e.preventDefault(); setCurrent(cur - 1); }}
  else if (e.key === '1') answerCurrent('1');
  else if (e.key === '0') answerCurrent('0');
}});
document.addEventListener('change', () => {{ persistProgress(); updateDone(); }});
restoreProgress();
setCurrent(0);
</script></body></html>"""


def render_review_html(queue: list[CalibItem], prelabels: list[str]) -> str:
    """生成自包含复核页；judge 建议只展示不预选。内容经 html.escape 防注入。"""
    import html as _html

    chunks = []
    minutes = max(1, round(len(queue) * 9 / 60))
    for it, pre in zip(queue, prelabels):
        qid = _html.escape(it.id, quote=True)
        chunks.append(
            f'<fieldset data-id="{_html.escape(it.id)}"><legend>{_html.escape(it.id)} '
            f'<span class="cat">[{_html.escape(it.category)}] judge 建议: {pre}</span></legend>'
            f'<div class="io">任务: {_html.escape(it.task_input)}\n'
            f'参考答案: {_html.escape(it.gold)}\n'
            f'Agent 输出: {_html.escape(it.agent_output)}</div>'
            f'<label><input type="radio" name="{qid}" value="1">正确</label>'
            f'<label><input type="radio" name="{qid}" value="0">错误</label>'
            f"</fieldset>"
        )
    return _REVIEW_TMPL.format(n_items=len(queue), minutes=minutes, items="\n".join(chunks))


# ---------------- κ 报告（以人工标签为准；构造真值仅作参照） ----------------


def load_jsonl(path: str | Path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def kappa_report(
    pool: list[CalibItem],
    labels: list[dict],
    judge=None,
    n_boot: int = 2000,
    seed: int = 0,
) -> dict[str, object]:
    """κ / 混淆矩阵 / 分类别误杀漏杀 / 长度偏置 / bootstrap CI。

    judge 为 None 时以人工标签 vs 构造真值算（标注质量自查）；
    给定 judge 时同时给 judge-vs-人工 的 κ（切 block 的核心数字）。
    """
    from .judge_lab import cohens_kappa, length_bias_check

    by_id = {it.id: it for it in pool}
    joined = []
    for rec in labels:
        it = by_id.get(str(rec["item_id"]))
        if it is None:
            continue
        human = bool(rec["human_label"])
        row: dict[str, object] = {"item": it, "human": human, "truth": it.truth}
        if judge is not None:
            row["judge"] = judge(it.task_input, it.gold, it.agent_output)
        joined.append(row)
    if not joined:
        raise ValueError("labels 与 pool 无交集")

    def _confusion(pred_key: str, ref_key: str) -> dict[str, object]:
        pred = [r[pred_key] for r in joined]
        ref = [r[ref_key] for r in joined]
        fp = sum(1 for p, rr in zip(pred, ref) if not p and rr)   # 误杀：判错好输出
        fn = sum(1 for p, rr in zip(pred, ref) if p and not rr)   # 漏杀：放过坏输出
        n_ref_pos = sum(ref)
        n_ref_neg = len(ref) - n_ref_pos
        return {
            "kappa": round(cohens_kappa(pred, ref), 4),
            "agreement": round(fmean([p == rr for p, rr in zip(pred, ref)]), 4),
            "false_block_rate": round(fp / n_ref_neg, 4) if n_ref_neg else float("nan"),
            "miss_rate": round(fn / n_ref_pos, 4) if n_ref_pos else float("nan"),
            "fp": fp, "fn": fn, "n": len(ref),
        }

    report: dict[str, object] = {
        "n_labeled": len(joined),
        "human_vs_truth": _confusion("human", "truth"),
    }
    if judge is not None:
        report["judge_vs_human"] = _confusion("judge", "human")
        # κ 的 bootstrap CI（按 item 重采样）
        j = [bool(r["judge"]) for r in joined]
        h = [bool(r["human"]) for r in joined]
        rng = random.Random(seed)
        boots = []
        for _ in range(n_boot):
            idx = [rng.randrange(len(j)) for _ in range(len(j))]
            boots.append(cohens_kappa([j[i] for i in idx], [h[i] for i in idx]))
        boots.sort()
        report["kappa_ci95"] = (
            round(boots[int(0.025 * n_boot)], 4), round(boots[int(0.975 * n_boot)], 4)
        )
        lengths = [len(r["item"].agent_output) for r in joined]
        report["length_bias"] = length_bias_check(j, lengths)
    return report


# ---------------- position-swap 一致性（成对模式） ----------------


def swap_consistency(pairs: list[PairItem], judge_pair) -> float:
    """judge_pair(task_input, gold, first, second) -> 'A'|'B'|'tie'。

    交换候选顺序各问一次，两次都指认 better 才算一致。返回一致率。
    """

    def ask(p: PairItem, order: str) -> bool:
        first, second = (p.better, p.worse) if order == "AB" else (p.worse, p.better)
        ans = judge_pair(p.task_input, p.gold, first, second)
        picked_better = (ans == "A") if order == "AB" else (ans == "B")
        return picked_better

    hits = [ask(p, "AB") and ask(p, "BA") for p in pairs]
    return fmean(hits) if hits else float("nan")


def make_pair_judge(judge):
    """把单输出 judge 包装成成对 judge：对 gold 分别打分，正确者胜。

    平手（同对同错）返回 'tie'，不计入一致性命中。
    """
    def judge_pair(task_input: str, gold: str, first: str, second: str) -> str:
        f_ok = judge(task_input, gold, first)
        s_ok = judge(task_input, gold, second)
        if f_ok and not s_ok:
            return "A"
        if s_ok and not f_ok:
            return "B"
        return "tie"

    return judge_pair


def make_llm_pair_judge(provider, rubric: str = "两个候选答案哪个更好？"):
    """真 pairwise judge：A/B 双候选一次一问，回答解析为 A/B/tie。

    与 make_pair_judge（规则包装、两候选分别打分）不同：真实 LLM pairwise
    一眼同看两个候选，能检出「分别打分都判对但相对偏好翻转」的位置偏置。
    未解析的回答容错为 tie。离线确定性由 MockProvider 的 pairwise 分支保证。
    """

    def judge_pair(task_input: str, gold: str, first: str, second: str) -> str:
        prompt = (
            f"{rubric}\n任务: {task_input}\n参考答案: {gold}\n"
            f"候选 A: {first}\n候选 B: {second}\n只回答 A、B 或 tie:"
        )
        res = provider.chat([{"role": "user", "content": prompt}])
        ans = res.text.strip().lower().rstrip(".").rstrip("。")
        if ans.startswith("a"):
            return "A"
        if ans.startswith("b"):
            return "B"
        return "tie"

    return judge_pair


# ---------------- κ 报告 HTML 渲染 ----------------


def render_kappa_html(report: dict[str, object], title: str = "Judge 校准报告") -> str:
    """κ 报告自包含页：混淆矩阵 + 分类别表 + 决策规则提醒。"""

    def table(rows: dict[str, object], caption: str) -> str:
        trs = "".join(
            f"<tr><td>{k}</td><td><b>{v}</b></td></tr>"
            for k, v in rows.items()
            if not isinstance(v, dict)
        )
        return f"<h2>{caption}</h2><table>{trs}</table>"

    sections = []
    for key, cap in (("human_vs_truth", "人工 vs 构造真值（标注质量自查）"),
                     ("judge_vs_human", "Judge vs 人工（切 block 核心数字）")):
        if key in report:
            r = dict(report[key])
            ci = report.get("kappa_ci95")
            if key == "judge_vs_human" and ci:
                r["κ 95% CI"] = f"{ci[0]} – {ci[1]}"
            sections.append(table(r, cap))
    lb = report.get("length_bias")
    if lb:
        sections.append(table(dict(lb), "长度偏置（三桶通过率应大致平坦）"))
    return (
        "<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
        f"<title>{title}</title><style>"
        "body{font-family:ui-sans-serif,system-ui;max-width:720px;margin:2rem auto;padding:0 1rem}"
        "table{border-collapse:collapse;width:100%;margin:1rem 0}"
        "td{border:1px solid #ddd;padding:6px 10px}h1{font-size:1.3rem}"
        ".muted{color:#777;font-size:.85rem}</style></head><body>"
        f"<h1>{title}</h1><p class='muted'>n={report['n_labeled']} · "
        "真值基线来自构造过程，非人工；切 block 前提见 docs/judge-block-policy.md</p>"
        + "\n".join(sections) + "</body></html>"
    )
