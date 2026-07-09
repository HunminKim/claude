#!/usr/bin/env python3
"""plan-gate CLI — 슬래시 커맨드 백엔드 + UserPromptSubmit dispatcher 공용.

usage: plan_gate_cli.py {approve|done|skip|skip-verify|rollback|retry|replan|status
                         |on|off|no-git|use-git|scope-off|scope-shadow|scope-enforce|subplan}
(실제 액션 목록은 COMMANDS dict 가 단일 진실 원천 — usage 문자열은 거기서 생성된다)

출력 채널: 사용자전용 (훅이 아닌 CLI — stdout 사람용 메시지.
UserPromptSubmit 경유 시 plan_approval.py 가 출력을 그대로 전달)

각 액션은 idempotent하게 동작한다 (같은 결과를 반복 호출해도 안전).
exit 0: 성공, exit 2: usage 오류 / 프로젝트 루트 없음 / plan-gate 관리 대상 아님.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_gate_lib as lib  # noqa: E402

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _get_feature_hint(root) -> str:
    """tasks/todo.md 첫 의미 있는 줄에서 작업 이름 추출."""
    from pathlib import Path as _Path

    todo = _Path(root) / "tasks" / "todo.md"
    if todo.exists():
        for line in todo.read_text(encoding="utf-8", errors="ignore").splitlines():
            text = line.strip().lstrip("#").strip()
            if text:
                return text[:50]
    return "현재 작업"


def _scope_note(manifest, root) -> str:
    """매니페스트 선언 시 스코프 계약 저장 + 현재 강제 모드 안내."""
    if not manifest:
        return ""
    mode = lib.scope_mode(root)
    tail = {
        "off": "강제 off — 기록만 (켜기: /plan-gate-scope-shadow|enforce)",
        "shadow": "강제 shadow — 위반 감지·기록만",
        "enforce": "강제 enforce — 스코프 밖 편집 거부 + Bash 롤백",
    }[mode]
    return f"\n  스코프 계약: {len(manifest['scope'])}개 패턴 저장됨 ({tail})"


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


def _approve_fresh_gate(root, state) -> int:
    """선승인(편집 전 /approve-plan): 즉시 approved gate 생성.

    첫 편집이 gate 를 열기 전에 승인하므로 plan_gate.py 의 첫-편집 스냅샷 분기를
    못 탄다. 여기서 직접 캡처하지 않으면 checkpoint 가 없어 layer-2 enforce 가 무백업
    삭제 방지로 영구 shadow 강등된다(H-2) — enforce 를 켜도 Bash 스코프밖 생성물이
    롤백 안 되는 설정·동작 불일치.
    """
    gate = lib.make_gate()
    if not lib.prefers_no_git(root):
        commit = lib.create_snapshot(root, gate)
        if commit:
            gate["checkpoint_commit"] = commit
    sha, mtime = lib.hash_todo_md(root)
    gate["todo_md_sha256"] = sha
    gate["todo_md_mtime"] = mtime
    lib.transition(gate, "approve_manual")  # fresh gate(created)→approved
    manifest = lib.apply_manifest(root, gate)  # 사람 승인 — 넓은 글롭 가드 우회
    lib.set_current_gate(state, gate)
    lib.save_state(root, state)
    _info(
        f"[plan-gate approve] 선승인 완료: {gate['id']}\n"
        f"  tasks/todo.md 계획 확인 후 작업을 시작하세요.\n"
        f"  임계값: 단일 파일 {lib.TRIGGER_REPEAT_RATIO}회 반복"
        + _scope_note(manifest, root)
    )
    return 0


def cmd_approve(root, state) -> int:
    gate = lib.current_gate(state)

    # gate 없음 = 아직 편집 시작 전 → 선승인 (스냅샷 포함)
    if gate is None or gate["state"] in ("done", "rolled_back"):
        return _approve_fresh_gate(root, state)

    if gate["state"] == "approved":
        _info(f"[plan-gate approve] 이미 승인됨: {gate['id']}")
        return 0

    if gate["state"] != "created":
        _err(f"[plan-gate approve] 현재 상태 '{gate['state']}'에서는 승인 불가.")
        return 1

    # todo.md 해시 검증 (gate 발동 시 캡처한 값과 비교)
    expected = gate.get("todo_md_sha256")
    current_sha, current_mtime = lib.hash_todo_md(root)

    # 기준점 부재 + todo.md 존재 = "게이트가 열린 뒤 관측 밖 경로로 작성됨".
    # gate 발동 시 todo.md 가 없었고(캡처 대상 없음) 이후 Write/Edit 도 한 번도 안 거쳤다는
    # 뜻 — Bash heredoc·외부 에디터로만 쓰인 계획이다. 추적 밖 '수정'(아래 해시 불일치)은
    # 잡으면서 '생성'만 침묵하던 비대칭을 없앤다. rearm 은 아래와 동일 기계를 쓴다.
    if expected is None and current_sha is not None:
        _err(
            "[plan-gate approve] tasks/todo.md가 plan-gate 관측 밖 경로로 작성됨.\n"
            f"  current_sha256={current_sha[:12]}…\n"
            "\n"
            "  👉 계획 내용을 확인했다면: 다시 /approve-plan 을 입력하면 통과됩니다.\n"
            "  👉 계획을 다시 짜려면: /replan 입력 후 todo.md 작성 → /approve-plan."
        )
        gate["todo_md_sha256"] = current_sha
        gate["todo_md_mtime"] = current_mtime
        lib.save_state(root, state)
        return 1

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

    lib.transition(gate, "approve_manual")  # created→approved (initial 누적치 보존)
    manifest = lib.apply_manifest(root, gate)  # 사람 승인 — 넓은 글롭 가드 우회

    # 스냅샷 백엔드는 working tree 를 건드리지 않으므로(stash 안 함)
    # approve 후 복원할 것이 없다 — 작업 파일은 그대로 유지된다.
    lib.save_state(root, state)
    _info(
        f"[plan-gate approve] 승인 완료: {gate['id']}\n"
        f"  임계값: 단일 파일 {lib.TRIGGER_REPEAT_RATIO}회 반복 (scope creep 방지)"
        + _scope_note(manifest, root)
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
        verdict = json.loads(result_path.read_text(encoding="utf-8", errors="ignore")).get("verdict")
    except Exception:
        return False
    if verdict not in ("✅", "❌"):
        return False
    try:
        lib.enter_verified(gate, verdict)  # verified 진입 단일 출처 (직접 대입 금지)
    except ValueError:
        return False  # cmd_done 가드상 도달 불가 상태 — 복구 대신 미검증 경로로
    lib.save_state(root, state)
    _info(f"[plan-gate done] verifier_result.json에서 상태 복구: {verdict}")
    return True


def _notify_scope_revert(reverted: bool) -> None:
    """게이트 닫힘 시 enforce→shadow 자동 복귀가 일어났으면 사용자에게 환기(침묵 금지)."""
    if reverted:
        _info(
            "[plan-gate] 스코프 강제(enforce) → shadow 자동 복귀.\n"
            "  게이트가 닫혀, 이번 작업용 enforce 가 다음 작업에서 신규 파일을 삭제·차단하는\n"
            "  stale enforce 사고를 막습니다. 계속 강제하려면 /plan-gate-scope-enforce 재입력."
        )


def _done_from_created(root, state, gate) -> int:
    """created(승인 전) 상태에서 verifier 요구 없이 우아하게 마감.

    cp·문서 위주 작업이 승인 절차를 건드리지 않고 끝났을 때 /done 이 거부되던
    갭(리포트 260612 #2) 해소. working tree 는 건드리지 않는다(변경 보존).
    """
    has_cp = bool(gate.get("checkpoint_commit") or gate.get("cp_snapshot"))
    _reverted = lib.do_gate_done(root, state, gate)
    tail = (
        "  체크포인트를 정리했습니다."
        if has_cp
        else "  정리할 체크포인트가 없습니다 — 승인·검증 절차 없이 종료합니다."
    )
    _info(f"[plan-gate done] 승인 전(created) 상태에서 마감합니다: {gate['id']}\n{tail}")
    _notify_scope_revert(_reverted)
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
    _reverted = lib.do_gate_done(root, state, gate)
    _info(f"[plan-gate done] 작업 완료. 체크포인트 정리됨: {gate['id']}")
    _notify_scope_revert(_reverted)

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
    _reverted = lib.do_gate_done(root, state, gate)
    _info(f"[plan-gate skip] gate 마감 완료: {gate['id']}")
    _notify_scope_revert(_reverted)
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
    _reverted = lib.do_gate_done(root, state, gate)
    _info(f"[plan-gate skip-verify] gate 마감 완료: {gate['id']}")
    _notify_scope_revert(_reverted)
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

    # rolled_back 마감 단일 출처 (상태·closed_at·enforce 청소 — 직접 대입 금지)
    _reverted = lib.do_gate_rolled_back(root, state, gate)
    _info(
        "[plan-gate rollback] 체크포인트로 복원 완료 — 편집 전 상태로 되돌렸습니다.\n"
        f"  gate {gate['id']} → rolled_back."
    )
    _notify_scope_revert(_reverted)
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

    # 체크포인트·계획 유지, 같은 gate를 다시 approved 로 돌려 재구현 허용.
    # 재시도 = 새 시도 → thrash 카운터 리셋(이전 시도 반복이 즉시 재차단하지 않도록).
    lib.transition(gate, "retry")
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

    # 체크포인트만 유지, 카운터·계획·매니페스트 전부 리셋 → todo.md 재작성 후 /approve-plan.
    # (전이별 리셋 집합은 lib.transition 의 replan 분기가 단일 출처)
    lib.transition(gate, "replan")
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
            "  파일 복사로 만들어지며 git 에 스냅샷을 만들지 않습니다. 되돌리려면 /plan-gate-use-git."
        )
    return 0


def cmd_use_git(root, state) -> int:
    if not lib.prefers_no_git(root):
        _info("[plan-gate use-git] 이미 git 체크포인트 모드입니다 (git repo 인 경우).")
    else:
        lib.unset_prefer_no_git(root)
        _info("[plan-gate use-git] git 체크포인트 모드로 복귀했습니다. (.claude/plan_gate_no_git 삭제)")
    return 0


def _set_scope_mode(root, mode: str) -> int:
    cur = lib.scope_mode(root)
    if cur == mode:
        _info(f"[plan-gate scope] 이미 '{mode}' 모드입니다.")
        return 0
    lib.set_scope_mode(root, mode)
    desc = {
        "off": "스코프 강제 끔 — 매니페스트는 기록만(thrash 가드는 유지).",
        "shadow": "감지·기록만 — 위반을 audit 에 남기되 차단·롤백 안 함(롤아웃 관찰용).",
        "enforce": "강제 — 스코프 밖 Edit 거부(layer-1) + Bash 스코프밖 변경 롤백(layer-2).",
    }[mode]
    _info(f"[plan-gate scope] '{cur}' → '{mode}' 전환.\n  {desc}")
    return 0


def cmd_subplan(root, state) -> int:
    """스코프 확장 escape-hatch (Claude 호출 가능). enforce 중 예상 밖 인접 파일을
    audit 남기며 스코프에 추가한다 — 전면 /replan 없이 진행하되 흔적을 남긴다.

    do-not-touch 는 확장으로도 못 뚫는다(scope_allows deny-first). 인자 없으면
    현재 확장 목록을 표시. 사용: plan_gate_cli.py subplan <패턴> [패턴...]
    """
    gate = _need_gate(state, "subplan")
    if gate is None:
        return 0
    patterns = [a.strip() for a in sys.argv[2:] if a.strip()]
    if not patterns:
        exps = gate.get("expansions") or []
        _info(
            f"[plan-gate subplan] 현재 확장 패턴 {len(exps)}개: {', '.join(exps) or '(없음)'}\n"
            "  사용법: /subplan <패턴> [패턴...] — 승인 스코프에 파일 패턴을 추가합니다."
        )
        return 0
    if not lib.has_manifest(gate):
        _err(
            "[plan-gate subplan] 스코프 매니페스트가 없습니다 — 확장할 계약이 없습니다.\n"
            "  tasks/todo.md 에 scope 선언 + /plan-gate-scope-enforce 후 의미가 있습니다."
        )
        return 1
    # 넓은 글롭 확장 거부(M-1) — 자동승인 broad-glob 가드와 일관. `**`·최상위 글롭은
    # 스코프 계약을 형해화하므로 디렉토리로 한정하게 강제한다.
    broad = [p for p in patterns if lib.is_broad_glob(p)]
    if broad:
        _err(
            f"[plan-gate subplan] 넓은 글롭은 확장 불가: {', '.join(broad)}\n"
            "  `**`·최상위 글롭은 스코프를 사실상 무력화합니다. 디렉토리로 한정하세요"
            " (예: src/util/**). 계획 자체를 넓혀야 하면 /replan 을 쓰세요."
        )
        return 1
    # F-009: deny-first 를 입력 단계에 적용 — do-not-touch 와 겹치는 패턴은 expansions 에
    # 넣지 않는다. (과거: enforcement 에서만 막아 do-not-touch 도 일단 추가되고 "확장됨"
    # 으로 오인 출력 + audit 에 죽은 데이터 누적.) 입력 거부가 "추가하되 무력화"보다 명료.
    dnt = gate.get("do_not_touch") or []
    denied = [p for p in patterns if lib.expansion_hits_deny(p, dnt)]
    allowed = [p for p in patterns if p not in denied]
    if denied:
        lib.log_audit(root, "subplan_denied", gate_id=gate["id"], denied=denied)

    exps = gate.setdefault("expansions", [])
    existing = set(exps) | set(gate.get("scope", []))
    added = [p for p in allowed if p not in existing]
    exps.extend(added)
    lib.save_state(root, state)
    if added:
        lib.log_audit(root, "subplan_expand", gate_id=gate["id"], added=added, total=len(exps))

    if denied and not added:
        _err(
            f"[plan-gate subplan] 거부됨(do-not-touch): {', '.join(denied)}\n"
            "  do-not-touch 패턴은 확장으로 뚫을 수 없습니다. 편집이 필요하면 /replan 으로 계획을 다시 짜세요."
        )
        return 1
    deny_note = f"\n  ⛔ 거부됨(do-not-touch): {', '.join(denied)}" if denied else ""
    _info(
        f"[plan-gate subplan] 스코프 확장: {', '.join(added) if added else '(이미 포함 — 변화 없음)'}\n"
        f"  현재 강제={lib.scope_mode(root)} / 확장 누계 {len(exps)}개. audit 에 기록됨 — "
        "사용자가 최종 diff 로 검토합니다." + deny_note + "\n"
        "  ⚠️  do-not-touch 패턴은 확장으로도 허용되지 않습니다. /replan 시 확장은 초기화됩니다."
    )
    return 0


def cmd_scope_off(root, state) -> int:
    return _set_scope_mode(root, "off")


def cmd_scope_shadow(root, state) -> int:
    return _set_scope_mode(root, "shadow")


def cmd_scope_enforce(root, state) -> int:
    return _set_scope_mode(root, "enforce")


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
    scope = gate.get("scope") or []
    mode = lib.scope_mode(root)
    mode_label = {
        "off": "off (기록만)",
        "shadow": "shadow (감지·기록)",
        "enforce": "enforce (차단·롤백)",
    }[mode]
    exps = gate.get("expansions") or []
    exp_note = f" (+확장 {len(exps)})" if exps else ""
    scope_line = (
        f"{len(scope)}개 패턴{exp_note} / 강제={mode_label}"
        if scope
        else f"없음 (thrash-only) / 강제={mode_label}"
    )
    _info(
        f"[plan-gate status]\n"
        f"  id              = {gate['id']}\n"
        f"  state           = {g_state}\n"
        f"  edits           = {gate['edit_count']}\n"
        f"  thrash(반복)    = {lib._max_code_repeat(gate)}/{lib.TRIGGER_REPEAT_RATIO} (green Bash 시 리셋)\n"
        f"  scope           = {scope_line}\n"
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
    "scope-off": cmd_scope_off,
    "scope-shadow": cmd_scope_shadow,
    "scope-enforce": cmd_scope_enforce,
    "subplan": cmd_subplan,
}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in COMMANDS:
        _err(f"usage: {argv[0]} {{{'|'.join(COMMANDS)}}}")
        return 2

    root = lib.find_project_root()
    if root is None:
        _err("[plan-gate cli] 프로젝트 루트를 찾을 수 없다 (CLAUDE_PROJECT_DIR 미설정).")
        return 2
    if not lib.is_plan_gate_manageable(root):
        _err(
            "[plan-gate cli] plan-gate 관리 대상이 아니다 "
            "(.claude/plan_gate_enabled 도 .claude/agents/verifier.md 도 없음)."
        )
        return 2

    state = lib.load_state(root)
    return COMMANDS[argv[1]](root, state)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
