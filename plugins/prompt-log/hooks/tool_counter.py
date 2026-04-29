#!/usr/bin/env python3
# [prompt-log] removable plugin — see plugins/prompt-log/README.md
"""PreToolUse 훅 — 도구 호출 카운트 누적.

matcher: Edit|Write|MultiEdit|Bash|Task

동작:
1. 동의 검사. 미동의면 exit 0
2. active record 없으면 exit 0 (prompt가 없는데 도구 호출 — 무시)
3. tools.<bucket>++ 누적 + total++
4. 영향 파일(Edit/Write/MultiEdit) 추가
5. exit 0 (모든 도구 호출 그대로 통과)
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prompt_log_lib as pl  # noqa: E402


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    tool_name = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}

    root = pl.pl_find_project_root()
    if root is None or not pl.pl_is_consented(root):
        return 0

    active = pl.pl_load_active(root)
    if active is None:
        return 0  # prompt 없이 도구만 호출되는 경우 (이론상 드뭄)

    bucket = pl.pl_tool_bucket(tool_name)
    tools = active.setdefault("tools", {
        "edit": 0, "write": 0, "multi_edit": 0,
        "bash": 0, "task": 0, "other": 0, "total": 0,
    })
    tools[bucket] = tools.get(bucket, 0) + 1
    tools["total"] = tools.get("total", 0) + 1

    target = pl.pl_extract_target_file(tool_name, tool_input)
    if target:
        unique = active.setdefault("files", {}).setdefault("unique", [])
        if target not in unique:
            unique.append(target)

    pl.pl_save_active(root, active)
    return 0


if __name__ == "__main__":
    sys.exit(main())
