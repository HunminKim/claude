#!/usr/bin/env python3
"""PreToolUse hook (Bash) — 위험한 Bash 명령 차단.

동작:
  1. stdin JSON에서 tool_input.command 추출
  2. 파괴적 패턴 감지 (rm -rf /, find / -delete 등)
  3. 핵심 운영 파일 직접 삭제 감지
  4. 위험 감지 시 exit 2 (차단 + 사유 출력)
  5. 안전한 명령은 exit 0 (통과)
"""
from __future__ import annotations

import json
import re
import sys

DIVIDER = "━" * 55

# 즉시 차단 — 복구 불가 수준의 파괴적 명령
HARD_BLOCK_PATTERNS: list[tuple[str, str]] = [
    (r"rm\s+-[a-z]*r[a-z]*f[a-z]*\s+/(?:\s|$)", "rm -rf / (루트 전체 삭제)"),
    (r"rm\s+-[a-z]*f[a-z]*r[a-z]*\s+/(?:\s|$)", "rm -rf / (루트 전체 삭제)"),
    (r"rm\s+-rf\s+/workspace\b", "rm -rf /workspace (작업 디렉토리 전체 삭제)"),
    (r"rm\s+-rf\s+~/", "rm -rf ~/ (홈 디렉토리 전체 삭제)"),
    (r"find\s+/\s+.*-delete\b", "find / -delete (루트 전체 탐색 삭제)"),
    (r"find\s+/\s+.*-exec\s+rm\b", "find / -exec rm (루트 전체 삭제 실행)"),
    (r":\s*\(\s*\)\s*\{.*:\|:.*\}", "Fork bomb"),
    (r"dd\s+.*of=/dev/(sd|nvme|vd)[a-z]", "dd → 블록 디바이스 직접 덮어쓰기"),
    (r"mkfs\.", "파일시스템 포맷"),
]

# 경고 후 차단 — 핵심 운영 파일 직접 삭제
PROTECTED_FILES = [
    "docker-compose.yml", "docker-compose.yaml",
    "docker-compose*.yml", "docker-compose*.yaml",
    "Dockerfile", ".env", "Makefile",
    "requirements.txt", "pyproject.toml",
]
_PROTECTED_RE = re.compile(
    r"(?:^|&&|\|;|\s)rm\s+[^;|&\n]*"
    r"(?:docker-compose[\w.-]*\.ya?ml|Dockerfile[\w.-]*|\.env[\w.-]*"
    r"|requirements\.txt|pyproject\.toml|Makefile)\b",
    re.IGNORECASE,
)


def _check(command: str) -> tuple[bool, str]:
    """(차단여부, 사유) 반환."""
    for pattern, reason in HARD_BLOCK_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return True, reason
    if _PROTECTED_RE.search(command):
        return True, "핵심 운영 파일 직접 삭제 감지"
    return False, ""


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    tool_name = data.get("tool_name", "")
    if tool_name != "Bash":
        return 0

    command: str = (data.get("tool_input") or {}).get("command", "") or ""
    if not command:
        return 0

    blocked, reason = _check(command)
    if not blocked:
        return 0

    sys.stderr.write("\n".join([
        "",
        DIVIDER,
        f"[dangerous-bash] 🚨 위험한 명령 차단: {reason}",
        DIVIDER,
        "",
        f"  명령: {command[:120]}{'...' if len(command) > 120 else ''}",
        "",
        "이 명령은 데이터를 복구 불가능하게 손실시킬 수 있습니다.",
        "정말 필요하다면 사용자가 직접 터미널에서 실행하세요.",
        DIVIDER,
        "",
    ]))
    return 2


if __name__ == "__main__":
    sys.exit(main())
