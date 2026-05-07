#!/usr/bin/env python3
"""UserPromptSubmit hook — 시간 관련 키워드 감지 시 현재 KST 시각을 컨텍스트에 주입.

Claude는 학습 데이터 기준 시간만 알고 실시간 시각을 모른다.
이 훅이 시스템 시간을 읽어 exit 2로 주입하면 Claude가 정확한 KST 기준으로 대화한다.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

_TIME_PAT = re.compile(
    r"(시간|시각|날짜|오늘|지금|현재|내일|어제|언제|몇\s*시|요일|이번\s*주|지난\s*주|다음\s*주"
    r"|time|date|today|now|current\s+time|yesterday|tomorrow|when)",
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

    try:
        kst = subprocess.check_output(
            ["date", "+%Y-%m-%d(%a) %H:%M:%S KST"],
            env={**os.environ, "TZ": "Asia/Seoul"},
            text=True,
        ).strip()
    except Exception:
        return 0

    sys.stderr.write(
        f"[time-context] 현재 시각: {kst}\n"
        "시간 관련 질문에는 반드시 위 KST 기준 시각을 사용하세요. 학습 데이터 기준 시간 추정 금지.\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
