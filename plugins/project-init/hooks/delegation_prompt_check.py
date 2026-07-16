#!/usr/bin/env python3
"""PreToolUse 훅 — subagent 호출 직전 위임 프롬프트 표준 4블록 확인 + Plan 검증·백그라운드 환기.

출력 채널:
- 차단 (4블록 누락): exit 2 + stderr — Claude blocking error 주입, 보강 후 재호출 유도
- 환기 (통과): exit 0 + hookSpecificOutput.additionalContext — Plan 검증·백그라운드 환기 주입

matcher: Agent|Task (v2.1.63에서 Task → Agent 개명 — 구버전 호환 위해 둘 다.
matcher 의 letter-only 토큰은 tool_name 정확일치라 "Task" 단독이면 현행에서 발화 0)

동작 단계:
1. tool_name 이 Agent/Task 가 아니면 silent exit 0
2. subagent_type 이 .claude/agents/ 의 커스텀 도메인 에이전트면 tool_input.prompt 에서
   TASK / USER_DECISIONS / CONSTRAINTS / GATE 4블록 존재 확인 — 누락 시 stderr + exit 2
   (verifier/Plan/Explore 등 유틸·미정의 에이전트는 4블록 검사 없이 통과)
3. 환기거리(도메인 위임의 Plan 검증 · 동기 호출)를 모아 hookSpecificOutput JSON 출력
   (additionalContext 단독 — 권한 판단 없음) + exit 0. 환기거리가 없으면 silent exit 0.
   exit 0 + plain stderr 는 사용자 터미널만 보이고 메인 context 에 안 들어가므로 무효.

동기 호출 환기는 에이전트 종류를 가리지 않는다 — run_in_background 를 명시적으로 false 로
주면 메인 세션이 서브에이전트 완료까지 멈춘다. 필드 생략은 툴 기본값(백그라운드)이라 조용히
통과. 환기일 뿐이라 이미 시작된 그 호출은 못 막는다 — 다음 호출부터 반영된다.

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
    is_domain = is_defined_domain_agent(os.environ.get("CLAUDE_PROJECT_DIR"), subagent_type)

    if is_domain:
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

    notes: list[str] = []
    if is_domain:
        notes.append(
            "도메인 위임 직전 — Plan subagent 외부 검증 호출했는가? "
            'Agent 툴(subagent_type="Plan") 로 tasks/todo.md 5섹션 검증 권장 (강제 아님, 환기).'
        )
    if tool_input.get("run_in_background") is False:
        notes.append(
            "서브에이전트를 동기(run_in_background=false)로 호출했다 — 완료까지 메인 세션이 "
            "멈춘다. 결과를 받아야 다음 스텝을 정할 수 있는 경우가 아니면 run_in_background 를 "
            "생략(기본 백그라운드)하라."
        )
    if not notes:
        return 0

    # permissionDecision 은 싣지 않는다 — "allow" 를 실으면 이 환기가 붙은 Agent 호출이
    # 사용자 권한 프롬프트를 우회한다(환기는 정보 주입일 뿐 권한 판단이 아니다).
    advisory = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "[delegation-prompt-check] " + " / ".join(notes),
        }
    }
    print(json.dumps(advisory, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
