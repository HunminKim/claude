#!/usr/bin/env python3
"""PreToolUse hook (Bash) — 위험한 Bash 명령 차단.

동작:
  1. stdin JSON에서 tool_input.command 추출
  2. 파괴적 패턴 감지 (rm -rf /, find / -delete 등)
  3. 핵심 운영 파일 직접 삭제 감지
  4. 비밀 파일 내용 출력 명령 감지 (cat/head/tail .env 등)
  5. 인라인 토큰/시크릿 감지 (ghp_, sk-ant-, AKIA 등)
  6. 위험 감지 시 exit 2 (차단 + 사유 출력)
  7. 안전한 명령은 exit 0 (통과)

출력 채널: 차단
"""
from __future__ import annotations

import json
import os
import re
import sys

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

DIVIDER = "━" * 55

# 즉시 차단 — 복구 불가 수준의 파괴적 명령
HARD_BLOCK_PATTERNS: list[tuple[str, str]] = [
    # rm -rf / 또는 /* (루트 전체) — 플래그 순서·결합 무관, /* glob 포함
    (r"rm\s+-[a-z]*r[a-z]*f[a-z]*\s+/(?:\*|\s|$)", "rm -rf / (루트 전체 삭제)"),
    (r"rm\s+-[a-z]*f[a-z]*r[a-z]*\s+/(?:\*|\s|$)", "rm -rf / (루트 전체 삭제)"),
    # rm -rf ~ (홈 전체) — 트레일링 슬래시 유무·플래그 순서 무관
    (r"rm\s+-[a-z]*r[a-z]*f[a-z]*\s+~(?:/|\*|\s|$)", "rm -rf ~ (홈 디렉토리 전체 삭제)"),
    (r"rm\s+-[a-z]*f[a-z]*r[a-z]*\s+~(?:/|\*|\s|$)", "rm -rf ~ (홈 디렉토리 전체 삭제)"),
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

# ── Layer: 비밀 파일 내용 노출 명령 (fail-closed 다중 경로 탐지) ──────────
#
# 블랙리스트(cat/head/tail)만으로는 grep·awk·sed·source·redirect·interpreter·
# cp 등 수십 가지 우회가 뚫린다. "읽기로 의심되면 차단"으로 설계한다.
# 정적 검사로 100% 막을 수는 없다(예: cp .env x 후 별도 명령으로 cat x —
# rename/copy 후 읽기는 본질적으로 사후 탐지 불가) → .gitignore + OS 권한이
# 최종 방어선. 이 훅은 "한 명령 안에서의 직접 노출"을 광범위하게 막는다.

_SECRET_FILES = (
    # .env 계열 — 단, 안전 템플릿(.example/.sample/.template/.dist)은 허용
    # (secret_read_guard 의 _ALLOW_ENV_RE 와 동일 정책)
    r"(?:\.env(?:\.local|\.production|\.development|\.staging|\.prod|\.dev)?"
    r"(?![\w.-]*\.(?:example|sample|template|dist)\b))"
    r"|(?:\.netrc|\.pgpass|\.npmrc|\.pypirc)"
    r"|(?:id_rsa|id_ed25519|id_ecdsa|id_dsa)"
    r"|(?:credentials\.json|token\.json|secrets\.(?:ya?ml|json|toml))"
    r"|(?:service_account[\w.-]*\.json)"
    r"|(?:[\w.-]*\.(?:pem|key|p12|pfx|jks|keystore))"
)
_SECRET_FILE_PAT = rf"(?:{_SECRET_FILES})\b"

# 명령 경계: 줄 시작·연결자·공백뿐 아니라 따옴표·괄호 뒤도 포함한다.
# `bash -c 'cat .env'` 처럼 인터프리터 인용 안에 든 명령을 놓치지 않기 위함.
_BND = r"(?:^|&&|\|\||\||;|\s|['\"(])"

# 1) 내용을 stdout 으로 흘리는 리더 명령 (대폭 확장)
_READ_CMDS = (
    r"(?:cat|tac|nl|head|tail|less|more|bat|batcat|grep|egrep|fgrep|zgrep|rg|ag|"
    r"awk|gawk|sed|cut|sort|uniq|rev|xxd|od|hexdump|hd|strings|base64|base32|"
    r"column|fold|fmt|paste|comm|join|diff|look|pr|expand|unexpand|"
    r"view|vi|vim|nano|emacs|pico|ex)"
)
_SECRET_READ_RE = re.compile(
    rf"{_BND}{_READ_CMDS}\s+[^;|&\n]*{_SECRET_FILE_PAT}",
    re.IGNORECASE,
)
# 2) 셸 sourcing: `source .env`, `. .env` (env 변수로 로드 후 echo 노출 가능)
_SECRET_SOURCE_RE = re.compile(
    rf"{_BND}(?:source|\.)\s+[^;|&\n]*{_SECRET_FILE_PAT}",
    re.IGNORECASE,
)
# 3) 입력 리다이렉트: `cmd < .env`, `$(< .env)`
_SECRET_REDIR_RE = re.compile(rf"<\s*[^\s;|&<>]*{_SECRET_FILE_PAT}", re.IGNORECASE)
# 4) 복사/이동/전송 시 비밀 파일이 첫 인자(소스)로 — exfil 경로
_SECRET_COPY_RE = re.compile(
    rf"{_BND}(?:cp|mv|scp|rsync|install|dd\s+if=)\s*"
    rf"(?:-\S+\s+)*[^\s;|&]*{_SECRET_FILE_PAT}",
    re.IGNORECASE,
)
# 5) 인터프리터 인라인 코드(-c/-e)가 비밀 파일을 언급 — open()/File.read 노출
_INTERP_EVAL_RE = re.compile(
    r"(?:python3?|node|deno|bun|ruby|perl|php)\b[^\n]*\s-(?:c|e)\b", re.IGNORECASE
)
_SECRET_ANY_RE = re.compile(_SECRET_FILE_PAT, re.IGNORECASE)


def _reads_secret(command: str) -> bool:
    """명령이 비밀 파일 내용을 노출할 가능성이 있으면 True (fail-closed)."""
    if _SECRET_READ_RE.search(command):
        return True
    if _SECRET_SOURCE_RE.search(command):
        return True
    if _SECRET_REDIR_RE.search(command):
        return True
    if _SECRET_COPY_RE.search(command):
        return True
    if _INTERP_EVAL_RE.search(command) and _SECRET_ANY_RE.search(command):
        return True
    return False

# ── Layer: 인라인 토큰/시크릿 감지 ──────────────────────

INLINE_TOKEN_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 순서: 구체적 패턴 → 일반 패턴 (sk-ant- 가 sk- 보다 먼저)
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "Anthropic API 키"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "OpenAI API 키"),
    (re.compile(r"ghp_[A-Za-z0-9]{30,}"), "GitHub PAT"),
    (re.compile(r"ghs_[A-Za-z0-9]{30,}"), "GitHub Secret"),
    (re.compile(r"gho_[A-Za-z0-9]{30,}"), "GitHub OAuth 토큰"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{30,}"), "GitHub Fine-grained PAT"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS Access Key"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9\-]+"), "Slack 토큰"),
    (re.compile(r"https?://[^\s:]+:[^\s@]+@"), "URL 내장 인증정보"),
]


def _deletes_project_root(command: str) -> bool:
    """rm -rf 가 작업 디렉토리 루트(CLAUDE_PROJECT_DIR) 전체를 지우면 True.

    특정 환경 경로(/workspace 등)를 박는 대신 런타임 작업 디렉토리와 동적 비교한다 —
    어떤 머신에 배포돼도 그 환경의 프로젝트 루트 통째 삭제를 막는다.
    """
    root = os.environ.get("CLAUDE_PROJECT_DIR", "").rstrip("/")
    if not root:
        return False
    pat = rf"rm\s+-[a-z]*r[a-z]*f[a-z]*\s+{re.escape(root)}(?:/+)?(?:\s|$)"
    return bool(re.search(pat, command))


def _check(command: str) -> tuple[bool, str]:
    """(차단여부, 사유) 반환."""
    for pattern, reason in HARD_BLOCK_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return True, reason
    if _deletes_project_root(command):
        return True, "작업 디렉토리(CLAUDE_PROJECT_DIR) 전체 삭제 감지"
    if _PROTECTED_RE.search(command):
        return True, "핵심 운영 파일 직접 삭제 감지"
    if _reads_secret(command):
        return True, "비밀 파일 내용 노출 가능 명령 감지"
    for pat, label in INLINE_TOKEN_PATTERNS:
        if pat.search(command):
            return True, f"인라인 시크릿 감지 ({label})"
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
