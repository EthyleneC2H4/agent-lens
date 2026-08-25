#!/usr/bin/env python3
"""PR 评论机器人入口（薄壳）——逻辑在 lens.ci，便于离线测试。

用法：
  uv run python scripts/pr_comment.py --gate-json gate.json \
      --repo OWNER/REPO --pr 123 --token "$GITHUB_TOKEN"
缺 token/repo/pr 时干跑：仅打印渲染后的 markdown。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lens.ci import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
