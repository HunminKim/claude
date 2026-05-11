#!/usr/bin/env python3
"""UserPromptSubmit hook — 버그 보고 감지 시 'Think Before Fixing' 체크리스트 주입.

동작:
  1. stdin JSON에서 prompt 추출
  2. 버그 키워드 검사 (한영 혼재)
  3. 자명한 오탈자·누락 키워드 있으면 허용 (즉시 수정 예외)
  4. 버그 키워드 O + 오탈자 X → exit 2 + stderr 체크리스트 주입
"""
from __future__ import annotations

import json
import re
import sys

BUG_PATTERNS = [
    r"버그",
    r"오류",
    r"에러",
    r"안\s*돼",
    r"안\s*됨",
    r"실패",
    r"\bbug\b",
    r"\berror\b",
    r"\bbroken\b",
    r"\bfailing\b",
    r"작동\s*안",
    r"동작\s*안",
    r"crash",
    r"깨짐",
]

# 원인과 범위가 자명한 경우 — 즉시 수정 허용
TRIVIAL_PATTERNS = [
    r"오탈자",
    r"typo",
    r"스펠",
    r"import\s*(누락|missing)",
    r"missing\s*import",
    r"들여쓰기",
    r"indentation",
]

DIVIDER = "━" * 55


def _matches(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    prompt: str = data.get("prompt", "") or ""
    if not prompt:
        return 0

    # 버그 증상 기술 없이 수정 명령만 있으면 통과 (기능 요청·질문)
    if not _matches(prompt, BUG_PATTERNS):
        return 0

    if _matches(prompt, TRIVIAL_PATTERNS):
        return 0

    msg = "\n".join([
        "",
        DIVIDER,
        "[THINK BEFORE FIXING] 버그 보고 감지 — 즉시 수정 전 필수 확인",
        DIVIDER,
        "",
        "수정 전 반드시 아래 세 가지를 먼저 보고할 것:",
        "  1. 추정 원인  (어느 레이어, 어떤 코드)",
        "  2. 수정 범위  (건드릴 파일·함수)",
        "  3. 불확실한 점 (재현 조건·기대 동작 중 불명확한 것)",
        "",
        "세 항목이 명확할 때만 수정 진행.",
        "불명확하면 'Assumption: ...' 으로 가정을 명시한 뒤 확인 요청.",
        "",
        "예외(즉시 수정 가능): 오탈자·import 누락처럼 원인과 범위가 자명한 것.",
        DIVIDER,
        "",
    ])
    sys.stderr.write(msg)
    return 2


if __name__ == "__main__":
    sys.exit(main())
