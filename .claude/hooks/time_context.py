#!/usr/bin/env python3
"""UserPromptSubmit hook — 시간 관련 키워드 감지 시 현재 KST 시각을 컨텍스트에 주입.

출력 채널: 환기 (exit 0 + stdout hookSpecificOutput.additionalContext JSON)

Claude는 학습 데이터 기준 시간만 알고 실시간 시각을 모른다.
이 훅이 시스템 시간을 읽어 additionalContext 로 주입하면 Claude가 정확한 KST 기준으로 대화한다.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# KST(한국 표준시)는 DST 없이 항상 UTC+9 고정 — 외부 `date` 명령(Unix 전용) 대신
# 순수 파이썬 고정 오프셋으로 계산해 Windows 포함 모든 OS 에서 동일 동작.
_KST = timezone(timedelta(hours=9))

_TIME_PAT = re.compile(
    # 명확한 시간 단어 (단독으로도 시간 질문)
    r"(시각|날짜|오늘|내일|어제|언제|몇\s*시|요일|이번\s*주|지난\s*주|다음\s*주"
    # '지금/현재'는 시간 단어와 결합한 경우만 (단독은 false positive 많음)
    r"|지금\s*(몇|시간|시각|날짜)"
    r"|현재\s*(시각|시간|날짜)"
    # 영어
    r"|today|yesterday|tomorrow|what\s+time|current\s+time)",
    re.IGNORECASE,
)


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    prompt = data.get("prompt") or ""
    if not _TIME_PAT.search(prompt):
        return 0

    kst = datetime.now(_KST).strftime("%Y-%m-%d(%a) %H:%M:%S KST")

    payload = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                f"[time-context] 현재 시각: {kst}. "
                "시간 관련 질문에는 반드시 이 KST 기준 시각을 사용하세요. "
                "학습 데이터 기준 시간 추정 금지."
            ),
        }
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
