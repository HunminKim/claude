#!/usr/bin/env python3
"""PreToolUse hook (Read) — 비밀 파일 읽기 차단.

역할: Claude가 Read 도구로 시크릿/인증 파일을 직접 읽는 것을 방지한다.
동작:
  1. stdin JSON에서 tool_input.file_path 추출
  2. 파일명/확장자를 비밀 파일 패턴과 대조
  3. 허용 예외(.env.example 등) 확인
  4. 위험 파일이면 exit 2 + stderr (차단)
  5. 안전하면 exit 0 (통과)

출력 채널: 차단
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import PurePosixPath

DIVIDER = "━" * 55

# ── 차단 대상 파일명 패턴 ──────────────────────────────────

# 정확한 파일명 매치 (대소문자 무시)
SECRET_EXACT_NAMES: set[str] = {
    ".env",
    ".netrc",
    ".pgpass",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "token.json",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
}

# 파일명이 이 접두사로 시작하면 차단 (대소문자 무시)
SECRET_PREFIX_PATTERNS: list[str] = [
    ".env.",          # .env.local, .env.production, ...
    "service_account",  # service_account.json, service_account_key.json
    "secrets.",       # secrets.yaml, secrets.json, ...
]

# 확장자 매치 (대소문자 무시)
SECRET_EXTENSIONS: set[str] = {
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".jks",
    ".keystore",
}

# ── 허용 예외 (false positive 방지) ───────────────────────

ALLOW_SUFFIXES: list[str] = [
    ".example",
    ".sample",
    ".template",
    ".dist",
]

ALLOW_EXTENSIONS: set[str] = {
    ".pub",  # 공개키는 안전
}

# .env 뒤에 example/sample/template/dist 가 붙은 형태
_ALLOW_ENV_RE = re.compile(
    r"^\.env\.(example|sample|template|dist)$", re.IGNORECASE,
)


def _is_secret_file(file_path: str) -> tuple[bool, str]:
    """(차단여부, 사유) 반환."""
    name = PurePosixPath(file_path).name
    name_lower = name.lower()

    # 허용 예외 먼저 검사
    if name_lower.endswith(".pub"):
        return False, ""
    if _ALLOW_ENV_RE.match(name_lower):
        return False, ""
    for suffix in ALLOW_SUFFIXES:
        if name_lower.endswith(suffix):
            return False, ""

    # 정확한 파일명 매치
    if name_lower in SECRET_EXACT_NAMES:
        return True, f"비밀 파일 읽기 차단: {name}"

    # 접두사 매치
    for prefix in SECRET_PREFIX_PATTERNS:
        if name_lower.startswith(prefix):
            return True, f"비밀 파일 읽기 차단: {name}"

    # 확장자 매치
    suffix = PurePosixPath(name_lower).suffix
    if suffix in SECRET_EXTENSIONS:
        return True, f"비밀 파일 읽기 차단 ({suffix}): {name}"

    return False, ""


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    tool_name = data.get("tool_name", "")
    if tool_name != "Read":
        return 0

    file_path: str = (data.get("tool_input") or {}).get("file_path", "") or ""
    if not file_path:
        return 0

    blocked, reason = _is_secret_file(file_path)
    if not blocked:
        return 0

    sys.stderr.write("\n".join([
        "",
        DIVIDER,
        f"[secret-read-guard] 🔒 {reason}",
        DIVIDER,
        "",
        f"  경로: {file_path}",
        "",
        "시크릿/인증 파일은 Claude가 읽을 수 없습니다.",
        "필요하다면 사용자가 직접 터미널에서 확인하세요.",
        DIVIDER,
        "",
    ]))
    return 2


if __name__ == "__main__":
    sys.exit(main())
