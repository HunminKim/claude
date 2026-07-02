# [prompt-log] removable plugin — see plugins/prompt-log/README.md
"""prompt-log 공통 라이브러리.

paths / 동의 검사(consent) / sanitize / record store / schema 를 담당.
훅 스크립트(prompt_logger.py, tool_counter.py, session_finalize.py)에서 공유.

식별 마커: 모든 prompt-log 파일/식별자는 grep 으로 한 번에 찾을 수 있도록
함수 prefix `pl_` 와 헤더 주석 `[prompt-log]` 를 일관되게 사용한다.

V1 범위 (V2_TODO.md 참고):
- 동의: 글로벌 whitelist + 프로젝트 marker 둘 다 검사 (default deny)
- 저장: ~/.claude/prompt-log/prompts-YYYY-MM.jsonl 월별 분할
- sanitize: 정규식 마스킹 (API key, JWT, AWS, 이메일)
- record: prompt 단위 jsonl, plan-gate 메타 read-only 참조
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl  # POSIX 전용 파일 락 — Windows 엔 없다
except ImportError:  # Windows: fcntl 부재 → 락 생략 (아래 _pl_file_lock 참고)
    fcntl = None  # type: ignore[assignment]


@contextlib.contextmanager
def _pl_file_lock(lock_path: Path):
    """크로스플랫폼 배타적 파일 락 컨텍스트.

    POSIX 는 fcntl.flock(LOCK_EX) 로 동시 쓰기를 막는다. Windows 처럼 fcntl 이
    없는 환경에서는 락을 생략한다 — prompt-log V1 은 단일 에이전트 직렬 실행을
    전제하고 상태 파일은 atomic-rename 으로 일관성을 유지하므로 무락이 안전하다
    (멀티세션 동시 쓰기 보호는 V2 보류 — design 260613 D8). 무락이라도 모듈
    import 자체가 죽지 않아 Windows 종료 시 ImportError 가 사라진다.
    """
    if fcntl is None:
        yield
        return
    with open(lock_path, "w", encoding="utf-8") as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)


def _pl_fchmod_600(fd: int) -> None:
    """파일 디스크립터를 0600 으로 — POSIX 전용. Windows 엔 os.fchmod 가 없어
    graceful skip 한다 (윈도우 권한 모델은 chmod 비트와 무관)."""
    if hasattr(os, "fchmod"):
        os.fchmod(fd, 0o600)

# ── [prompt-log] paths ──────────────────────────────────────────────────
PL_GLOBAL_DIRNAME = "prompt-log"  # ~/.claude/prompt-log/
PL_PROJECT_MARKER = "prompt-log-consent"  # <project>/.claude/prompt-log-consent
PL_ALLOWED_FILE = "projects-allowed.json"
PL_ACTIVE_FILE = "prompt-log-active.json"  # <project>/.claude/state/prompt-log-active.json


def pl_home() -> Path:
    """글로벌 prompt-log 디렉토리 경로 (생성하지 않는다).

    읽기 경로(동의 검사 등)에서 mkdir 부작용이 생기면 **미동의 프로젝트에서도**
    매 프롬프트마다 ~/.claude/prompt-log/ 가 만들어진다 — default deny 취지 위반.
    디렉토리 생성은 쓰기 지점(pl_save_allowed/pl_append_record)이 담당한다.
    """
    return Path(os.path.expanduser("~/.claude")) / PL_GLOBAL_DIRNAME


def pl_log_path(ts: datetime | None = None) -> Path:
    """월별 jsonl 파일 경로."""
    ts = ts or datetime.now(timezone.utc)
    return pl_home() / f"prompts-{ts:%Y-%m}.jsonl"


def pl_allowed_path() -> Path:
    return pl_home() / PL_ALLOWED_FILE


def pl_project_marker_path(project_root: Path) -> Path:
    return project_root / ".claude" / PL_PROJECT_MARKER


def pl_active_state_path(project_root: Path) -> Path:
    return project_root / ".claude" / "state" / PL_ACTIVE_FILE


def pl_find_project_root() -> Path | None:
    """CLAUDE_PROJECT_DIR 우선, 없으면 cwd 상위에서 .claude/ 탐색."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".claude").exists():
            return parent
    return None


# ── [prompt-log] consent (동의 검사) ─────────────────────────────────────
def pl_load_allowed() -> list[dict[str, Any]]:
    p = pl_allowed_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return []


def pl_save_allowed(allowed: list[dict[str, Any]]) -> None:
    p = pl_allowed_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lock_file = p.parent / ".allowed.lock"
    with _pl_file_lock(lock_file):
        tmp = p.with_suffix(".tmp")
        # 0600 — 프로젝트 절대경로 목록도 active/records 와 동일한 권한 정책
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        _pl_fchmod_600(fd)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(allowed, ensure_ascii=False, indent=2))
        tmp.replace(p)


def pl_is_consented(project_root: Path) -> bool:
    """프로젝트가 동의 상태인지: 글로벌 whitelist + 프로젝트 marker 둘 다 있어야 True.
    한쪽만 있으면 default deny.
    """
    if not pl_project_marker_path(project_root).exists():
        return False
    abs_path = str(project_root.resolve())
    for entry in pl_load_allowed():
        if entry.get("abs_path") == abs_path:
            return True
    return False


def pl_grant_consent(project_root: Path) -> None:
    """marker 생성 + whitelist 등록. /project-init 또는 사용자 수동 호출."""
    marker = pl_project_marker_path(project_root)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(pl_now_iso() + "\n", encoding="utf-8")

    abs_path = str(project_root.resolve())
    allowed = pl_load_allowed()
    if not any(e.get("abs_path") == abs_path for e in allowed):
        allowed.append(
            {
                "abs_path": abs_path,
                "project_name": project_root.name,
                "consent_at": pl_now_iso(),
            }
        )
        pl_save_allowed(allowed)


# ── [prompt-log] sanitize (PII 마스킹) ───────────────────────────────────
# V1 최소 패턴. V2: 사용자 정의 yaml 추가.
PL_SANITIZE_PATTERNS = [
    # API keys — specific 패턴을 general보다 먼저 (sk-ant-가 sk-에 먼저 걸리지 않도록)
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "[REDACTED:anthropic_key]"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "[REDACTED:openai_key]"),
    (re.compile(r"ghp_[A-Za-z0-9]{30,}"), "[REDACTED:github_pat]"),
    (re.compile(r"ghs_[A-Za-z0-9]{30,}"), "[REDACTED:github_secret]"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]+"), "[REDACTED:slack_token]"),
    # AWS
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED:aws_access_key]"),
    # 40자 base64 패턴 제거 — Git SHA·hex hash 등 정상 텍스트 오탐 심각
    # JWT
    (re.compile(r"eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"), "[REDACTED:jwt]"),
    # URL with credentials
    (re.compile(r"https?://[^\s:]+:[^\s@]+@"), "https://[REDACTED:url_creds]@"),
    # Email
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "[REDACTED:email]"),
    # Korean PII
    # 주민등록번호: 6자리-7자리 (앞자리 뒤 '-' 선택, 오탐 방지를 위해 앞뒤 단어경계 확인)
    (
        re.compile(r"\b([0-9]{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12][0-9]|3[01]))-?([1-4][0-9]{6})\b"),
        "[REDACTED:kr_rrn]",
    ),
    # 사업자등록번호: 3-2-5 자리 (예: 123-45-67890)
    (re.compile(r"\b[0-9]{3}-[0-9]{2}-[0-9]{5}\b"), "[REDACTED:kr_brn]"),
    # 한국 전화번호: 010/011/016/017/018/019-XXXX-XXXX, 지역번호 02/0X-XXXX-XXXX
    (
        re.compile(r"\b(01[016789]|0(?:2|3[1-3]|4[1-4]|5[1-5]|6[1-4]|7[1-7]|8[1-8]))-?([0-9]{3,4})-?([0-9]{4})\b"),
        "[REDACTED:kr_phone]",
    ),
]


def _pl_load_custom_patterns() -> list[tuple[re.Pattern[str], str]]:
    """~/.claude/prompt-log/sanitize_rules.yaml 에서 사용자 정의 패턴 로드.

    yaml 형식:
      - pattern: "regex"
        replacement: "[REDACTED:label]"
    yaml 미설치 또는 파일 없으면 빈 리스트 반환 (graceful skip).
    """
    rules_path = pl_home() / "sanitize_rules.yaml"
    if not rules_path.exists():
        return []
    try:
        import importlib.util

        if importlib.util.find_spec("yaml") is None:
            return []
        import yaml  # type: ignore[import]

        data = yaml.safe_load(rules_path.read_text(encoding="utf-8", errors="ignore")) or []
        result: list[tuple[re.Pattern[str], str]] = []
        for entry in data:
            if isinstance(entry, dict) and "pattern" in entry and "replacement" in entry:
                result.append((re.compile(entry["pattern"]), str(entry["replacement"])))
        return result
    except Exception:
        return []


_PL_CUSTOM_PATTERNS: list[tuple[re.Pattern[str], str]] | None = None


def pl_sanitize(text: str) -> str:
    global _PL_CUSTOM_PATTERNS
    if not text:
        return text
    if _PL_CUSTOM_PATTERNS is None:
        _PL_CUSTOM_PATTERNS = _pl_load_custom_patterns()
    out = text
    for pattern, replacement in PL_SANITIZE_PATTERNS + _PL_CUSTOM_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


# ── [prompt-log] active state (prompt 추적) ──────────────────────────────
def pl_load_active(project_root: Path) -> dict[str, Any] | None:
    p = pl_active_state_path(project_root)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None


def _pl_write_active(project_root: Path, active: dict[str, Any]) -> None:
    """active 파일 무락 기록 (호출자가 락을 이미 잡은 상태에서 사용)."""
    p = pl_active_state_path(project_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    # 내용 기록 전에 0600 확보 — 기록 후 chmod 하면 그 사이
    # prompt 본문이 group/other readable 로 노출되는 race 발생
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    _pl_fchmod_600(fd)  # tmp 잔존물이 0644 였던 경우 강제 교정 (POSIX)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(active, ensure_ascii=False, indent=2))
    tmp.replace(p)


def pl_save_active(project_root: Path, active: dict[str, Any]) -> None:
    p = pl_active_state_path(project_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    lock_file = p.parent / ".active.lock"
    with _pl_file_lock(lock_file):
        _pl_write_active(project_root, active)


def pl_update_active(project_root: Path, mutate) -> bool:
    """active 를 load→변형→save 까지 **단일 락**으로 갱신. 갱신했으면 True.

    쓰기 구간만 락으로 감싸면 병렬 툴콜 훅 2개가 같은 스냅샷을 읽고 서로의
    증가분을 덮어써 카운트가 유실된다(RMW race). 락을 read 이전으로 넓힌다.
    active 없으면 no-op(False). mutate 는 dict 를 제자리 변형하는 callable.
    """
    p = pl_active_state_path(project_root)
    if not p.exists():
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    lock_file = p.parent / ".active.lock"
    with _pl_file_lock(lock_file):
        active = pl_load_active(project_root)
        if active is None:
            return False
        mutate(active)
        _pl_write_active(project_root, active)
    return True


def pl_clear_active(project_root: Path) -> None:
    p = pl_active_state_path(project_root)
    if p.exists():
        p.unlink()


# ── [prompt-log] schema / store ──────────────────────────────────────────
# plan-gate 전이 토큰 값 — project-init plan_approval._ACTION_TOKENS 와 동기 유지.
# (플러그인 간 import 불가 — 변경 시 양쪽 함께 갱신. tests/smoke_test.py 가 대조)
PL_TOKEN_VALUES = {
    "approve-plan",
    "approve",
    "done",
    "skip",
    "keep",
    "skip-verify",
    "rollback",
    "retry",
    "replan",
}


def pl_normalize_token(text: str) -> str | None:
    """prompt 텍스트가 plan-gate 토큰이면 정규화된 토큰 값, 아니면 None.

    실데이터에서 관측된 3가지 입력 형태를 모두 흡수한다 (260610 분석 — 구버전
    슬래시 정확일치 세트는 98건 중 0건 인식):
    - 평문: "done", "approve"          (UserPromptSubmit fallback 경로)
    - 슬래시: "/done"
    - 플러그인 네임스페이스: "/project-init:done"
    """
    t = text.strip().lstrip("/")
    # 플러그인 네임스페이스 prefix 제거 (예: project-init:done → done) — 임의 플러그인 호환
    t = re.sub(r"^[\w.-]+:", "", t)
    return t if t in PL_TOKEN_VALUES else None


def pl_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def pl_new_prompt_id() -> str:
    ts = int(time.time() * 1000)
    return f"pl_{ts}_{uuid.uuid4().hex[:6]}"


def pl_project_meta(project_root: Path) -> dict[str, Any]:
    abs_path = str(project_root.resolve())
    return {
        "abs_path": abs_path,
        "name": project_root.name,
        "hash": hashlib.sha256(abs_path.encode()).hexdigest()[:12],
    }


def pl_make_active_record(
    project_root: Path, prompt_text: str, session_id: str | None
) -> dict[str, Any]:
    sanitized = pl_sanitize(prompt_text or "")
    token_value = pl_normalize_token(sanitized)
    return {
        "prompt_id": pl_new_prompt_id(),
        "session_id": session_id or "",
        "ts_start": pl_now_iso(),
        "ts_end": None,
        "project": pl_project_meta(project_root),
        "prompt": {
            "text": sanitized,
            "len": len(sanitized),
            "is_token": token_value is not None,
            "token_value": token_value,
        },
        "tools": {
            "edit": 0,
            "write": 0,
            "multi_edit": 0,
            "bash": 0,
            "task": 0,
            "agent": 0,
            "other": 0,
            "total": 0,
        },
        "files": {"unique": []},
        "plan_gate": None,  # session_finalize에서 채움
        "outcome": {"ended_by": None, "duration_sec": None},
    }


def pl_finalize_record(active: dict[str, Any], project_root: Path, ended_by: str) -> dict[str, Any]:
    """active record를 final record로 변환 (flush 직전)."""
    ts_end = pl_now_iso()
    # duration 계산
    try:
        t0 = datetime.fromisoformat(active["ts_start"])
        t1 = datetime.fromisoformat(ts_end)
        duration = (t1 - t0).total_seconds()
    except Exception:
        duration = None

    record = dict(active)
    record["ts_end"] = ts_end
    record["outcome"] = {"ended_by": ended_by, "duration_sec": duration}

    # files: list of paths → unique_count + sample
    unique = record.get("files", {}).get("unique", [])
    if isinstance(unique, list):
        record["files"] = {
            "unique_count": len(unique),
            "sample": unique[:6],
        }

    # plan-gate read-only 참조 (있으면 메타 첨부, 없으면 null)
    record["plan_gate"] = pl_read_plan_gate_meta(project_root)
    return record


def pl_read_plan_gate_meta(project_root: Path) -> dict[str, Any] | None:
    """plan-gate state 파일을 read-only로 참조. 없거나 읽기 실패 시 None."""
    pg_state = project_root / ".claude" / "state" / "plan_gate.json"
    if not pg_state.exists():
        return None
    try:
        data = json.loads(pg_state.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None
    current_id = data.get("current_gate_id")
    gate = (data.get("gates", {}) or {}).get(current_id) if current_id else None
    if not gate:
        return None
    return {
        "gate_id": gate.get("id"),
        "state": gate.get("state"),
        "verifier_status": gate.get("verifier_status"),
        "edit_count": gate.get("edit_count"),
        "unique_files_count": len(gate.get("unique_files", [])),
    }


def pl_append_record(record: dict[str, Any]) -> None:
    """월별 jsonl에 1줄 append. flock으로 동시 쓰기 방지, 0600 권한 보장."""
    _pl_append_line(pl_log_path(), record)


def _pl_append_line(p: Path, record: dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    lock_file = p.parent / ".records.lock"
    with _pl_file_lock(lock_file):
        # append 전에 0600 으로 생성 — 기록 후 chmod 의 노출 race 방지
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        _pl_fchmod_600(fd)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(line)


def pl_flush_record(record: dict[str, Any]) -> str | None:
    """레코드를 월별 jsonl 에 flush. 실패 시 dead-letter 로 2차 시도.

    반환: 성공 None / 실패 시 경고 메시지(호출자가 stderr 로 출력).
    과거: flush 실패 후에도 active 를 무조건 삭제해 레코드가 영구 유실됐다 —
    본 파일이 안 되면 failed-flush.jsonl 에라도 남겨 재시도 여지를 보존한다.
    """
    try:
        pl_append_record(record)
        return None
    except Exception as e:
        try:
            _pl_append_line(pl_home() / "failed-flush.jsonl", record)
            return f"[prompt-log] flush 실패({e}) — failed-flush.jsonl 에 보존"
        except Exception as e2:
            return f"[prompt-log] flush 실패({e}) + dead-letter 도 실패({e2}) — 레코드 유실"


# ── [prompt-log] tool counting helpers ──────────────────────────────────
def pl_tool_bucket(tool_name: str) -> str:
    """도구 이름 → bucket key (tools 딕셔너리에서 사용)."""
    if tool_name == "Edit":
        return "edit"
    if tool_name == "Write":
        return "write"
    if tool_name == "MultiEdit":
        return "multi_edit"
    if tool_name == "Bash":
        return "bash"
    if tool_name == "Task":
        return "task"
    if tool_name == "Agent":
        # v2.1.63에서 Task → Agent 개명. task 버킷과 분리해 신구 데이터 구분 가능하게.
        return "agent"
    return "other"


def pl_extract_target_file(tool_name: str, tool_input: dict[str, Any]) -> str | None:
    if tool_name in ("Edit", "Write", "MultiEdit"):
        return tool_input.get("file_path")
    return None
