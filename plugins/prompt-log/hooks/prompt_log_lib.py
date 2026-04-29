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

import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── [prompt-log] paths ──────────────────────────────────────────────────
PL_GLOBAL_DIRNAME = "prompt-log"             # ~/.claude/prompt-log/
PL_PROJECT_MARKER = "prompt-log-consent"     # <project>/.claude/prompt-log-consent
PL_ALLOWED_FILE = "projects-allowed.json"
PL_ACTIVE_FILE = "prompt-log-active.json"    # <project>/.claude/state/prompt-log-active.json


def pl_home() -> Path:
    """글로벌 prompt-log 디렉토리. 없으면 만든다."""
    home = Path(os.path.expanduser("~/.claude")) / PL_GLOBAL_DIRNAME
    home.mkdir(parents=True, exist_ok=True)
    return home


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
        return json.loads(p.read_text())
    except Exception:
        return []


def pl_save_allowed(allowed: list[dict[str, Any]]) -> None:
    p = pl_allowed_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(allowed, ensure_ascii=False, indent=2))
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
    marker.write_text(pl_now_iso() + "\n")

    abs_path = str(project_root.resolve())
    allowed = pl_load_allowed()
    if not any(e.get("abs_path") == abs_path for e in allowed):
        allowed.append({
            "abs_path": abs_path,
            "project_name": project_root.name,
            "consent_at": pl_now_iso(),
        })
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
    (re.compile(r"eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
     "[REDACTED:jwt]"),
    # URL with credentials
    (re.compile(r"https?://[^\s:]+:[^\s@]+@"), "https://[REDACTED:url_creds]@"),
    # Email
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
     "[REDACTED:email]"),
]


def pl_sanitize(text: str) -> str:
    if not text:
        return text
    out = text
    for pattern, replacement in PL_SANITIZE_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


# ── [prompt-log] active state (prompt 추적) ──────────────────────────────
def pl_load_active(project_root: Path) -> dict[str, Any] | None:
    p = pl_active_state_path(project_root)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def pl_save_active(project_root: Path, active: dict[str, Any]) -> None:
    p = pl_active_state_path(project_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(active, ensure_ascii=False, indent=2))
    tmp.replace(p)


def pl_clear_active(project_root: Path) -> None:
    p = pl_active_state_path(project_root)
    if p.exists():
        p.unlink()


# ── [prompt-log] schema / store ──────────────────────────────────────────
PL_TOKEN_SET = {
    "/approve-plan", "/done", "/rollback", "/retry", "/replan",
}


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


def pl_make_active_record(project_root: Path, prompt_text: str,
                          session_id: str | None) -> dict[str, Any]:
    sanitized = pl_sanitize(prompt_text or "")
    is_token = sanitized.strip() in PL_TOKEN_SET
    return {
        "prompt_id": pl_new_prompt_id(),
        "session_id": session_id or "",
        "ts_start": pl_now_iso(),
        "ts_end": None,
        "project": pl_project_meta(project_root),
        "prompt": {
            "text": sanitized,
            "len": len(sanitized),
            "is_token": is_token,
            "token_value": sanitized.strip() if is_token else None,
        },
        "tools": {
            "edit": 0, "write": 0, "multi_edit": 0,
            "bash": 0, "task": 0, "other": 0, "total": 0,
        },
        "files": {"unique": []},
        "plan_gate": None,            # session_finalize에서 채움
        "outcome": {"ended_by": None, "duration_sec": None},
    }


def pl_finalize_record(active: dict[str, Any], project_root: Path,
                       ended_by: str) -> dict[str, Any]:
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
        data = json.loads(pg_state.read_text())
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
    """월별 jsonl에 1줄 append."""
    p = pl_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with open(p, "a", encoding="utf-8") as f:
        f.write(line)


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
    return "other"


def pl_extract_target_file(tool_name: str, tool_input: dict[str, Any]) -> str | None:
    if tool_name in ("Edit", "Write", "MultiEdit"):
        return tool_input.get("file_path")
    return None
