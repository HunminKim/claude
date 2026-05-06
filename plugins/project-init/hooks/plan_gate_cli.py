#!/usr/bin/env python3
"""plan-gate CLI — 슬래시 커맨드 백엔드 + UserPromptSubmit dispatcher 공용.

usage: plan_gate_cli.py {approve|done|rollback|retry|replan|status}

각 액션은 idempotent하게 동작한다 (같은 결과를 반복 호출해도 안전).
출력은 stdout으로 사람이 읽을 수 있는 메시지를 작성한다.
exit 0: 성공, exit 1: 잘못된 상태 전이.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_gate_lib as lib  # noqa: E402


def _info(msg: str) -> None:
    sys.stdout.write(msg + "\n")


def _err(msg: str) -> None:
    sys.stderr.write(msg + "\n")


def _need_gate(state, action: str):
    gate = lib.current_gate(state)
    if gate is None:
        _err(f"[plan-gate {action}] 활성 gate 없음 — 무시.")
        return None
    return gate


def cmd_approve(root, state) -> int:
    gate = lib.current_gate(state)

    # gate 없음 = 아직 편집 시작 전 → 선승인: 즉시 approved gate 생성
    if gate is None or gate["state"] in ("done", "rolled_back"):
        gate = lib.make_gate()
        sha, mtime = lib.hash_todo_md(root)
        gate["todo_md_sha256"] = sha
        gate["todo_md_mtime"] = mtime
        gate["state"] = "approved"
        gate["approved_at"] = lib.now_iso()
        gate["initial_edit_count"] = 0
        gate["initial_unique_files"] = 0
        lib.set_current_gate(state, gate)
        lib.save_state(root, state)
        _info(
            f"[plan-gate approve] 선승인 완료: {gate['id']}\n"
            f"  tasks/todo.md 계획 확인 후 작업을 시작하세요.\n"
            f"  limit={lib.post_approval_limit(gate)} edits (scope creep 방지)"
        )
        return 0

    if gate["state"] == "approved":
        _info(f"[plan-gate approve] 이미 승인됨: {gate['id']}")
        return 0

    if gate["state"] != "created":
        _err(f"[plan-gate approve] 현재 상태 '{gate['state']}'에서는 승인 불가.")
        return 1

    # todo.md 해시 검증 (gate 발동 시 캡처한 값과 비교)
    expected = gate.get("todo_md_sha256")
    current_sha, current_mtime = lib.hash_todo_md(root)
    if expected and current_sha != expected:
        _err(
            "[plan-gate approve] tasks/todo.md가 gate 발동 후 변경됨.\n"
            f"  expected_sha256={expected[:12]}…\n"
            f"  current_sha256 ={current_sha[:12] if current_sha else 'None'}…\n"
            "  /replan 으로 새 계획 + 재승인하거나, 의도된 변경이면 다시 /approve-plan."
        )
        # 새 해시로 갱신해 두 번째 /approve-plan에서는 통과시킨다 (사용자 의도 추정)
        gate["todo_md_sha256"] = current_sha
        gate["todo_md_mtime"] = current_mtime
        lib.save_state(root, state)
        return 1

    gate["state"] = "approved"
    gate["approved_at"] = lib.now_iso()
    gate["edit_count_post_approval"] = 0
    if gate.get("initial_edit_count") is None:
        gate["initial_edit_count"] = gate["edit_count"]
        gate["initial_unique_files"] = len(gate["unique_files"])
    lib.save_state(root, state)
    _info(
        f"[plan-gate approve] 승인 완료: {gate['id']}\n"
        f"  initial_edits={gate['initial_edit_count']} "
        f"limit={lib.post_approval_limit(gate)} (scope creep 방지)"
    )
    return 0


def cmd_done(root, state) -> int:
    gate = _need_gate(state, "done")
    if gate is None:
        return 0

    if gate["state"] == "done":
        _info("[plan-gate done] 이미 완료됨.")
        return 0

    if gate["state"] not in ("approved", "verified"):
        _err(f"[plan-gate done] 현재 상태 '{gate['state']}'에서는 완료 불가.")
        return 1

    if lib.post_approval_limit_exceeded(gate):
        _info(
            "[plan-gate done] ⚠️  scope creep 상태에서 완료 처리됩니다.\n"
            "  승인된 계획 범위를 초과한 작업이 포함됩니다. 의도된 경우 계속 진행하세요."
        )

    lib.do_gate_done(root, state, gate)
    _info(f"[plan-gate done] 작업 완료. 체크포인트 정리됨: {gate['id']}")
    return 0


def cmd_rollback(root, state) -> int:
    gate = _need_gate(state, "rollback")
    if gate is None:
        return 0

    tag = gate.get("checkpoint_clean_tag")
    if not tag:
        _err(
            "[plan-gate rollback] 체크포인트 tag가 없다 (git 미사용 또는 tag 생성 실패).\n"
            "  복원 불가 — 수동으로 되돌려야 한다."
        )
        return 1

    if not lib.reset_to_tag(root, tag):
        _err(f"[plan-gate rollback] git reset --hard {tag} 실패.")
        return 1

    # 잃은 dirty 변경 stash는 유지 (사용자가 수동 git stash pop 가능)
    stash_ref = lib.find_stash_for_gate(root, gate["id"])
    stash_msg = ""
    if stash_ref:
        stash_msg = f"\n  잃은 변경은 {stash_ref} stash에 보존됨 (git stash list 로 확인)."

    # tag 삭제 (이미 reset 했으니 더 이상 필요 없음)
    lib.delete_tag(root, tag)

    gate["state"] = "rolled_back"
    lib.clear_current_gate(state)
    lib.save_state(root, state)
    _info(
        f"[plan-gate rollback] 체크포인트로 복원 완료: {tag}{stash_msg}\n"
        f"  gate {gate['id']} → rolled_back."
    )
    return 0


def cmd_retry(root, state) -> int:
    gate = _need_gate(state, "retry")
    if gate is None:
        return 0

    if gate["state"] != "verified" or gate.get("verifier_status") != "❌":
        _err(
            f"[plan-gate retry] 현재 상태 '{gate['state']}' verifier='{gate.get('verifier_status')}'\n"
            "  /retry는 verifier ❌ 후에만 사용한다."
        )
        return 1

    # 체크포인트 유지, 같은 gate를 다시 approved 상태로 돌려 재구현 허용
    gate["state"] = "approved"
    gate["verifier_status"] = None
    # post_approval 카운터는 누적 유지 — 무한 retry 방지
    lib.save_state(root, state)
    _info(
        f"[plan-gate retry] 같은 체크포인트에서 재시도 시작: {gate['id']}\n"
        f"  post_approval limit={lib.post_approval_limit(gate)} "
        f"(현재 {gate['edit_count_post_approval']})"
    )
    return 0


def cmd_replan(root, state) -> int:
    gate = _need_gate(state, "replan")
    if gate is None:
        return 0

    # 체크포인트 유지, 카운터/상태만 리셋 → tasks/todo.md 다시 작성하고 /approve-plan
    gate["state"] = "created"
    gate["edit_count"] = 0
    gate["edit_count_post_approval"] = 0
    gate["unique_files"] = []
    gate["multi_edit_max"] = 0
    gate["initial_edit_count"] = None
    gate["initial_unique_files"] = None
    gate["approved_at"] = None
    gate["todo_md_sha256"] = None
    gate["todo_md_mtime"] = None
    gate["verifier_status"] = None
    lib.save_state(root, state)
    wt_dirty = lib.has_git(root) and not lib.working_tree_clean(root)
    _info(
        f"[plan-gate replan] 계획 재작성 모드: {gate['id']}\n"
        "  tasks/todo.md 갱신 후 /approve-plan 입력하라.\n"
        "  체크포인트는 유지된다."
        + ("\n  ⚠️  working tree에 미커밋 변경이 남아있습니다." if wt_dirty else "")
    )
    return 0


def cmd_on(root, state) -> int:
    if lib.is_plan_gate_enabled(root):
        _info("[plan-gate on] 이미 활성화됨.")
    else:
        lib.enable_plan_gate(root)
        _info("[plan-gate on] plan-gate 활성화됨. (.claude/plan_gate_enabled 생성)")
    return 0


def cmd_off(root, state) -> int:
    if not lib.is_plan_gate_enabled(root):
        _info("[plan-gate off] 이미 비활성화됨.")
    else:
        lib.disable_plan_gate(root)
        _info("[plan-gate off] plan-gate 비활성화됨. (.claude/plan_gate_enabled 삭제)")
    return 0


def cmd_status(root, state) -> int:
    gate = lib.current_gate(state)
    if gate is None:
        _info("[plan-gate status] 활성 gate 없음.")
        return 0
    _info(
        f"[plan-gate status]\n"
        f"  id              = {gate['id']}\n"
        f"  state           = {gate['state']}\n"
        f"  edits           = {gate['edit_count']}\n"
        f"  edits_approved  = {gate['edit_count_post_approval']} / {lib.post_approval_limit(gate)}\n"
        f"  unique_files    = {len(gate['unique_files'])}\n"
        f"  multi_edit_max  = {gate['multi_edit_max']}\n"
        f"  approved_at     = {gate.get('approved_at') or '-'}\n"
        f"  approved_auto   = {'yes (보수적 limit)' if gate.get('approved_auto') else 'no (명시 승인)'}\n"
        f"  verifier_status = {gate.get('verifier_status') or '-'}\n"
        f"  clean_tag       = {gate.get('checkpoint_clean_tag') or '-'}\n"
        f"  dirty_stash     = {gate.get('checkpoint_dirty_stash_ref') or '-'}"
    )
    return 0


COMMANDS = {
    "approve": cmd_approve,
    "done": cmd_done,
    "rollback": cmd_rollback,
    "retry": cmd_retry,
    "replan": cmd_replan,
    "status": cmd_status,
    "on": cmd_on,
    "off": cmd_off,
}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in COMMANDS:
        _err(f"usage: {argv[0]} {{{'|'.join(COMMANDS)}}}")
        return 2

    root = lib.find_project_root()
    if root is None:
        _err("[plan-gate cli] 프로젝트 루트를 찾을 수 없다 (CLAUDE_PROJECT_DIR 미설정).")
        return 2
    if not lib.is_project_init_managed(root):
        _err(
            "[plan-gate cli] project-init으로 초기화된 프로젝트가 아니다 (.claude/agents/verifier.md 없음)."
        )
        return 2

    state = lib.load_state(root)
    return COMMANDS[argv[1]](root, state)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
