#!/usr/bin/env python3
"""PreToolUse 훅 — subagent 호출 직전 위임 프롬프트 표준 4블록 확인 + Plan 검증 환기.

출력 채널:
- 차단 (4블록 누락): exit 2 + stderr — Claude blocking error 주입, 보강 후 재호출 유도
- 환기 (통과): exit 0 + hookSpecificOutput.additionalContext — Plan 검증 환기 주입

matcher: Agent|Task (v2.1.63에서 Task → Agent 개명 — 구버전 호환 위해 둘 다.
matcher 의 letter-only 토큰은 tool_name 정확일치라 "Task" 단독이면 현행에서 발화 0)

동작 단계:
1. tool_name 이 Agent/Task 가 아니면 silent exit 0
2. subagent_type 이 .claude/agents/ 의 커스텀 도메인 에이전트가 아니면 silent exit 0
   (verifier/Plan/Explore 등 유틸·미정의 에이전트는 통과)
3. tool_input.prompt 에서 TASK / USER_DECISIONS / CONSTRAINTS / GATE 4블록 존재 확인
4. 누락 시 stderr + exit 2 — Claude context 에 blocking error 주입, 보강 후 재호출 유도
5. 통과 시 hookSpecificOutput JSON 출력 (permissionDecision=allow + additionalContext) + exit 0
   — 차단 없이 Plan 검증 환기 메시지를 Claude context 에 주입 (exit 0 + plain stderr 는
   사용자 터미널만 보이고 메인 context 에 안 들어가므로 무효)

한계: 정규식 기반이라 블록 존재만 점검한다. 블록 내용 적정성과 Plan 검증 실제 호출 여부는
CLAUDE.md 의 "위임 전 due diligence" 자연어 절차에 의존한다.

참고: https://code.claude.com/docs/en/hooks.md (PreToolUse hookSpecificOutput 스펙)
"""

from __future__ import annotations

import json
import os
import sys

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from delegation_common import is_defined_domain_agent  # noqa: E402

REQUIRED_BLOCKS: tuple[str, ...] = (
    "TASK:",
    "USER_DECISIONS:",
    "CONSTRAINTS:",
    "GATE:",
)


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    if (data.get("tool_name") or "") not in ("Agent", "Task"):
        return 0

    tool_input = data.get("tool_input") or {}
    subagent_type = tool_input.get("subagent_type") or ""
    if not is_defined_domain_agent(os.environ.get("CLAUDE_PROJECT_DIR"), subagent_type):
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

    advisory = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "additionalContext": (
                "[delegation-prompt-check] 도메인 위임 직전 — "
                "Plan subagent 외부 검증 호출했는가? "
                'Agent 툴(subagent_type="Plan") 로 tasks/todo.md 5섹션 검증 권장 '
                "(강제 아님, 환기)."
            ),
        }
    }
    print(json.dumps(advisory, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
