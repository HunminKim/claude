#!/usr/bin/env python3
"""PermissionRequest hook — project-init 진행 중 권한 자동 승인.

/tmp/.claude_init_in_progress 신호 파일이 존재하는 동안
Write, Edit, MultiEdit, Bash, Read 작업을 자동 승인한다.

흐름:
  1. project-init 스킬이 시작 시 touch /tmp/.claude_init_in_progress (승인 1회)
  2. 이후 모든 파일 생성·Bash 명령은 이 훅이 자동 승인
  3. project-init 완료 후 신호 파일 삭제 → 정상 모드 복귀
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SIGNAL_FILE = Path("/tmp/.claude_init_in_progress")
_AUTO_APPROVE_TOOLS = {"Write", "Edit", "MultiEdit", "Bash", "Read", "Glob", "Grep"}


def main() -> int:
    if not _SIGNAL_FILE.exists():
        return 0

    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    tool_name = data.get("tool_name", "")
    if tool_name not in _AUTO_APPROVE_TOOLS:
        return 0

    result = {
        "permissionDecision": "allow",
        "reason": "project-init 진행 중 자동 승인",
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
