#!/usr/bin/env python3
"""PostToolUse hook (matcher: Edit|Write|MultiEdit) — verifier 미호출 상기.

출력 채널: 환기 (exit 0 + stdout hookSpecificOutput.additionalContext JSON)

코드 파일 수정 후 @verifier 를 한 번도 호출하지 않았으면 Claude context 에
verifier 호출 환기 메시지를 주입한다. 차단하지 않는다.

출력 조건 (AND):
  - gate["state"] == "approved" AND verifier_status is None
  - approved_auto == False (project-init 스캐폴딩 제외)
  - 수정 파일이 코드 파일 (docs/ tasks/ .claude/ .git 제외)
  - edit_count_post_approval >= 2 (첫 편집엔 출력 X)
  - 짝수 편집 횟수마다 1번 출력
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_gate_lib as lib  # noqa: E402

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

_SKIP_PREFIXES = ("docs/", "tasks/", ".claude/", ".git")


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Edit", "Write", "MultiEdit"):
        return 0

    root = lib.find_project_root()
    if root is None or not lib.is_plan_gate_enabled(root):
        return 0

    state = lib.load_state(root)
    gate = lib.current_gate(state)

    if gate is None or gate["state"] != "approved":
        return 0
    if gate.get("verifier_status") is not None:
        return 0
    if gate.get("approved_auto"):
        return 0

    tool_input = data.get("tool_input", {}) or {}
    target = lib.extract_target_file(tool_name, tool_input, project_root=root)
    if not target:
        return 0
    if any(target.startswith(p) for p in _SKIP_PREFIXES):
        return 0

    count = gate.get("edit_count_post_approval", 0)
    if count < 2 or count % 2 != 0:
        return 0

    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": (
                f"[plan-gate] 💡 코드 수정 {count}회 — @verifier 검증이 아직 없습니다.\n"
                "  기능 구현이 완료됐으면 @verifier 를 호출해 검증하세요."
            ),
        }
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
