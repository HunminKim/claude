#!/usr/bin/env python3
"""UserPromptSubmit 훅 — 사용자 위임 요청 감지 시 tasks/todo.md 5섹션 점검.

출력 채널: 비차단 환기 (exit 0 + hookSpecificOutput.additionalContext)
  — UserPromptSubmit 에서 exit 2 는 프롬프트를 지우고 stderr 를 사용자에게만 보여
  메인 Claude 가 못 본다(=보강 유도 무효). 실제 위임 차단은 PreToolUse
  delegation_prompt_check 이 담당하고, 이 훅은 메인에게 todo.md 보강을 환기만 한다.

matcher: 전역 (UserPromptSubmit)

동작 단계:
1. 위임 의도 감지 (위임/맡겨 키워드 또는 .claude/agents/ 의 도메인 에이전트 @멘션)
2. 미매칭이면 silent exit 0 (일반 대화 통과)
3. tasks/todo.md 가 없으면 additionalContext 로 5섹션 작성 환기
4. todo.md 에 5섹션(영향 파일/USER_DECISIONS/CONSTRAINTS/기술 충돌 점검/fallback) 헤더 존재 확인
5. 누락 섹션이 있으면 additionalContext 로 보강 환기

한계: 정규식 기반이라 섹션 헤더 존재만 점검한다. 내용 충분성은
CLAUDE.md/workflow.md 의 "위임 전 due diligence" 가 Plan subagent 외부 검증으로 보완한다.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from delegation_common import is_defined_domain_agent  # noqa: E402

# 위임 의도 감지: 한국어 키워드(에이전트 무관) + .claude/agents/ 에 정의된 도메인 에이전트 @멘션.
# 특정 이름(backend 등)을 박지 않고 프로젝트가 정의한 어떤 에이전트에도 일반화한다.
_DELEGATION_KEYWORD = re.compile(r"위임|맡겨")
_MENTION = re.compile(r"@(?:agent-)?([A-Za-z][\w-]*)")
REQUIRED_SECTIONS: tuple[str, ...] = (
    "영향 파일",
    "USER_DECISIONS",
    "CONSTRAINTS",
    "기술 충돌 점검",
    "fallback",
)


def _mentions_domain_agent(prompt: str) -> bool:
    """@멘션 중 .claude/agents/ 에 정의된 커스텀 도메인 에이전트가 있으면 True.

    유틸 에이전트(Plan/Explore/verifier 등) 멘션은 위임으로 보지 않는다.
    """
    root = os.environ.get("CLAUDE_PROJECT_DIR")
    if not root:
        return False
    for m in _MENTION.finditer(prompt):
        if is_defined_domain_agent(root, m.group(1)):
            return True
    return False


def find_todo() -> Path | None:
    root = os.environ.get("CLAUDE_PROJECT_DIR")
    if not root:
        return None
    cand = Path(root) / "tasks" / "todo.md"
    return cand if cand.exists() else None


def missing_sections(content: str) -> list[str]:
    return [s for s in REQUIRED_SECTIONS if f"## {s}" not in content]


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    prompt = data.get("prompt") or ""
    if not (_DELEGATION_KEYWORD.search(prompt) or _mentions_domain_agent(prompt)):
        return 0

    todo = find_todo()
    if todo is None:
        msg = (
            "[delegation-due-diligence] 위임 키워드 감지 — tasks/todo.md 가 없다. "
            "위임 전 5섹션(영향 파일/USER_DECISIONS/CONSTRAINTS/기술 충돌 점검/fallback) "
            "todo.md 작성 후 Plan subagent 외부 검증 필수."
        )
    else:
        missing = missing_sections(todo.read_text(encoding="utf-8", errors="ignore"))
        if not missing:
            return 0
        msg = (
            f"[delegation-due-diligence] tasks/todo.md 5섹션 누락: {', '.join(missing)}. "
            "위임 전 보강 필수 (빈 섹션은 'N/A' 명시)."
        )

    advisory = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": msg,
        }
    }
    sys.stdout.write(json.dumps(advisory, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
