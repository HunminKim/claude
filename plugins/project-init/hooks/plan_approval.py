#!/usr/bin/env python3
"""UserPromptSubmit hook — plan-gate 토큰 fallback 처리.

슬래시 커맨드(`/approve-plan` 등)는 commands/*.md 정의가 1차로 처리하지만,
사용자가 슬래시 커맨드 형태가 아닌 평문 메시지로 토큰을 입력해도 동일하게
plan-gate 상태를 갱신할 수 있도록 fallback을 제공한다.

idempotent: 같은 토큰을 슬래시 커맨드 + 메시지 둘 다로 받아도 안전.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_gate_lib as lib  # noqa: E402

TOKEN_TO_ACTION = {
    "/approve-plan": "approve",
    "/done": "done",
    "/skip": "skip",
    "/keep": "skip",   # /skip의 별칭 — 동일하게 동작, 자동완성에는 /skip만 노출
    "/rollback": "rollback",
    "/retry": "retry",
    "/replan": "replan",
}


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    prompt = (data.get("prompt") or "").strip()
    if prompt not in TOKEN_TO_ACTION:
        return 0

    root = lib.find_project_root()
    if root is None or not lib.is_plan_gate_enabled(root):
        return 0

    action = TOKEN_TO_ACTION[prompt]
    cli = Path(__file__).parent / "plan_gate_cli.py"
    try:
        r = subprocess.run(
            [sys.executable, str(cli), action],
            capture_output=True,
            text=True,
            cwd=str(root),
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(root)},
        )
    except Exception as e:
        sys.stderr.write(f"[plan-gate approval] CLI 실행 실패: {e}\n")
        return 0

    # CLI stdout/stderr를 그대로 Claude 컨텍스트에 노출
    if r.stdout:
        sys.stdout.write(r.stdout)
    if r.stderr:
        sys.stderr.write(r.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
