#!/usr/bin/env bash
# bootstrap-remote.sh —— git remote 创建 + 推送 + 真实 PR 门禁演示分支生成。
#
# ROADMAP Phase B 最后一项（陌生 PR 触发评测的实录演示）只卡「仓库不存在」
# 这一个外部动作。本脚本把它压到最小：
#   · 有 gh CLI 且已登录 → 自动建仓并推送；
#   · 没有 → 打印 30 秒手工路径，用户建完空仓重跑本脚本即可续行。
#
# 用法：
#   scripts/bootstrap-remote.sh [REMOTE_URL]
#     REMOTE_URL 默认 git@github.com:EthyleneC2H4/agent-lens.git
#
# 演示设计（对应 v1.0 完成态 #1「observe / block 两态」）：
#   main 分支 workflow 用内置 mock solver——两版本一致，门禁永远绿；
#   脚本另生成两个演示分支，把 cand 评测换成恒错的退化 solver
#   （lens.fixtures.degraded_solver:make_degraded_solver）：
#     demo/regressed-observe —— MODE 兜底保持 observe：CI 绿跑，
#                               但 PR 评论列出全部退化 case（观察态）；
#     demo/regressed-block   —— MODE 兜底改 block：CI 直接红 X（阻断态）。
#   两个 PR 的截图入 docs/ 即可关闭 Phase B。

set -euo pipefail

OWNER_REPO="${OWNER_REPO:-EthyleneC2H4/agent-lens}"
REMOTE_URL="${1:-git@github.com:${OWNER_REPO}.git}"

cd "$(git rev-parse --show-toplevel)"

# ---------- 1) remote 就位 ----------
if git remote get-url origin >/dev/null 2>&1; then
  echo "✓ origin 已存在: $(git remote get-url origin)"
else
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    echo "→ 用 gh 创建仓库 ${OWNER_REPO} 并推送…"
    gh repo create "$OWNER_REPO" --source . --remote origin --push
  else
    cat <<EOF
✗ 未检测到可用的 gh CLI。请手工完成一次性动作（~30 秒）：
    1. 打开 https://github.com/new 新建空仓 ${OWNER_REPO}
       （不要勾选 README / .gitignore / license——必须完全为空）
    2. 回来执行本脚本并显式传入远端地址：
       scripts/bootstrap-remote.sh git@github.com:${OWNER_REPO}.git
  （若用 HTTPS 而非 SSH，地址形如 https://github.com/${OWNER_REPO}.git）
EOF
    exit 1
  fi
fi

# ---------- 2) 推 main ----------
echo "→ 推送 main…"
git push -u origin main

# ---------- 3) 生成演示分支 ----------
make_demo_branch() {   # $1=分支名 $2=observe|block
  local branch="$1" mode="$2"
  echo "→ 构造演示分支 ${branch}（${mode} 态）…"
  git checkout -q -b "$branch" main
  MODE="$mode" python3 - <<'PY'
import os
import pathlib

mode = os.environ["MODE"]
p = pathlib.Path(".github/workflows/lens-gate.yml")
s = p.read_text()
needle = (
    '--version "cand-${{ github.event.pull_request.head.sha || github.sha }}" \\'
)
replacement = needle + (
    "\n            --solver-spec lens.fixtures.degraded_solver:"
    "make_degraded_solver \\"
)
assert needle in s, "workflow 中找不到 cand 版本评测行（上游已改动？）"
s = s.replace(needle, replacement, 1)
if mode == "block":
    fallback = "${{ inputs.mode || 'observe' }}"
    assert fallback in s, "workflow 中找不到 MODE 兜底表达式"
    s = s.replace(fallback, "${{ inputs.mode || 'block' }}", 1)
p.write_text(s)
PY
  git add .github/workflows/lens-gate.yml
  git commit -q -m "ci(demo): cand 评测切换退化 solver 制造真实回归（门禁${mode}态演示分支，勿合并）"
  git push -qu origin "$branch"
}

make_demo_branch demo/regressed-observe observe
make_demo_branch demo/regressed-block block
git checkout -q main

cat <<EOF

✅ 全部推送完成。在浏览器开两个 PR（各等 ~2 分钟 CI 跑完）：

  observe 态（绿跑 + PR 评论列出退化清单）:
    https://github.com/${OWNER_REPO}/pull/new/demo/regressed-observe
  block 态（红 X + 门禁阻断）:
    https://github.com/${OWNER_REPO}/pull/new/demo/regressed-block

截图两个 PR 的 checks 与评论页存入 docs/，即可关闭 ROADMAP Phase B
最后一项与 §8.2 #9（ubuntu CI 实跑）。
EOF
