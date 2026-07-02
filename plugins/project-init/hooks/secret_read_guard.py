#!/usr/bin/env python3
"""PreToolUse hook (Read|Grep) — 비밀 파일 읽기 차단.

역할: Claude가 Read·Grep 도구로 시크릿/인증 파일 내용을 노출하는 것을 방지한다.
      Grep 의 content 출력 모드는 Read 와 동등한 노출 경로이므로 함께 막는다.
동작:
  1. stdin JSON 에서 대상 경로 추출 (Read.file_path / Grep.path)
  2. 파일명/확장자를 비밀 파일 패턴과 대조
  3. 허용 예외(.env.example 등) 확인
  4. 위험 파일이면 exit 2 + stderr (차단)
  5. 안전하면 exit 0 (통과)

출력 채널: 차단
"""
from __future__ import annotations

import fnmatch
import json
import re
import sys
from pathlib import PurePosixPath

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

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
    # secrets.<민감확장자>만 비밀로 본다 (아래 접두사 "secrets." 는 secrets.md·
    # secrets.txt 같은 문서까지 차단하는 오탐이라 정확 이름으로 대체).
    "secrets.yaml",
    "secrets.yml",
    "secrets.json",
    "secrets.toml",
}

# 파일명이 이 접두사로 시작하면 차단 (대소문자 무시)
SECRET_PREFIX_PATTERNS: list[str] = [
    ".env.",          # .env.local, .env.production, ...
    "service_account",  # service_account.json, service_account_key.json
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

# 경로에 이 디렉토리 컴포넌트가 있으면 차단 — Grep path=~/.ssh 처럼 디렉토리
# 통째 탐색으로 그 안의 비밀(id_rsa 등) 내용이 content 모드에 노출되는 우회 차단.
SENSITIVE_DIRS: set[str] = {".ssh", ".gnupg", ".aws", ".gcloud", ".azure", ".kube"}


def _is_allowed(name_lower: str) -> bool:
    """안전 예외(공개키·.env.example·*.sample 등)면 True (false positive 방지)."""
    if name_lower.endswith(".pub") or _ALLOW_ENV_RE.match(name_lower):
        return True
    return any(name_lower.endswith(s) for s in ALLOW_SUFFIXES)


def _is_secret_file(file_path: str) -> tuple[bool, str]:
    """(차단여부, 사유) 반환."""
    # Windows 백슬래시 경로(C:\Users\x\.ssh\id_rsa)를 정규화 — PurePosixPath 는 `\` 를
    # 구분자로 보지 않아 정규화 없이는 basename 추출·디렉토리 검사가 통째로 무력화된다.
    name = PurePosixPath(file_path.replace("\\", "/")).name
    name_lower = name.lower()

    if _is_allowed(name_lower):
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

    # *.env (prod.env, local.env 등) — 접두사 무관 .env 류는 비밀
    # (dangerous_bash_check 의 _SECRET_FILES 와 동일 정책)
    if name_lower.endswith(".env"):
        return True, f"비밀 파일 읽기 차단: {name}"

    return False, ""


def _hits_sensitive_dir(file_path: str) -> bool:
    """경로 컴포넌트에 민감 디렉토리(.ssh 등)가 있으면 True (디렉토리 탐색 우회 차단)."""
    parts = [p.lower() for p in PurePosixPath(file_path.replace("\\", "/")).parts]
    return any(p in SENSITIVE_DIRS for p in parts)


# 대표 비밀 파일명 — glob 이 이 중 하나라도 매칭하면 "비밀 겨냥"으로 본다.
_REP_SECRETS: set[str] = (
    {n.lower() for n in SECRET_EXACT_NAMES}
    | {".env.local", "prod.env", "service_account.json"}
    | {f"x{ext}" for ext in SECRET_EXTENSIONS}
)


def _glob_targets_secret(glob: str) -> bool:
    """Grep glob 파라미터가 비밀 파일을 겨냥하면 True (.env*, *.pem, id_* 등).

    과거: `*`/`?` 를 지운 잔여 문자열만 비밀 판정 → `id_*`(→"id_")·`.en*`(→".en")
    처럼 core 가 비밀 이름과 정확히 안 맞으면 통과(id_rsa·.env 노출)했다. 이제 대표
    비밀 파일명을 glob 에 fnmatch 로 대조해, glob 이 실제로 비밀을 매칭하면 차단한다.
    단 너무 넓은 글롭(`*` 등, 리터럴 core<3)은 특정 겨냥이 아니라고 보고 통과시킨다.
    """
    core = glob.replace("*", "").replace("?", "")
    if len(core) < 3:
        return False  # `*`·`??` 등 광역 글롭은 비밀 특정 겨냥으로 보지 않음
    g = glob.lower()
    if any(fnmatch.fnmatch(rep, g) for rep in _REP_SECRETS):
        return True
    cl = core.lower()
    return cl.endswith(".env") or any(cl.endswith(ext) for ext in SECRET_EXTENSIONS)


def _evaluate(tool_input: dict) -> tuple[bool, str, str]:
    """(차단여부, 사유, 표시대상) 반환. Read 는 file_path, Grep 은 path(+glob).

    비밀 파일 직접 지목 / 민감 디렉토리 탐색 / glob 으로 비밀 겨냥 — 셋 다 차단.
    """
    file_path = tool_input.get("file_path") or tool_input.get("path") or ""
    glob = tool_input.get("glob") or ""
    if file_path:
        blocked, reason = _is_secret_file(file_path)
        if blocked:
            return True, reason, file_path
        if _hits_sensitive_dir(file_path):
            return True, f"민감 디렉토리 접근 차단: {file_path}", file_path
    if glob and _glob_targets_secret(glob):
        return True, f"비밀 파일 glob 차단: {glob}", glob
    return False, "", file_path or glob


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    if data.get("tool_name", "") not in ("Read", "Grep"):
        return 0

    blocked, reason, target = _evaluate(data.get("tool_input") or {})
    if not blocked:
        return 0

    sys.stderr.write("\n".join([
        "",
        DIVIDER,
        f"[secret-read-guard] 🔒 {reason}",
        DIVIDER,
        "",
        f"  대상: {target}",
        "",
        "시크릿/인증 파일은 Claude가 읽을 수 없습니다.",
        "필요하다면 사용자가 직접 터미널에서 확인하세요.",
        DIVIDER,
        "",
    ]))
    return 2


if __name__ == "__main__":
    sys.exit(main())
