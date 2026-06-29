#!/usr/bin/env python3
"""PermissionRequest hook — project-init 진행 중 권한 자동 승인.

출력 채널: 결정 주입 (exit 0 + stdout hookSpecificOutput.decision JSON)
— PermissionRequest 의 공식 스키마는 hookSpecificOutput.decision.behavior 다.
  top-level permissionDecision 은 PreToolUse 전용 형태로 이 이벤트에선 무효
  (v1.30.0 채널 교정 — hooks.md PermissionRequest 스펙 기준).

시스템 임시 디렉토리(tempfile.gettempdir())의 .claude_init_in_progress 신호 파일이
존재하는 동안 Write, Edit, Bash, Read 작업을 자동 승인한다.
(특정 OS 경로 /tmp 를 박지 않고 플랫폼 임시 경로를 사용 — SKILL.md 의
 ${TMPDIR:-/tmp} 와 동일 위치로 맞춘다.)

흐름:
  1. project-init 스킬이 시작 시 임시 디렉토리에 .claude_init_in_progress 생성 (승인 1회)
  2. 이후 모든 파일 생성·Bash 명령은 이 훅이 자동 승인
  3. project-init 완료 후 신호 파일 삭제 → 정상 모드 복귀
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

_SIGNAL_FILE = Path(tempfile.gettempdir()) / ".claude_init_in_progress"
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
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {
                "behavior": "allow",
                "message": "project-init 진행 중 자동 승인",
            },
        }
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
