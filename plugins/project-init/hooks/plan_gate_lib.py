"""plan-gate 공통 라이브러리.

상태 관리, 체크포인트(git tag/stash), 트리거 휴리스틱, 프로젝트 감지를 담당.
훅 스크립트(plan_gate.py, plan_approval.py, plan_gate_cli.py, plan_gate_gc.py,
update_docs.py)에서 공유한다.

상태 파일: <project>/.claude/state/plan_gate.json
체크포인트: git tag `.claude/gate/<gate_id>/clean`
            git stash entry (message에 gate_id 포함)
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

# ── 정책 디폴트 (D5/D2/D7) ───────────────────────────────────────────────
TRIGGER_EDIT_COUNT = 3
TRIGGER_UNIQUE_FILES = 3
TRIGGER_MULTI_EDIT_ITEMS = 5
APPROVED_BUFFER = 2     # initial_count + buffer
APPROVED_MIN = 5        # 최소 임계값
GC_MAX_AGE_DAYS = 30

GATE_STATES = {"created", "approved", "verified", "rejected", "rolled_back", "done"}

TAG_PREFIX = ".claude/gate/"
STASH_PREFIX = "[plan-gate] "


# ── 프로젝트 감지 ────────────────────────────────────────────────────────
def find_project_root() -> Path | None:
    """CLAUDE_PROJECT_DIR 우선, 없으면 cwd 상위에서 .claude/를 찾는다."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".claude").exists():
            return parent
    return None


def is_project_init_managed(root: Path) -> bool:
    """project-init 플러그인이 초기화한 프로젝트만 plan-gate 적용한다.
    무관한 프로젝트에 부작용을 주지 않는 가드.
    """
    return (root / ".claude" / "agents" / "verifier.md").exists()


# ── 상태 파일 입출력 ─────────────────────────────────────────────────────
def state_path(root: Path) -> Path:
    return root / ".claude" / "state" / "plan_gate.json"


def load_state(root: Path) -> dict[str, Any]:
    p = state_path(root)
    if not p.exists():
        return {"current_gate_id": None, "gates": {}}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {"current_gate_id": None, "gates": {}}


def save_state(root: Path, state: dict[str, Any]) -> None:
    p = state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    tmp.replace(p)


# ── git 헬퍼 ────────────────────────────────────────────────────────────
def _git(root: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=check,
    )


def has_git(root: Path) -> bool:
    r = _git(root, "rev-parse", "--git-dir")
    return r.returncode == 0


def head_sha(root: Path) -> str | None:
    r = _git(root, "rev-parse", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else None


def working_tree_clean(root: Path) -> bool:
    r = _git(root, "status", "--porcelain")
    return r.returncode == 0 and r.stdout.strip() == ""


def existing_clean_tag_for_head(root: Path) -> str | None:
    """현재 HEAD를 가리키는 .claude/gate/*/clean tag가 이미 있으면 반환."""
    sha = head_sha(root)
    if not sha:
        return None
    r = _git(root, "tag", "--points-at", sha, "--list", f"{TAG_PREFIX}*/clean")
    if r.returncode != 0:
        return None
    tags = [t.strip() for t in r.stdout.splitlines() if t.strip()]
    return tags[0] if tags else None


def create_clean_tag(root: Path, gate_id: str) -> str | None:
    """현재 HEAD에 lightweight tag 생성. 실패 시 None."""
    if not has_git(root) or not head_sha(root):
        return None
    tag = f"{TAG_PREFIX}{gate_id}/clean"
    r = _git(root, "tag", tag)
    return tag if r.returncode == 0 else None


def delete_tag(root: Path, tag: str) -> bool:
    r = _git(root, "tag", "-d", tag)
    return r.returncode == 0


def stash_dirty(root: Path, gate_id: str) -> str | None:
    """working tree dirty면 stash 생성. message에 gate_id 포함.
    반환: stash ref 또는 None."""
    if working_tree_clean(root):
        return None
    msg = f"{STASH_PREFIX}{gate_id}"
    r = _git(root, "stash", "push", "-u", "-m", msg)
    if r.returncode != 0:
        return None
    # 가장 최근 stash가 방금 만든 것
    return "stash@{0}"


def find_stash_for_gate(root: Path, gate_id: str) -> str | None:
    """gate_id가 message에 포함된 stash entry 찾기."""
    r = _git(root, "stash", "list")
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        # 형식: stash@{N}: On branch: <message>
        if gate_id in line:
            ref = line.split(":", 1)[0].strip()
            return ref
    return None


def reset_to_tag(root: Path, tag: str) -> bool:
    r = _git(root, "reset", "--hard", tag)
    return r.returncode == 0


def stash_drop(root: Path, ref: str) -> bool:
    r = _git(root, "stash", "drop", ref)
    return r.returncode == 0


def stash_pop(root: Path, ref: str) -> bool:
    r = _git(root, "stash", "pop", ref)
    return r.returncode == 0


# ── todo.md 해시 ────────────────────────────────────────────────────────
def todo_md_path(root: Path) -> Path:
    return root / "tasks" / "todo.md"


def hash_todo_md(root: Path) -> tuple[str | None, float | None]:
    """todo.md의 sha256과 mtime. 없으면 (None, None)."""
    p = todo_md_path(root)
    if not p.exists():
        return None, None
    try:
        content = p.read_bytes()
        return hashlib.sha256(content).hexdigest(), p.stat().st_mtime
    except Exception:
        return None, None


# ── gate 객체 ───────────────────────────────────────────────────────────
def new_gate_id() -> str:
    ts = int(time.time() * 1000)
    suffix = uuid.uuid4().hex[:6]
    return f"plan-gate_{ts}_{suffix}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_gate(gate_id: str | None = None) -> dict[str, Any]:
    return {
        "id": gate_id or new_gate_id(),
        "created_at": now_iso(),
        "state": "created",
        "edit_count": 0,
        "edit_count_post_approval": 0,
        "unique_files": [],
        "multi_edit_max": 0,
        "initial_edit_count": None,
        "initial_unique_files": None,
        "approved_at": None,
        "todo_md_sha256": None,
        "todo_md_mtime": None,
        "checkpoint_clean_tag": None,
        "checkpoint_dirty_stash_ref": None,
        "verifier_status": None,
    }


def current_gate(state: dict[str, Any]) -> dict[str, Any] | None:
    gid = state.get("current_gate_id")
    if not gid:
        return None
    return state.get("gates", {}).get(gid)


def set_current_gate(state: dict[str, Any], gate: dict[str, Any]) -> None:
    state.setdefault("gates", {})[gate["id"]] = gate
    state["current_gate_id"] = gate["id"]


def clear_current_gate(state: dict[str, Any]) -> None:
    state["current_gate_id"] = None


# ── 트리거 휴리스틱 (D5) ─────────────────────────────────────────────────
def trigger_threshold_exceeded(gate: dict[str, Any]) -> bool:
    return (
        gate["edit_count"] >= TRIGGER_EDIT_COUNT
        or len(gate["unique_files"]) >= TRIGGER_UNIQUE_FILES
        or gate["multi_edit_max"] >= TRIGGER_MULTI_EDIT_ITEMS
    )


def post_approval_limit(gate: dict[str, Any]) -> int:
    """승인 후 재차단 임계값 (D2): max(initial + buffer, MIN)."""
    initial = gate.get("initial_edit_count") or 0
    return max(initial + APPROVED_BUFFER, APPROVED_MIN)


def post_approval_limit_exceeded(gate: dict[str, Any]) -> bool:
    return gate["edit_count_post_approval"] >= post_approval_limit(gate)


# ── tool input 분석 ─────────────────────────────────────────────────────
def extract_target_file(tool_name: str, tool_input: dict[str, Any]) -> str | None:
    if tool_name in ("Edit", "Write"):
        return tool_input.get("file_path")
    if tool_name == "MultiEdit":
        return tool_input.get("file_path")
    return None


def count_multi_edit_items(tool_name: str, tool_input: dict[str, Any]) -> int:
    if tool_name == "MultiEdit":
        return len(tool_input.get("edits", []) or [])
    return 0


# ── 토큰 정의 (D6) ──────────────────────────────────────────────────────
APPROVE_TOKENS = {"/approve-plan"}
DONE_TOKENS = {"/done"}
ROLLBACK_TOKENS = {"/rollback"}
RETRY_TOKENS = {"/retry"}
REPLAN_TOKENS = {"/replan"}

ALL_TOKENS = APPROVE_TOKENS | DONE_TOKENS | ROLLBACK_TOKENS | RETRY_TOKENS | REPLAN_TOKENS


def detect_token(prompt: str) -> str | None:
    s = (prompt or "").strip()
    if s in ALL_TOKENS:
        return s
    return None
