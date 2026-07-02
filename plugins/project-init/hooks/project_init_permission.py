#!/usr/bin/env python3
"""PermissionRequest hook — project-init 진행 중 권한 자동 승인.

출력 채널: 결정 주입 (exit 0 + stdout hookSpecificOutput.decision JSON)
— PermissionRequest 의 공식 스키마는 hookSpecificOutput.decision.behavior 다.
  top-level permissionDecision 은 PreToolUse 전용 형태로 이 이벤트에선 무효
  (v1.30.0 채널 교정 — hooks.md PermissionRequest 스펙 기준).

신호 파일: 프로젝트 루트의 `.claude/state/.init_in_progress`.
이 파일이 존재하고 & 생성된 지 INIT_TTL_SECONDS 이내인 동안
_AUTO_APPROVE_TOOLS(Write, Edit, MultiEdit, Bash, Read, Glob, Grep)를 자동 승인한다.

⚠️ 신호 파일을 공유 /tmp 고정 경로에 두던 과거 설계의 3중 위험을 제거한다:
  1. 공유 위치(world-writable /tmp) — 같은 머신의 다른 프로세스/사용자가 고정 이름
     파일을 만들면 자동승인이 켜졌다. → 프로젝트 `.claude/state/` 로 이동(소유자만 쓰기,
     프로젝트 스코프라 다른 프로젝트로 새지 않음).
  2. 프로젝트 바인딩 없음 — /tmp 신호 하나가 모든 프로젝트 세션을 자동승인했다.
     → 훅이 CLAUDE_PROJECT_DIR(없으면 stdin cwd) 기준으로 그 프로젝트의 신호만 본다.
  3. stale 영구 자동승인 — init 스킬이 크래시해 파일을 못 지우면 이후 영구 자동승인.
     → mtime TTL 로 만료(만료 시 self-heal 삭제 + 승인 안 함, fail-safe).

흐름:
  1. project-init 스킬이 시작 시 `.claude/state/.init_in_progress` 생성 (승인 1회)
  2. 이후 파일 생성·Bash 명령은 TTL 내에서 이 훅이 자동 승인
  3. project-init 완료 후 신호 파일 삭제 → 정상 모드 복귀
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

_SIGNAL_REL = ".claude/state/.init_in_progress"
_AUTO_APPROVE_TOOLS = {"Write", "Edit", "MultiEdit", "Bash", "Read", "Glob", "Grep"}
# init 은 사용자 응답(AskUserQuestion) 대기로 길어질 수 있으나, stale 노출을 이 시간으로
# 상한한다. 만료되면 자동승인이 멈추고 일반 권한 흐름으로 복귀한다(fail-safe 방향).
INIT_TTL_SECONDS = 60 * 60  # 60분


def _project_root(data: dict) -> Path:
    """작업 대상 프로젝트 루트. CLAUDE_PROJECT_DIR 우선, 없으면 stdin cwd, 그다음 cwd."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    cwd = data.get("cwd")
    if isinstance(cwd, str) and cwd:
        return Path(cwd)
    return Path.cwd()


def _signal_fresh(signal: Path) -> bool:
    """신호 파일이 존재하고 TTL 이내면 True. 만료 시 self-heal 삭제 후 False."""
    try:
        age = time.time() - signal.stat().st_mtime
    except OSError:
        return False
    if age <= INIT_TTL_SECONDS:
        return True
    # stale — init 크래시로 남은 고아. 자동승인 방지 + 흔적 정리(best-effort).
    try:
        signal.unlink()
    except OSError:
        pass
    return False


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    tool_name = data.get("tool_name", "")
    if tool_name not in _AUTO_APPROVE_TOOLS:
        return 0

    signal = _project_root(data) / _SIGNAL_REL
    if not _signal_fresh(signal):
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
