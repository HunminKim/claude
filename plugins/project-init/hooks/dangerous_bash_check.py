#!/usr/bin/env python3
"""PreToolUse hook (Bash) — 위험한 Bash 명령 차단.

동작:
  1. stdin JSON에서 tool_input.command 추출
  2. 파괴적 패턴 감지 (rm -rf /, find / -delete 등)
  3. 핵심 운영 파일 직접 삭제 감지
  4. 비밀 파일 내용 출력 명령 감지 (cat/head/tail .env 등)
  5. 인라인 토큰/시크릿 감지 (ghp_, sk-ant-, AKIA 등)
  6. 위험 감지 시 exit 2 (차단 + 사유 출력)
  7. 워크스페이스(CLAUDE_PROJECT_DIR) 밖 파괴적 명령(rm/mv/shred/find -delete/
     `>` truncate 가 상위·형제 디렉토리 타겟) 감지 시 permissionDecision=ask 로 승격
     (변수·명령치환 타겟은 해석 불가 → fail-closed 로 ask)
  8. plan-gate 전이 CLI 호출(approve/done 등) 감지 시 permissionDecision=ask 로 승격
     (차단 아님 — 사용자 전용 결정을 Claude 가 Bash 로 우회 실행하는 것을 확인창으로 가드)
  9. 안전한 명령은 exit 0 (통과)

출력 채널: 차단 (위험 명령) / 권한 승격 (워크스페이스 밖 파괴 명령·plan-gate 전이 — exit 0 + permissionDecision=ask JSON)
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
# (rm 의 루트/홈 재귀 삭제는 플래그 순서·형태가 다양해 _rm_targets_root 토큰 판정 +
#  아래 substring 백스톱 정규식 둘 다로 판정 — 접두 래퍼·쿼팅 우회를 이중 방어)
HARD_BLOCK_PATTERNS: list[tuple[str, str]] = [
    # find / 또는 /* 루트 탐색 삭제 — `/` 뒤 공백 또는 `/*` glob 모두 커버(/home 등 하위는 제외)
    (r"find\s+/\*?\s+.*-delete\b", "find / -delete (루트 전체 탐색 삭제)"),
    (r"find\s+/\*?\s+.*-exec\s+rm\b", "find / -exec rm (루트 전체 삭제 실행)"),
    (r":\s*\(\s*\)\s*\{.*:\|:.*\}", "Fork bomb"),
    (r"dd\s+.*of=/dev/(sd|nvme|vd)[a-z]", "dd → 블록 디바이스 직접 덮어쓰기"),
    (r"mkfs\.", "파일시스템 포맷"),
    # rm -rf / ~ substring 백스톱 — 토큰 판정이 못 잡는 쿼팅(`sh -c 'rm -rf /'`)·
    # 이상 접두를 넓게 커버. 플래그 결합·순서 무관, `/` 뒤 공백/glob/끝/닫는따옴표만
    # 루트로 인정(닫는따옴표: `sh -c "rm -rf /"` 처럼 중첩 인용 안의 삭제까지 커버).
    (r"rm\s+-[a-z]*r[a-z]*f[a-z]*\s+/(?:\*|\s|$|['\"`])", "rm -rf / (루트 전체 삭제)"),
    (r"rm\s+-[a-z]*f[a-z]*r[a-z]*\s+/(?:\*|\s|$|['\"`])", "rm -rf / (루트 전체 삭제)"),
    (r"rm\s+-[a-z]*r[a-z]*f[a-z]*\s+~(?:/|\*|\s|$|['\"`])", "rm -rf ~ (홈 전체 삭제)"),
    (r"rm\s+-[a-z]*f[a-z]*r[a-z]*\s+~(?:/|\*|\s|$|['\"`])", "rm -rf ~ (홈 전체 삭제)"),
]

# rm 루트/홈 재귀 삭제 타겟 — /, //, /*, ~, ~/, $HOME, ${HOME} (하위 경로는 미매치)
_RM_ROOT_TARGET_RE = re.compile(r"^(?:/+|/\*|~|~/|\$HOME|\$\{HOME\})/*\*?$")

# rm 앞에 흔히 붙는 명령 래퍼 — 이들을 건너뛰고 실제 rm 토큰을 찾는다.
# (sudo -E rm·env rm·time rm·\rm 우회 방지 — 래퍼 미스킵이 F1 회귀의 원인)
_CMD_WRAPPERS = {
    "sudo", "doas", "env", "time", "nice", "ionice", "command",
    "exec", "builtin", "stdbuf", "nohup", "setsid",
}


def _find_cmd_index(toks: list[str], names: frozenset[str]) -> int:
    """세그먼트 토큰 목록에서 names 에 속한 실행 토큰의 인덱스. 없으면 -1.

    첫 토큰이 대상 명령(또는 `\\rm` 식 백슬래시 접두)이면 그 자리. 래퍼(sudo/env/time/...)면
    그 옵션·옵션값을 건너뛰며 대상 명령을 찾는다. 첫 토큰이 래퍼도 대상도 아니면 미해당.
    """
    if not toks:
        return -1
    first = os.path.basename(toks[0].lstrip("\\"))
    if first in names:
        return 0
    if first not in _CMD_WRAPPERS:
        return -1
    for i in range(1, len(toks)):
        if os.path.basename(toks[i].lstrip("\\")) in names:
            return i
        if toks[i].startswith("-"):
            continue  # 래퍼 옵션 (예: sudo -E, nice -n)
        # 비옵션 토큰(옵션값·VAR=x 등)은 건너뛰고 계속 대상 명령을 찾는다
    return -1


_RM_ONLY = frozenset({"rm"})


def _find_rm_index(toks: list[str]) -> int:
    """세그먼트 토큰 목록에서 실제 rm 실행 토큰의 인덱스. 없으면 -1."""
    return _find_cmd_index(toks, _RM_ONLY)


def _is_recursive_flag(tok: str) -> bool:
    """rm 인자 토큰이 재귀 플래그면 True (--recursive, -R, -rf/-fr 등 결합 short)."""
    if tok == "--recursive":
        return True
    if tok.startswith("--"):
        return False  # --force 등 — 재귀만으로 위험 판정
    return tok.startswith("-") and "r" in tok[1:].lower()


def _rm_targets_root(command: str) -> bool:
    """rm 이 루트(/)·홈(~/$HOME) 전체를 재귀 삭제하려 하면 True (플래그 순서·형태 무관).

    세그먼트별로 rm 호출(접두 래퍼 스킵 포함)을 찾아, 재귀 플래그(-r/-R/-rf 결합·
    --recursive)와 루트/홈 타겟이 함께 있으면 차단한다. 긴옵션(`--recursive --force /`)·
    `//`·`$HOME` 등 substring 정규식이 못 잡는 변형을 커버한다(정규식은 백스톱으로 병행).
    하위 경로(`rm -rf build/`)는 타겟 정규식이 배제.
    """
    for seg in re.split(r"[;&|\n]+", command):
        toks = seg.split()
        idx = _find_rm_index(toks)
        if idx < 0:
            continue
        # 인자 토큰의 감싼 따옴표 제거 — `rm -rf "/"`·`rm -rf "$HOME"` 처럼
        # 셸이 실행 시 벗겨내는 인용 타겟을 정규식이 놓치던 우회를 봉합.
        args = [t.strip("'\"") for t in toks[idx + 1:]]
        recursive = any(_is_recursive_flag(t) for t in args)
        targets = [t for t in args if not t.startswith("-")]
        if recursive and any(_RM_ROOT_TARGET_RE.match(t) for t in targets):
            return True
    return False

# 경고 후 차단 — 핵심 운영 파일 직접 삭제
PROTECTED_FILES = [
    "docker-compose.yml", "docker-compose.yaml",
    "docker-compose*.yml", "docker-compose*.yaml",
    "Dockerfile", ".env", "Makefile",
    "requirements.txt", "pyproject.toml",
]
# 끝에 `(?![\w.-])` — 파일명 경계. `\b` 는 "Makefile-backup"·"api.key.md" 처럼
# 하이픈/점으로 이어지는 무관 파일에서 성립해 오탐을 냈다(rm my-Makefile-backup 차단).
_PROTECTED_RE = re.compile(
    r"(?:^|&&|\|;|\s)rm\s+[^;|&\n]*"
    r"(?:docker-compose[\w.-]*\.ya?ml|Dockerfile[\w.-]*|\.env[\w.-]*"
    r"|requirements\.txt|pyproject\.toml|Makefile)(?![\w.-])",
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
# 끝에 `(?![\w.-])` — 파일명 경계(`\b` 대신). `\b` 는 "notes.env.md"·"api.key.md"
# 처럼 비밀 토큰 뒤에 `.확장자` 가 더 붙는 무관 문서 파일에서도 성립해 오탐을 냈다.
_SECRET_FILE_PAT = rf"(?:{_SECRET_FILES})(?![\w.-])"

# 명령 경계: 줄 시작·연결자·공백뿐 아니라 따옴표·괄호·백틱 뒤도 포함한다.
# `bash -c 'cat .env'`·`` `cat .env` `` 처럼 인용/명령치환 안에 든 명령을 놓치지 않기 위함.
_BND = r"(?:^|&&|\|\||\||;|\s|['\"(`])"

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
# 4) 복사/이동/링크/전송 시 비밀 파일이 소스로 — exfil 경로.
#    ln 포함: `ln -s .env leak` 후 leak 을 읽으면 secret_read_guard 도 우회된다.
_SECRET_COPY_RE = re.compile(
    rf"{_BND}(?:cp|mv|ln|scp|rsync|install|dd\s+if=)\s*"
    rf"(?:-\S+\s+)*[^\s;|&]*{_SECRET_FILE_PAT}",
    re.IGNORECASE,
)
# 4b) 아카이브/전송 도구 인자에 비밀 파일이 명시적으로 등장 — 한 명령 내 exfil.
#     tar/zip 로 묶거나 nc 로 내보내며 비밀 파일명을 직접 지목하는 경우.
#     ⚠️ curl/wget 은 여기서 제외 — URL 경로의 `foo.pem`(다운로드 목적지)까지 삼켜
#     `curl https://x/pubkey.pem` 같은 정상 다운로드를 오탐했다(F3). 아래 업로드 문맥
#     정규식으로만 잡는다.
_SECRET_EXFIL_RE = re.compile(
    rf"{_BND}(?:tar|zip|gzip|bzip2|xz|7z|nc|ncat|socat)\b"
    rf"[^\n]*{_SECRET_FILE_PAT}",
    re.IGNORECASE,
)
# 4c) curl/wget 은 비밀 파일이 실제 업로드 대상(=소스)일 때만 exfil 로 본다.
#     - `-T/--upload-file <secret>` : 파일이 플래그 바로 뒤 인자.
#     - `@<secret>` : -d/--data*/-F/--form 이 파일을 읽는 curl 문법(`@` 접두).
#     플래그 뒤 아무 데나(예: URL 경로 `.../server.pem`)의 확장자는 목적지라 삼키지
#     않는다 — `curl -d @payload https://x/certs/server.pem` 같은 정상 POST 오탐 방지.
_SECRET_UPLOAD_RE = re.compile(
    rf"(?:curl|wget)\b[^\n]*(?:"
    rf"(?:-T|--upload-file)\s+['\"]?{_SECRET_FILE_PAT}"
    rf"|@['\"]?{_SECRET_FILE_PAT}"
    rf")",
    re.IGNORECASE,
)
# 5) 인터프리터가 비밀 파일을 언급 — 인라인 코드(-c/-e)·stdin(`- `)·heredoc(<<) 모두.
#    `python3 - <<EOF ... open('.env')` 처럼 -c 없이 stdin/heredoc 로 읽는 우회 포함.
_INTERP_EVAL_RE = re.compile(
    r"(?:python3?|node|deno|bun|ruby|perl|php)\b[^\n]*(?:\s-(?:c|e)\b|\s-\s|\s-$|<<)",
    re.IGNORECASE,
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
    if _SECRET_EXFIL_RE.search(command):
        return True
    if _SECRET_UPLOAD_RE.search(command):
        return True
    if _INTERP_EVAL_RE.search(command) and _SECRET_ANY_RE.search(command):
        return True
    return False

# ── Layer: 인라인 토큰/시크릿 감지 ──────────────────────

INLINE_TOKEN_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 순서: 구체적 패턴 → 일반 패턴 (sk-ant- 가 sk- 보다 먼저)
    # 앞에 영숫자가 있으면 매치 금지 — "task-..."·"desk-..." 같은 평범한 토큰이
    # 단어 내부 "sk-" 로 OpenAI 키 오인돼 실사용 명령(git checkout 등)이 차단되던 오탐 방지.
    (re.compile(r"(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_\-]{20,}"), "Anthropic API 키"),
    (re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}"), "OpenAI API 키"),
    (re.compile(r"ghp_[A-Za-z0-9]{30,}"), "GitHub PAT"),
    (re.compile(r"ghs_[A-Za-z0-9]{30,}"), "GitHub Secret"),
    (re.compile(r"gho_[A-Za-z0-9]{30,}"), "GitHub OAuth 토큰"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{30,}"), "GitHub Fine-grained PAT"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS Access Key"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9\-]+"), "Slack 토큰"),
    (re.compile(r"https?://[^\s:]+:[^\s@]+@"), "URL 내장 인증정보"),
]


# ── Layer: plan-gate 자가 전이 승격 (차단 아님 — 사용자 확인 ask) ─────────
# 게이트 전이(approve/done/…)는 사용자 전용 통제 지점인데, Claude 가 Bash 로
# plan_gate_cli.py 를 직접 호출하면 슬래시 커맨드의 disable-model-invocation 을
# 우회해 자가승인·자가마감이 가능하다. 차단(exit 2)하지 않는 이유: /approve-plan
# 슬래시 커맨드 자체가 같은 명령을 실행하므로, 정당한 사용자 경로까지 죽이지 않고
# 권한창(ask)으로 올려 사용자가 한 번 확인하게 한다. status/subplan(의도적 Claude
# 호출 가능)·scope-shadow/enforce 는 승격 대상이 아니다.
_GATE_TRANSITION_RE = re.compile(
    r"plan_gate_cli\.py['\"]?\s+"
    r"(approve|done|skip|skip-verify|retry|replan|rollback|off|scope-off)(?!\S)"
)


def _gate_self_transition(command: str) -> str | None:
    """명령이 plan-gate 전이 CLI 호출이면 액션명 반환, 아니면 None."""
    m = _GATE_TRANSITION_RE.search(command)
    return m.group(1) if m else None


def _escalate_if_needed(command: str, data: dict) -> None:
    """비차단 승격 판정 — 워크스페이스 밖 파괴 명령·plan-gate 전이면 ask 를 출력."""
    cwd = (data.get("cwd") or "").strip() or os.getcwd()
    outside = _destructive_outside_workspace(command, cwd)
    if outside:
        _emit_ask(
            f"[workspace-guard] {outside}입니다. 워크스페이스 밖 파괴적 작업은 "
            "사용자 확인이 필요합니다 — 사용자가 명시적으로 요청한 작업이면 허용하고, "
            "Claude 의 자율 판단이면 거부 후 워크스페이스 안에서 대안을 찾으세요."
        )
        return
    action = _gate_self_transition(command)
    if action:
        _emit_ask(
            f"[plan-gate] '{action}' 는 게이트 상태를 바꾸는 사용자 전용 결정입니다. "
            "Claude 의 자율 실행이면 거부하고 슬래시 커맨드(/approve-plan 등)로 "
            "직접 입력하세요. 사용자가 요청한 실행이면 허용해도 됩니다."
        )


def _emit_ask(reason: str) -> None:
    """PreToolUse permissionDecision=ask — 사용자 확인 후에만 실행되게 승격."""
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


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


# ── Layer: 워크스페이스 밖 파괴적 명령 → 사용자 확인(ask) 승격 ──────────
#
# 루트/홈 하드블록(위)이 못 덮는 중간 지대: 상위·형제 디렉토리를 향한 파괴적
# 파일 명령 (`rm -rf ../x`, 절대경로 형제, `> ../config` truncate 등).
# 차단(exit 2)이 아니라 ask 인 이유: 사용자가 정당하게 요청한 워크스페이스 밖
# 정리까지 죽이지 않고, 확인창 한 번으로 통제권을 사용자에게 넘긴다
# (plan-gate 전이 승격과 동일 채널). 정적 분석 한계(변수·명령치환 타겟)는
# fail-closed — 해석 불가면 ask.

_FS_DESTRUCTIVE = frozenset({"rm", "shred", "unlink", "rmdir", "mv"})
_WS_GUARD_CMDS = _FS_DESTRUCTIVE | {"find"}
# find 의 파괴적 표현식 — -delete, -exec/-execdir rm|shred
_FIND_DESTRUCTIVE_RE = re.compile(r"-delete\b|-exec(?:dir)?\s+(?:\S*/)?(?:rm|shred)\b")
# `find … | xargs rm` — find 세그먼트 자체엔 파괴 표현식이 없지만 경로가 삭제 대상
_XARGS_RM_RE = re.compile(r"\bxargs\b[^\n;&|]*\b(?:rm|shred)\b")
# stdout `>` truncate 리다이렉트 — `>>`(append)·`>&`(fd 복제)·`>(`(process subst) 제외
_TRUNC_REDIR_RE = re.compile(r"(?<!>)>(?![>&(])\s*([^\s;|&<>()]+)")
_GLOB_CHAR_RE = re.compile(r"[*?\[]")
_UNRESOLVED_RE = re.compile(r"[$`]")
# 시스템 임시 트리는 워크스페이스 밖이어도 통과 (스크래치 용도) — 단, 워크스페이스
# 자체가 그 트리 안이면 같은 트리의 형제를 지울 수 있으므로 해당 트리는 예외 비활성.
_TMP_PREFIXES = tuple(os.path.realpath(d) for d in ("/tmp", "/var/tmp", "/dev/shm"))
# 리다이렉트 무해 타겟 — realpath 전에 문자열로 판정 (/dev/stdout 은 realpath 가
# 훅 프로세스의 실제 fd 대상으로 풀려 오판하므로 lexical 비교가 맞다)
_DEV_SINKS = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"})


def _quoted_subcommands(command: str) -> list[str]:
    """따옴표 안 문자열 목록 — `sh -c 'rm -rf ../x'` 같은 중첩 명령을 세그먼트로 추가."""
    return [a or b for a, b in re.findall(r"'([^']+)'|\"([^\"]+)\"", command)]


def _path_escapes(target: str, cwd: str, root: str) -> str:
    """타겟 경로 판정: 'outside' | 'inside' | 'unresolved'.

    root·cwd 는 realpath 된 절대경로 전제. 변수는 훅 프로세스 환경으로 확장을
    시도하고, 남는 `$`/백틱(미정의 변수·명령치환)은 해석 불가로 본다.
    glob 타겟은 첫 glob 문자 앞까지의 리터럴 접두로 판정한다 (`../*` → `../`).
    """
    t = target.strip("'\"")
    if not t or t == "-":
        return "inside"
    t = os.path.expanduser(os.path.expandvars(t))
    if _UNRESOLVED_RE.search(t):
        return "unresolved"
    if t in _DEV_SINKS:
        return "inside"
    m = _GLOB_CHAR_RE.search(t)
    if m:
        t = t[:m.start()] or "."
    resolved = os.path.realpath(os.path.join(cwd, t))
    if resolved == root or resolved.startswith(root + os.sep):
        return "inside"
    for pref in _TMP_PREFIXES:
        under_pref = resolved.startswith(pref + os.sep)
        root_in_pref = root == pref or root.startswith(pref + os.sep)
        if under_pref and not root_in_pref:
            return "inside"
    return "outside"


def _segment_targets(toks: list[str], idx: int, name: str, seg: str, xargs_rm: bool) -> list[str]:
    """세그먼트에서 판정 대상 경로 토큰 추출. 파괴적 문맥이 아니면 빈 목록."""
    if name == "find":
        # find 는 파괴 표현식(-delete/-exec rm)이 있거나 xargs rm 으로 이어질 때만 판정
        if not (_FIND_DESTRUCTIVE_RE.search(seg) or xargs_rm):
            return []
        paths = []
        for t in toks[idx + 1:]:
            if t.strip("'\"").startswith("-") or t in ("(", "!"):
                break  # 표현식 시작 — 이후는 경로가 아니다
            paths.append(t)
        return paths or ["."]  # 경로 생략 시 find 는 cwd 를 탐색
    paths = []
    for t in toks[idx + 1:]:
        if t == "--":
            continue
        if t.startswith("--target-directory="):
            paths.append(t.split("=", 1)[1])  # mv/cp 계열 목적지 플래그
        elif not t.strip("'\"").startswith("-"):
            paths.append(t)
    return paths


def _destructive_outside_workspace(command: str, cwd: str) -> str | None:
    """워크스페이스 밖을 향한 파괴적 명령이면 사유 문자열, 아니면 None."""
    root = os.environ.get("CLAUDE_PROJECT_DIR", "").rstrip("/")
    if not root:
        return None
    root = os.path.realpath(root)
    cwd = os.path.realpath(cwd or ".")
    xargs_rm = bool(_XARGS_RM_RE.search(command))
    segments = re.split(r"[;&|\n]+", command) + _quoted_subcommands(command)
    for seg in segments:
        toks = seg.split()
        idx = _find_cmd_index(toks, _WS_GUARD_CMDS)
        if idx < 0:
            continue
        name = os.path.basename(toks[idx].lstrip("\\"))
        for t in _segment_targets(toks, idx, name, seg, xargs_rm):
            verdict = _path_escapes(t, cwd, root)
            if verdict == "outside":
                return f"{name} 타겟 {t!r} 이(가) 워크스페이스 밖 경로"
            if verdict == "unresolved":
                return f"{name} 타겟 {t!r} 해석 불가 (변수/명령치환)"
    # `>` truncate 리다이렉트 — 워크스페이스 밖 파일을 0바이트로 덮는 사고 방지
    for m in _TRUNC_REDIR_RE.finditer(command):
        target = m.group(1)  # fd 숫자(`2>`)는 `>` 앞이라 그룹에 안 들어온다
        verdict = _path_escapes(target, cwd, root)
        if verdict == "outside":
            return f"리다이렉트(>) 타겟 {target!r} 이(가) 워크스페이스 밖 경로"
        if verdict == "unresolved":
            return f"리다이렉트(>) 타겟 {target!r} 해석 불가 (변수/명령치환)"
    return None


def _check(command: str) -> tuple[bool, str]:
    """(차단여부, 사유) 반환."""
    for pattern, reason in HARD_BLOCK_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return True, reason
    if _rm_targets_root(command):
        return True, "rm 루트/홈 전체 재귀 삭제 감지"
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
        _escalate_if_needed(command, data)
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
