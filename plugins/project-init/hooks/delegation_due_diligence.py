#!/usr/bin/env python3
"""UserPromptSubmit 훅 — 사용자 위임 요청 감지 시 tasks/todo.md 5섹션 점검.

matcher: 전역 (UserPromptSubmit)

동작 단계:
1. 사용자 prompt 에서 위임 키워드 정규식 매칭 (@backend, @frontend, @deeplearning, @ai, 위임, 맡겨)
2. 미매칭이면 silent exit 0 (일반 대화 통과)
3. tasks/todo.md 가 없으면 stderr + exit 2 (메인이 보강하도록 유도)
4. todo.md 에 5섹션(영향 파일/USER_DECISIONS/CONSTRAINTS/기술 충돌 점검/fallback) 헤더 존재 확인
5. 누락 섹션이 있으면 stderr + exit 2

한계: 정규식 기반이라 섹션 헤더 존재만 점검한다. 내용 충분성은
CLAUDE.md/workflow.md 의 "위임 전 due diligence" 가 Plan subagent 외부 검증으로 보완한다.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

DELEGATION_PATTERN = re.compile(r"@(backend|frontend|deeplearning|ai|infra)\b|위임|맡겨")
REQUIRED_SECTIONS: tuple[str, ...] = (
    "영향 파일",
    "USER_DECISIONS",
    "CONSTRAINTS",
    "기술 충돌 점검",
    "fallback",
)


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
    if not DELEGATION_PATTERN.search(prompt):
        return 0

    todo = find_todo()
    if todo is None:
        print(
            "[delegation-due-diligence] 위임 키워드 감지 — tasks/todo.md 가 없다. "
            "위임 전 5섹션(영향 파일/USER_DECISIONS/CONSTRAINTS/기술 충돌 점검/fallback) "
            "todo.md 작성 후 Plan subagent 외부 검증 필수.",
            file=sys.stderr,
        )
        return 2

    missing = missing_sections(todo.read_text(encoding="utf-8"))
    if missing:
        print(
            f"[delegation-due-diligence] tasks/todo.md 5섹션 누락: {', '.join(missing)}. "
            "위임 전 보강 필수 (빈 섹션은 'N/A' 명시).",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
