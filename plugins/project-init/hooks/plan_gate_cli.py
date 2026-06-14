#!/usr/bin/env python3
"""plan-gate CLI — 슬래시 커맨드 백엔드 + UserPromptSubmit dispatcher 공용.

usage: plan_gate_cli.py {approve|done|rollback|retry|replan|status}

출력 채널: 사용자전용 (훅이 아닌 CLI — stdout 사람용 메시지.
UserPromptSubmit 경유 시 plan_approval.py 가 출력을 그대로 전달)

각 액션은 idempotent하게 동작한다 (같은 결과를 반복 호출해도 안전).
exit 0: 성공, exit 1: 잘못된 상태 전이.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_gate_lib as lib  # noqa: E402


def _get_feature_hint(root) -> str:
    """tasks/todo.md 첫 의미 있는 줄에서 작업 이름 추출."""
    from pathlib import Path as _Path

    todo = _Path(root) / "tasks" / "todo.md"
    if todo.exists():
        for line in todo.read_text(errors="ignore").splitlines():
            text = line.strip().lstrip("#").strip()
            if text:
                return text[:50]
    return "현재 작업"


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
        gate["approved_auto"] = False  # 명시 선승인 — 자동 승인 sticky 방지
        gate["initial_edit_count"] = 0
        gate["initial_unique_files"] = 0
        gate["edit_count_post_approval"] = 0
        lib.set_current_gate(state, gate)
        lib.save_state(root, state)
        _info(
            f"[plan-gate approve] 선승인 완료: {gate['id']}\n"
            f"  tasks/todo.md 계획 확인 후 작업을 시작하세요.\n"
            f"  임계값: 단일 파일 {lib.TRIGGER_REPEAT_RATIO}회 반복"
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
            "\n"
            "  👉 계획을 의도적으로 수정했다면: 다시 /approve-plan 을 입력하면 통과됩니다.\n"
            "  👉 계획을 처음부터 다시 짜려면: /replan 입력 후 todo.md 작성 → /approve-plan."
        )
        # 새 해시로 갱신해 두 번째 /approve-plan에서는 통과시킨다 (사용자 의도 추정)
        gate["todo_md_sha256"] = current_sha
        gate["todo_md_mtime"] = current_mtime
        lib.save_state(root, state)
        return 1

    gate["state"] = "approved"
    gate["approved_at"] = lib.now_iso()
    gate["approved_auto"] = False  # 명시 승인 — 자동 승인 sticky 해제
    gate["edit_count_post_approval"] = 0
    if gate.get("initial_edit_count") is None:
        gate["initial_edit_count"] = gate["edit_count"]
        gate["initial_unique_files"] = len(gate["unique_files"])

    # 스냅샷 백엔드는 working tree 를 건드리지 않으므로(stash 안 함)
    # approve 후 복원할 것이 없다 — 작업 파일은 그대로 유지된다.
    lib.save_state(root, state)
    _info(
        f"[plan-gate approve] 승인 완료: {gate['id']}\n"
        f"  임계값: 단일 파일 {lib.TRIGGER_REPEAT_RATIO}회 반복 (scope creep 방지)"
    )
    return 0


def _recover_verifier_from_file(root, gate, state) -> bool:
    """update_docs.py가 gate 업데이트를 놓쳤을 때 verifier_result.json에서 직접 복구."""
    import json
    from pathlib import Path as _Path

    result_path = _Path(root) / "docs" / ".verifier_result.json"
    if not result_path.exists():
        return False
    try:
        verdict = json.loads(result_path.read_text(encoding="utf-8")).get("verdict")
    except Exception:
        return False
    if verdict not in ("✅", "❌"):
        return False
    gate["state"] = "verified"
    gate["verifier_status"] = verdict
    lib.save_state(root, state)
    _info(f"[plan-gate done] verifier_result.json에서 상태 복구: {verdict}")
    return True


def _done_from_created(root, state, gate) -> int:
    """created(승인 전) 상태에서 verifier 요구 없이 우아하게 마감.

    cp·문서 위주 작업이 승인 절차를 건드리지 않고 끝났을 때 /done 이 거부되던
    갭(리포트 260612 #2) 해소. working tree 는 건드리지 않는다(변경 보존).
    """
    has_cp = bool(gate.get("checkpoint_commit") or gate.get("cp_snapshot"))
    lib.do_gate_done(root, state, gate)
    tail = (
        "  체크포인트를 정리했습니다."
        if has_cp
        else "  정리할 체크포인트가 없습니다 — 승인·검증 절차 없이 종료합니다."
    )
    _info(f"[plan-gate done] 승인 전(created) 상태에서 마감합니다: {gate['id']}\n{tail}")
    return 0


def cmd_done(root, state) -> int:
    gate = _need_gate(state, "done")
    if gate is None:
        return 0

    if gate["state"] == "done":
        _info("[plan-gate done] 이미 완료됨.")
        return 0

    # created(승인 전): verifier 요구 없이 우아하게 마감 (리포트 260612 #2)
    if gate["state"] == "created":
        return _done_from_created(root, state, gate)

    if gate["state"] not in ("approved", "verified"):
        _err(f"[plan-gate done] 현재 상태 '{gate['state']}'에서는 완료 불가.")
        return 1

    if gate.get("verifier_status") is None:
        # update_docs.py가 gate 업데이트를 놓친 경우 — verifier_result.json에서 직접 복구
        recovered = _recover_verifier_from_file(root, gate, state)
        if not recovered:
            _err(
                "[plan-gate done] verifier 미검증 — 완료 불가.\n"
                "  @verifier 호출 → docs/.verifier_result.json 생성 후 다시 /done.\n"
                "  의도적으로 건너뛰려면 /skip-verify 를 명시적으로 입력."
            )
            return 1

    # F-2: verified+❌ 상태에서 /done 시 보존 의도 명시
    if gate["state"] == "verified" and gate.get("verifier_status") == "❌":
        _info(
            "[plan-gate done] ⚠️  verifier 검증 실패 상태에서 완료 처리됩니다.\n"
            "  발견된 문제를 인지한 채로 현재 변경사항을 보존합니다.\n"
            "  체크포인트는 정리됩니다. 이후 별도 수정이 필요합니다."
        )

    edit_count = gate.get("edit_count", 0)
    lib.do_gate_done(root, state, gate)
    _info(f"[plan-gate done] 작업 완료. 체크포인트 정리됨: {gate['id']}")

    # compact 권고: 편집이 5회 이상이면 컨텍스트 관리 안내
    if edit_count >= 5:
        feature_hint = _get_feature_hint(root)
        _info(
            "\n💡 컨텍스트 관리: /compact 실행을 권장합니다.\n"
            "  compact 후 새 작업 시작 시 입력하세요:\n"
            f"  「{feature_hint} 완료됨. 다음 작업 지시해줘.」"
        )
    return 0


def cmd_skip(root, state) -> int:
    """verified+❌ 상태에서 현재 변경사항을 보존하며 gate를 마감한다."""
    gate = _need_gate(state, "skip")
    if gate is None:
        return 0

    if gate["state"] == "done":
        _info("[plan-gate skip] 이미 완료됨.")
        return 0

    if gate["state"] != "verified" or gate.get("verifier_status") != "❌":
        _err(
            f"[plan-gate skip] 현재 상태 '{gate['state']}' verifier='{gate.get('verifier_status')}'\n"
            "  /skip 은 verifier ❌ 후에만 사용한다. 정상 완료는 /done 을 쓰세요."
        )
        return 1

    _info(
        "[plan-gate skip] verifier 검증 실패 상태를 인지하고 현재 변경사항을 보존합니다.\n"
        "  발견된 문제는 다음 gate 주기에서 별도 처리하세요.\n"
        "  체크포인트는 정리됩니다."
    )
    lib.do_gate_done(root, state, gate)
    _info(f"[plan-gate skip] gate 마감 완료: {gate['id']}")
    return 0


def cmd_skip_verify(root, state) -> int:
    """verifier 검증을 의도적으로 건너뛰고 완료 처리."""
    gate = _need_gate(state, "skip-verify")
    if gate is None:
        return 0

    if gate["state"] == "done":
        _info("[plan-gate skip-verify] 이미 완료됨.")
        return 0

    if gate["state"] not in ("approved", "verified"):
        _err(f"[plan-gate skip-verify] 현재 상태 '{gate['state']}'에서는 사용 불가.")
        return 1

    # 이미 verifier 판정이 있으면 ⏭️ 로 덮어쓰지 않는다 — 기록 왜곡 방지
    # (❌ 를 ⏭️ 로 바꾸면 "검증 실패" 사실이 "건너뜀"으로 둔갑한다)
    existing = gate.get("verifier_status")
    if existing == "❌":
        _err(
            "[plan-gate skip-verify] verifier ❌ 판정이 이미 있습니다 — 사용 불가.\n"
            "  실패 기록을 보존하며 마감하려면 /skip, 재시도는 /retry, 되돌리기는 /rollback."
        )
        return 1
    if existing == "✅":
        _err("[plan-gate skip-verify] verifier ✅ 판정이 이미 있습니다 — /done 을 쓰세요.")
        return 1

    _info(
        "[plan-gate skip-verify] ⚠️  verifier 검증 없이 완료 처리합니다.\n"
        "  건너뛴 사실이 gate 기록에 남습니다. 체크포인트는 정리됩니다."
    )
    gate["verifier_status"] = "⏭️"
    lib.do_gate_done(root, state, gate)
    _info(f"[plan-gate skip-verify] gate 마감 완료: {gate['id']}")
    return 0


def cmd_rollback(root, state) -> int:
    gate = _need_gate(state, "rollback")
    if gate is None:
        return 0

    if not lib.rollback_checkpoint(root, gate):
        _err(
            "[plan-gate rollback] 복원할 체크포인트가 없습니다.\n"
            "  편집 전 스냅샷이 없어 되돌릴 수 없습니다.\n"
            "\n"
            "  대안:\n"
            "    /skip  — 현재 변경사항 보존하며 gate 마감 (문제 인지 후 유지)\n"
            "    /done  — 동일 효과 (/skip 과 같음)"
            + (
                "\n  수동 복원이 필요하면: git reflog 로 직전 상태를 찾아 복구하세요."
                if lib.has_git(root)
                else ""
            )
        )
        return 1

    gate["state"] = "rolled_back"
    lib.clear_current_gate(state)
    lib.save_state(root, state)
    _info(
        "[plan-gate rollback] 체크포인트로 복원 완료 — 편집 전 상태로 되돌렸습니다.\n"
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
    # 재시도 = 새 시도 → thrash 카운터 리셋(이전 시도의 반복이 즉시 재차단하지 않도록).
    gate["edit_count_post_approval"] = 0
    gate["file_edit_counts"] = {}
    lib.save_state(root, state)
    _info(
        f"[plan-gate retry] 같은 체크포인트에서 재시도 시작: {gate['id']}\n"
        f"  반복 카운터 리셋"
    )
    return 0


def cmd_replan(root, state) -> int:
    gate = _need_gate(state, "replan")
    if gate is None:
        return 0

    # 체크포인트 유지, 카운터/상태만 리셋 → tasks/todo.md 다시 작성하고 /approve-plan
    gate["state"] = "created"
    gate["approved_auto"] = False  # 명시 재승인 대기 — 자동 승인 sticky 해제
    gate["edit_count"] = 0
    gate["edit_count_post_approval"] = 0
    gate["file_edit_counts"] = {}  # thrash 카운터도 리셋(이전 리뷰: replan 미리셋 버그)
    gate["unique_files"] = []
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


def cmd_no_git(root, state) -> int:
    if lib.prefers_no_git(root):
        _info("[plan-gate no-git] 이미 cp 스냅샷 모드입니다.")
    else:
        lib.set_prefer_no_git(root)
        _info(
            "[plan-gate no-git] git 체크포인트를 끄고 cp 스냅샷 모드로 전환했습니다.\n"
            "  (.claude/plan_gate_no_git 생성) 이후 체크포인트는 .claude/state/checkpoints/ 에\n"
            "  파일 복사로 만들어지며 git tag/stash 를 쓰지 않습니다. 되돌리려면 /plan-gate-use-git."
        )
    return 0


def cmd_use_git(root, state) -> int:
    if not lib.prefers_no_git(root):
        _info("[plan-gate use-git] 이미 git 체크포인트 모드입니다 (git repo 인 경우).")
    else:
        lib.unset_prefer_no_git(root)
        _info("[plan-gate use-git] git 체크포인트 모드로 복귀했습니다. (.claude/plan_gate_no_git 삭제)")
    return 0


_NEXT_ACTION = {
    "created": "→ 다음 액션: tasks/todo.md 작성 → /approve-plan",
    "approved": "→ 다음 액션: 작업 진행 → @verifier 호출 → /done | /rollback",
    "verified_ok": "→ 다음 액션: /done (완료) 또는 /rollback (되돌리기)",
    "verified_fail": "→ 다음 액션: /retry (재구현) | /skip (현 상태 보존) | /rollback",
    "done": "→ gate 완료 상태입니다.",
    "rolled_back": "→ rollback 완료 상태입니다.",
}


def cmd_status(root, state) -> int:
    gate = lib.current_gate(state)
    if gate is None:
        _info("[plan-gate status] 활성 gate 없음.")
        return 0
    g_state = gate["state"]
    verifier = gate.get("verifier_status")
    if g_state == "verified":
        action_key = "verified_ok" if verifier == "✅" else "verified_fail"
    else:
        action_key = g_state
    next_action = _NEXT_ACTION.get(action_key, "")
    _info(
        f"[plan-gate status]\n"
        f"  id              = {gate['id']}\n"
        f"  state           = {g_state}\n"
        f"  edits           = {gate['edit_count']}\n"
        f"  thrash(반복)    = {lib._max_code_repeat(gate)}/{lib.TRIGGER_REPEAT_RATIO} (green Bash 시 리셋)\n"
        f"  unique_files    = {len(gate['unique_files'])}\n"
        f"  approved_at     = {gate.get('approved_at') or '-'}\n"
        f"  approved_auto   = {'yes' if gate.get('approved_auto') else 'no (명시 승인)'}\n"
        f"  verifier_status = {verifier or '-'}\n"
        f"  checkpoint      = {(gate.get('checkpoint_commit') or '')[:12] or ('cp' if gate.get('cp_snapshot') else '-')}\n"
        f"\n"
        f"  {next_action}"
    )
    return 0


COMMANDS = {
    "approve": cmd_approve,
    "done": cmd_done,
    "skip": cmd_skip,
    "skip-verify": cmd_skip_verify,
    "rollback": cmd_rollback,
    "retry": cmd_retry,
    "replan": cmd_replan,
    "status": cmd_status,
    "on": cmd_on,
    "off": cmd_off,
    "no-git": cmd_no_git,
    "use-git": cmd_use_git,
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
