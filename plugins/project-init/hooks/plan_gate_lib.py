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

GATE_STATES = {"created", "approved", "verified", "rolled_back", "done"}

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
    # 실제 ref는 find_stash_for_gate()로만 탐색; gate_id를 sentinel로 저장
    return gate_id


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


# ── intro flag (dismissable 안내) ───────────────────────────────────────
def intro_flag_path(root: Path) -> Path:
    return root / ".claude" / "state" / "plan_gate_intro_seen.flag"


def intro_seen(root: Path) -> bool:
    return intro_flag_path(root).exists()


def mark_intro_seen(root: Path) -> None:
    p = intro_flag_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(now_iso())


# ── git diff 요약 (Claude에게 컨텍스트 + 사용자에게 진행 상황 정보) ──────
def git_diff_summary(root: Path, max_diff_lines: int = 80) -> str:
    """차단 시점의 변경 사항 요약. stat + 일부 diff."""
    if not has_git(root):
        return "(git 미사용 — diff 정보 없음)"
    stat = _git(root, "diff", "--stat", "HEAD").stdout.strip()
    diff = _git(root, "diff", "HEAD", "--no-color").stdout.splitlines()
    diff_head = "\n".join(diff[:max_diff_lines])
    truncated = len(diff) > max_diff_lines
    parts = []
    if stat:
        parts.append("변경 통계:")
        parts.append(stat)
    if diff_head:
        parts.append("")
        parts.append("변경 일부 (앞 %d줄):" % max_diff_lines)
        parts.append("```")
        parts.append(diff_head)
        if truncated:
            parts.append(f"... ({len(diff) - max_diff_lines}줄 생략)")
        parts.append("```")
    return "\n".join(parts) if parts else "(변경 없음)"


# ── 트리거 사유 자연어화 ────────────────────────────────────────────────
def trigger_reason_human(gate: dict[str, Any]) -> str:
    reasons = []
    if gate["edit_count"] >= TRIGGER_EDIT_COUNT:
        reasons.append(f"파일 편집 {gate['edit_count']}회 (임계 {TRIGGER_EDIT_COUNT})")
    if len(gate["unique_files"]) >= TRIGGER_UNIQUE_FILES:
        reasons.append(
            f"영향 파일 {len(gate['unique_files'])}개 (임계 {TRIGGER_UNIQUE_FILES})"
        )
    if gate["multi_edit_max"] >= TRIGGER_MULTI_EDIT_ITEMS:
        reasons.append(
            f"단일 MultiEdit {gate['multi_edit_max']}개 항목 "
            f"(임계 {TRIGGER_MULTI_EDIT_ITEMS})"
        )
    return " / ".join(reasons) or "임계값 도달"


def _files_list(gate: dict[str, Any], max_n: int = 6) -> str:
    files = gate.get("unique_files", [])
    if not files:
        return "(없음)"
    if len(files) <= max_n:
        return "\n".join(f"  • {f}" for f in files)
    head = "\n".join(f"  • {f}" for f in files[:max_n])
    return head + f"\n  • ... ({len(files) - max_n}개 더)"


# ── 메시지 빌더 ─────────────────────────────────────────────────────────
DIVIDER = "━" * 60


def _intro_block() -> str:
    """첫 차단에만 함께 보여주는 plan-gate 소개."""
    return (
        "▌ plan-gate 란?\n"
        "  큰 변경을 사용자가 검토하지 못한 채로 진행되는 것을 막는 자동 게이트입니다.\n"
        "  차단 시점에 git tag + git stash로 자동 체크포인트를 생성하므로,\n"
        "  /rollback 으로 안전하게 되돌릴 수 있습니다.\n"
        "  비활성화: .claude/agents/verifier.md 를 삭제하면 plan-gate 가 꺼집니다.\n"
        "  임계값 조정: plugins/project-init/hooks/plan_gate_lib.py 상수\n"
        "              (TRIGGER_EDIT_COUNT 등) 를 수정하세요.\n"
        "  이 안내는 한 번만 표시됩니다.\n"
    )


def format_soft_hint(gate: dict[str, Any]) -> str:
    """edits=2 시점의 부드러운 경고 (차단 X)."""
    return (
        f"\n{DIVIDER}\n"
        f"⚠️  plan-gate 임박\n"
        f"{DIVIDER}\n"
        f"현재까지 {gate['edit_count']}회 편집 / "
        f"{len(gate['unique_files'])}개 파일.\n"
        f"다음 편집이 plan-gate(임계 {TRIGGER_EDIT_COUNT}회) 를 발동시킬 수 있습니다.\n"
        f"큰 작업이라면 미리 tasks/todo.md 에 계획을 작성해두는 것이 좋습니다.\n"
        f"{DIVIDER}\n"
    )


def format_trigger_message(
    gate: dict[str, Any],
    show_intro: bool,
    diff_summary: str,
) -> str:
    """첫 plan-gate 발동 메시지 (사용자+Claude 둘 다 봄)."""
    parts = [
        "",
        DIVIDER,
        "🛑 PLAN-GATE 차단됨 — 사용자 계획 승인 필요",
        DIVIDER,
        "",
        "▌ 왜 멈췄나",
        f"  복잡도 임계값 도달: {trigger_reason_human(gate)}",
        "",
        "▌ 지금까지 한 일",
        f"  • 파일 편집 {gate['edit_count']}회 / 영향 파일 {len(gate['unique_files'])}개",
        f"  • 영향 파일 목록:",
        _files_list(gate),
    ]
    if gate["multi_edit_max"] > 0:
        parts.append(f"  • MultiEdit 최대 항목 수: {gate['multi_edit_max']}")
    parts += [
        "",
        "▌ 자동으로 생성된 체크포인트",
        f"  • clean tag : {gate.get('checkpoint_clean_tag') or '(없음 — git 미사용)'}",
        f"  • dirty stash: {gate.get('checkpoint_dirty_stash_ref') or '(working tree clean)'}",
        "",
        "▌ 사용자에게 다음 토큰 중 하나 입력 요청",
        "  /approve-plan  계획을 승인하고 작업 재개",
        "  /replan        계획을 다시 짜고 재승인 (체크포인트 유지)",
        "  /rollback      체크포인트로 working tree 복원",
        "",
        "▌ 변경 사항 요약 (Claude 가 todo.md 작성에 활용)",
        diff_summary,
        "",
        "▌ Claude 행동 지시",
        "  1. 위 변경 사항·영향 파일을 근거로 tasks/todo.md 에",
        "     단계별 계획을 작성한다 (의도 한 줄 + 단계 체크리스트 + 예상 잔여 작업).",
        "  2. 사용자에게 위 안내를 한국어로 자연스럽게 풀어 안내한다.",
        "  3. 사용자가 토큰을 입력할 때까지 추가 Edit/Write 시도하지 않는다.",
        "",
    ]
    if show_intro:
        parts.append(_intro_block())
    parts += [DIVIDER, ""]
    return "\n".join(parts)


def format_d1_lock_message(gate: dict[str, Any]) -> str:
    """verifier ❌ 미해결 상태에서 새 Edit 시도 시."""
    return (
        f"\n{DIVIDER}\n"
        f"🛑 PLAN-GATE LOCK — 이전 작업 결정 대기 중\n"
        f"{DIVIDER}\n"
        f"\n"
        f"▌ 상태\n"
        f"  gate {gate['id']} 는 verifier 검증 ❌ 후 사용자 결정을 기다리고 있습니다.\n"
        f"  새 코드 수정 전에 이전 작업을 먼저 해결해야 합니다.\n"
        f"\n"
        f"▌ 사용자에게 다음 토큰 중 하나 입력 요청\n"
        f"  /retry     같은 체크포인트에서 재시도 (Claude 가 문제를 수정)\n"
        f"  /rollback  체크포인트로 복원 (이번 시도 폐기)\n"
        f"\n"
        f"▌ Claude 행동 지시\n"
        f"  사용자에게 위 두 옵션을 한국어로 풀어 안내하고, 입력 전까지 멈춘다.\n"
        f"\n{DIVIDER}\n"
    )


def format_scope_creep_message(gate: dict[str, Any]) -> str:
    """승인 후 scope 초과 시."""
    limit = post_approval_limit(gate)
    return (
        f"\n{DIVIDER}\n"
        f"🛑 PLAN-GATE — 승인된 계획의 범위 초과\n"
        f"{DIVIDER}\n"
        f"\n"
        f"▌ 무슨 일이?\n"
        f"  /approve-plan 승인 후 추가 편집이 {gate['edit_count_post_approval']}회 누적되어\n"
        f"  scope creep 임계값 {limit}회를 초과했습니다.\n"
        f"  (초기 승인 시 편집 {gate.get('initial_edit_count')}회 기준)\n"
        f"\n"
        f"▌ 사용자에게 다음 토큰 중 하나 입력 요청\n"
        f"  /done      현재까지를 완료로 마감\n"
        f"  /replan    todo.md 갱신 후 재승인 (체크포인트 유지)\n"
        f"  /rollback  체크포인트로 복원\n"
        f"\n"
        f"▌ Claude 행동 지시\n"
        f"  현재 진행 상황을 한국어로 요약하고, 위 세 옵션의 의미를\n"
        f"  사용자가 결정할 수 있게 풀어 안내한다.\n"
        f"\n{DIVIDER}\n"
    )
