#!/usr/bin/env python3
"""PostToolUse Write hook — tasks/todo.md 작성 직후 계획 요약·승인 요청 주입.

tasks/todo.md 가 새로 작성되면 파일 내용을 읽어 Claude 컨텍스트에 주입한다.
Claude는 이 컨텍스트를 받아 계획을 사용자에게 요약해서 보여준 뒤
/approve-plan 을 요청한다.

plan-gate 가 비활성 상태이거나 tasks/todo.md 가 아닌 파일을 쓸 때는
아무것도 하지 않는다.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_gate_lib as lib  # noqa: E402

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    file_path = data.get("tool_input", {}).get("file_path", "")
    if not file_path.endswith("tasks/todo.md"):
        return 0

    root = lib.find_project_root()
    if root is None or not lib.is_plan_gate_enabled(root):
        return 0

    todo = Path(file_path)
    if not todo.exists():
        return 0

    content = todo.read_text(encoding="utf-8", errors="ignore").strip()
    if not content:
        return 0

    context = (
        "tasks/todo.md 작성 완료. 아래 계획을 사용자에게 요약해서 보여준 뒤, "
        "작업을 시작하기 전에 반드시 `/approve-plan` 입력을 요청하라. "
        "승인 없이 편집을 시작하지 않는다.\n\n"
        f"{content}"
    )

    result = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": context,
        }
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
