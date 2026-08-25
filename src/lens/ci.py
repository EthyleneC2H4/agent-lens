"""CI 门禁胶水 —— gate JSON → GitHub PR 评论（markdown 渲染 + 幂等 upsert）。

零第三方依赖；HTTP 层可注入 transport 以便离线测试。同一 PR 的重复运行
只更新带 MARKER 的既有评论，不刷屏。
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from typing import Any

MARKER = "<!-- agent-lens-gate:v1 -->"

ApiTransport = Any  # callable(method, url, token, body) -> dict|list


def render_comment(payload: dict[str, object]) -> str:
    """把 gate --out-json 的 payload 渲染成 PR 评论 markdown。"""
    allowed = bool(payload["allowed"])
    verdict = "✅ **放行**" if allowed else "🛑 **阻断**"
    diffs = payload["diffs"] or []
    status_icon = {
        "improved": "🟢",
        "regressed": "🔴",
        "fragile": "🟡",
        "unchanged": "⚪",
    }
    lines = [
        MARKER,
        f"## AgentLens 门禁判定：{verdict}",
        "",
        "| 模式 | 基线 → 候选 | 候选通过率 |",
        "|---|---|---|",
        f"| `{payload['mode']}` | `{payload['base_run']}` → `{payload['cand_run']}` "
        f"| {float(payload['cand_success_rate']):.2%} |",
        "",
        "| case | base | cand | status |",
        "|---|---|---|---|",
    ]
    for d in diffs:
        assert isinstance(d, dict)
        lines.append(
            f"| {status_icon.get(str(d['status']), '⚪')} {d['task_id']} "
            f"| {d['base_passes']}/{d['trials']} | {d['cand_passes']}/{d['trials']} "
            f"| {d['status']} |"
        )
    violations = payload.get("violations") or []
    if violations:
        lines += ["", "**违规项**"]
        lines += [f"- ⚠️ {v}" for v in violations]
    lines += [
        "",
        "<sub>pass@k（乐观界）/ pass^k（悲观界）双侧分布口径见 README；"
        "门禁阈值规则见 docs/gate-policy.md。本评论由 AgentLens 自动维护。</sub>",
    ]
    return "\n".join(lines)


def default_transport(
    method: str, url: str, token: str, body: dict[str, object] | None
) -> Any:
    """GitHub API 的 urllib 实现。"""
    req = urllib.request.Request(  # noqa: S310
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "agent-lens-gate",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        raw = resp.read().decode()
    return json.loads(raw) if raw else {}


def upsert_comment(
    repo: str,
    pr_number: int,
    token: str,
    body: str,
    transport: ApiTransport | None = None,
    api_base: str = "https://api.github.com",
) -> dict[str, object]:
    """找带 MARKER 的既有评论则更新，否则新建。返回 API 响应。"""
    tp = transport or default_transport
    comments = tp("GET", f"{api_base}/repos/{repo}/issues/{pr_number}/comments", token, None)
    target = next((c for c in comments if str(c.get("body", "")).startswith(MARKER)), None)
    if target:
        return tp(
            "PATCH",
            f"{api_base}/repos/{repo}/issues/comments/{target['id']}",
            token,
            {"body": body},
        )
    return tp(
        "POST", f"{api_base}/repos/{repo}/issues/{pr_number}/comments", token, {"body": body}
    )


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：无 --token 时干跑（只打印渲染结果），便于本地验证。"""
    ap = argparse.ArgumentParser(description="AgentLens PR 评论机器人")
    ap.add_argument("--gate-json", required=True, help="lens gate --out-json 的输出文件")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--pr", type=int, default=0)
    ap.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    args = ap.parse_args(argv)

    payload = json.loads(open(args.gate_json, encoding="utf-8").read())  # noqa: SIM115
    body = render_comment(payload)
    if not args.token or not args.repo or not args.pr:
        print(body)
        print("\n[lens] 干跑模式（缺 --token/--repo/--pr），未调用 GitHub API")
        return 0
    resp = upsert_comment(args.repo, args.pr, args.token, body)
    print(f"评论已更新: {resp.get('html_url', '(no url)')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
