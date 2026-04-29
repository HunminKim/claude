#!/usr/bin/env python3
"""PreToolUse hook (matcher: Edit|Write|MultiEdit) — plan-gate 강제.

동작 (D1/D2/D5/D7):
  1. 첫 Edit 직전: working tree clean이면 lightweight tag 생성 (롤백 지점)
  2. 호출마다 edit_count / unique_files / multi_edit_max 누적
  3. gate.state == "created" 단계에서 트리거 임계값 초과 시:
       - 현재 dirty 변경을 stash (gate_id 마커)
       - clean tag로 reset (롤백 지점에서 사용자 승인 대기)
       - exit 2로 차단, stderr에 안내 메시지
  4. gate.state == "approved" 단계에서:
       - edit_count_post_approval 누적
       - max(initial+2, 5) 초과 시 차단 (scope creep 방지)
  5. 미해결 gate(verified/created/approved)가 있는데 새 작업 시도하면 D1 lock으로 차단
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_gate_lib as lib  # noqa: E402


def _print_block(msg: str) -> None:
    sys.stderr.write(msg + "\n")


def _gate_pending_summary(gate: dict) -> str:
    return (
        f"id={gate['id']}\n"
        f"  state={gate['state']}\n"
        f"  edits={gate['edit_count']} files={len(gate['unique_files'])} "
        f"multi_max={gate['multi_edit_max']}\n"
        f"  approved_at={gate.get('approved_at') or '-'}\n"
        f"  verifier_status={gate.get('verifier_status') or '-'}"
    )


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {}) or {}

    if tool_name not in ("Edit", "Write", "MultiEdit"):
        return 0

    root = lib.find_project_root()
    if root is None or not lib.is_project_init_managed(root):
        return 0  # plan-gate 비활성화 (project-init 미관리 프로젝트)

    state = lib.load_state(root)
    gate = lib.current_gate(state)

    # ── 첫 Edit 직전 clean tag (D7) ──────────────────────────────────────
    # gate가 아직 없으면 새로 만든다. 그 시점에 working tree clean이면 tag.
    if gate is None or gate["state"] in ("done", "rolled_back"):
        gate = lib.make_gate()
        # 이미 같은 HEAD에 clean tag가 있으면 재사용
        existing = lib.existing_clean_tag_for_head(root)
        if existing:
            gate["checkpoint_clean_tag"] = existing
        elif lib.has_git(root) and lib.working_tree_clean(root):
            tag = lib.create_clean_tag(root, gate["id"])
            if tag:
                gate["checkpoint_clean_tag"] = tag
        lib.set_current_gate(state, gate)

    # ── D1: 미해결 gate에서 새 작업 시도 차단 ────────────────────────────
    # verifier_status가 ❌이고 사용자가 /retry 또는 /rollback 안 한 상태라면
    # 새 plan 트리거 자체를 막는다. 같은 gate 안에서 추가 Edit은 허용.
    if gate["state"] == "verified" and gate.get("verifier_status") == "❌":
        # 이미 verifier가 실패 보고. 사용자 결정 대기 중. Edit 차단.
        msg = (
            "\n[PLAN-GATE LOCK] 이전 작업이 verifier 검증 실패 후 사용자 결정 대기 중.\n"
            f"{_gate_pending_summary(gate)}\n"
            "사용자에게 다음을 요청한다:\n"
            "  /retry   — 같은 체크포인트에서 재시도\n"
            "  /rollback — 체크포인트로 복원\n"
        )
        _print_block(msg)
        lib.save_state(root, state)
        return 2

    # ── 카운터 누적 ──────────────────────────────────────────────────────
    target = lib.extract_target_file(tool_name, tool_input)
    multi_items = lib.count_multi_edit_items(tool_name, tool_input)

    gate["edit_count"] += 1
    if gate["state"] == "approved":
        gate["edit_count_post_approval"] += 1
    if target and target not in gate["unique_files"]:
        gate["unique_files"].append(target)
    if multi_items > gate["multi_edit_max"]:
        gate["multi_edit_max"] = multi_items

    # ── 트리거 평가 ──────────────────────────────────────────────────────
    if gate["state"] == "created" and lib.trigger_threshold_exceeded(gate):
        # 임계값 도달 → 체크포인트 확보 후 차단
        gate["initial_edit_count"] = gate["edit_count"]
        gate["initial_unique_files"] = len(gate["unique_files"])

        # dirty 보존: stash (D4)
        if lib.has_git(root) and not lib.working_tree_clean(root):
            ref = lib.stash_dirty(root, gate["id"])
            if ref:
                gate["checkpoint_dirty_stash_ref"] = ref

        # clean tag가 아직 없으면 지금 stash 후 HEAD에 tag (이제 working tree clean)
        if not gate.get("checkpoint_clean_tag") and lib.has_git(root):
            tag = lib.create_clean_tag(root, gate["id"])
            if tag:
                gate["checkpoint_clean_tag"] = tag

        # todo.md 해시 캡처 (다음 /approve-plan 검증용)
        sha, mtime = lib.hash_todo_md(root)
        gate["todo_md_sha256"] = sha
        gate["todo_md_mtime"] = mtime

        lib.set_current_gate(state, gate)
        lib.save_state(root, state)

        msg = (
            f"\n[PLAN-GATE TRIGGERED] 복잡도 임계값 도달 — 사용자 계획 승인 필요.\n"
            f"  edits={gate['edit_count']} files={len(gate['unique_files'])} "
            f"multi_max={gate['multi_edit_max']}\n"
            f"  gate_id={gate['id']}\n"
            f"  checkpoint_tag={gate.get('checkpoint_clean_tag') or '(no git)'}\n"
            f"  dirty_stash={gate.get('checkpoint_dirty_stash_ref') or '(clean)'}\n"
            "\n필수 행동 (Claude):\n"
            "  1. tasks/todo.md 에 단계별 계획을 작성한다\n"
            "  2. 사용자에게 계획 검토를 요청한다\n"
            "  3. 사용자가 /approve-plan 입력하면 작업 재개\n"
            "     /replan: 계획 재작성 후 재승인\n"
            "     /rollback: 체크포인트로 복원\n"
        )
        _print_block(msg)
        return 2

    # ── 승인 후 scope creep 차단 (D2) ────────────────────────────────────
    if gate["state"] == "approved" and lib.post_approval_limit_exceeded(gate):
        limit = lib.post_approval_limit(gate)
        msg = (
            f"\n[PLAN-GATE SCOPE LIMIT] 승인된 계획의 scope를 초과했다.\n"
            f"  edits_post_approval={gate['edit_count_post_approval']} >= limit={limit}\n"
            f"  initial_edit_count={gate.get('initial_edit_count')}\n"
            "\n필수 행동:\n"
            "  /done    — 현재까지를 완료로 마감\n"
            "  /replan  — 계획을 갱신하고 재승인\n"
            "  /rollback — 체크포인트로 복원\n"
        )
        _print_block(msg)
        lib.save_state(root, state)
        return 2

    # ── 통과 ────────────────────────────────────────────────────────────
    lib.save_state(root, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
