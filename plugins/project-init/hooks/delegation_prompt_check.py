#!/usr/bin/env python3
"""PreToolUse 훅 — subagent 호출 직전 위임 프롬프트 표준 4블록 확인.

matcher: Task

동작 단계:
1. tool_name 이 Task 가 아니면 silent exit 0
2. subagent_type 이 도메인/일반 에이전트가 아니면 silent exit 0 (verifier/Plan/Explore 등은 통과)
3. tool_input.prompt 에서 TASK / USER_DECISIONS / CONSTRAINTS / GATE 4블록 존재 확인
4. 누락 시 stderr + exit 2

한계: 정규식 기반이라 블록 존재만 점검한다. 블록 내용 적정성은
CLAUDE.md 의 "위임 전 due diligence" 가 Plan subagent 외부 검증으로 보완한다.
"""

from __future__ import annotations

import json
import sys

REQUIRED_BLOCKS: tuple[str, ...] = (
    "TASK:",
    "USER_DECISIONS:",
    "CONSTRAINTS:",
    "GATE:",
)
DELEGATION_SUBAGENTS: frozenset[str] = frozenset(
    {"backend", "frontend", "deeplearning", "ai", "general-purpose"}
)


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    if (data.get("tool_name") or "") != "Task":
        return 0

    tool_input = data.get("tool_input") or {}
    subagent_type = tool_input.get("subagent_type") or ""
    if subagent_type not in DELEGATION_SUBAGENTS:
        return 0

    prompt = tool_input.get("prompt") or ""
    missing = [b for b in REQUIRED_BLOCKS if b not in prompt]
    if missing:
        print(
            f"[delegation-prompt-check] 위임 프롬프트 표준 블록 누락: {', '.join(missing)}. "
            "TASK/USER_DECISIONS/CONSTRAINTS/GATE 4블록 모두 포함 필수 "
            "(tasks/todo.md 5섹션에서 발췌).",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
