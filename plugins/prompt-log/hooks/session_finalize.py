#!/usr/bin/env python3
# [prompt-log] removable plugin — see plugins/prompt-log/README.md
"""SessionEnd 훅 — 마지막 active prompt를 finalize/flush.

출력 채널: 사용자전용 (exit 0 + stderr — flush 실패 경고만. 평시 무출력)

동작:
1. 동의 검사. 미동의면 exit 0
2. active record 있으면 finalize(ended_by="session_end") → 월별 jsonl append
3. active state 파일 삭제
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prompt_log_lib as pl  # noqa: E402

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def main() -> int:
    try:
        json.load(sys.stdin)  # input은 사용 안 함
    except Exception:
        pass

    root = pl.pl_find_project_root()
    if root is None or not pl.pl_is_consented(root):
        return 0

    active = pl.pl_load_active(root)
    if active is None:
        return 0

    record = pl.pl_finalize_record(active, root, ended_by="session_end")
    try:
        pl.pl_append_record(record)
    except Exception as e:
        sys.stderr.write(f"[prompt-log] flush 실패: {e}\n")
    finally:
        pl.pl_clear_active(root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
