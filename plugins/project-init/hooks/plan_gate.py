#!/usr/bin/env python3
"""PreToolUse hook (matcher: Edit|Write|MultiEdit|NotebookEdit) — plan-gate 강제.

출력 채널:
- 차단 (D1 lock / 임계 트리거 / approved thrash): exit 2 + stderr — Claude blocking error 주입
- deny (scope enforce layer-1 — 스코프 밖 Edit 거부): exit 0 + stdout hookSpecificOutput.permissionDecision="deny" JSON
- 환기 (계획 감지→승인 유도 / stale gate / 24h 잔류 / hot-file / soft hint / multi-edit hint / scope shadow 위반): exit 0 + stdout hookSpecificOutput.additionalContext JSON — Claude context 주입
- 사용자 터미널 전용 (.plan-gateignore 자동 추가): exit 0 + stderr

동작 (D1/D2/D5/D7 + UX 풍부화):
  1. 첫 Edit 직전: 체크포인트 스냅샷 캡처 — git(opt-out 아님)이면 프라이빗
     ref(refs/plan-gate/<id>), 아니면 cp 디렉토리 백엔드 (clean/dirty 무관)
  2. 호출마다 edit_count / unique_files 누적
  3. created 상태에서 임계값 직전(soft hint): advisory 환기
  4. created 상태에서 임계값 도달:
       - todo.md 해시 캡처 (TOCTOU 기준점)
       - exit 2 + stderr 풍부한 한국어 안내 (차단)
       - 첫 차단이면 plan-gate 소개도 함께 표시 (dismissable)
       (체크포인트는 1번에서 이미 캡처 — 트리거 시점 별도 stash/tag 없음)
  5. approved 상태에서 같은 파일 thrash(수렴 없는 반복 편집) 도달 시 차단
     (post-approval 볼륨 scope-creep 차단(v1)은 제거됨 — edit_count_post_approval 은
      이제 verifier_remind 트리거로만 쓰인다)
  6. verified+❌ 미해결 상태에서 새 Edit 시도하면 D1 lock으로 차단

환기 메시지는 list 에 누적했다가 통과 분기에서 한 번에 JSON 출력한다.
차단 분기에선 advisory 무시 (차단 우선, 환기는 다음 사이클로).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_gate_lib as lib  # noqa: E402

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _print_stderr(msg: str) -> None:
    sys.stderr.write(msg + "\n")


def _emit_deny(reason: str) -> None:
    """PreToolUse deny: 이 도구 호출만 거부하고 사유를 Claude 에 전달 (layer-1)."""
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def _emit_advisories(items: list[str]) -> None:
    """누적된 환기 메시지를 hookSpecificOutput.additionalContext JSON 한 번으로 출력.

    ⚠️ permissionDecision 은 싣지 않는다 — "allow" 를 함께 실으면 환기가 붙은
    Edit/Write 가 사용자 권한 프롬프트를 우회해 자동 허용된다(환기는 정보 주입일 뿐
    권한 판단이 아니다). additionalContext 는 단독 사용이 공식 스펙상 유효하다.
    """
    if not items:
        return
    combined = "\n\n".join(items)
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": combined,
        }
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def _layer1_denied(root, gate, state, target, advisories: list[str]) -> bool:
    """layer-1 스코프 판정. enforce 거부면 deny emit 후 True(호출자 return 0).

    shadow 위반이면 advisory 누적 후 False. off/허용/매니페스트 없음 → False.
    스코프 밖 Edit/Write 를 편집 전에 거부한다 — Bash 우회는 layer-2 가 사후 스윕.
    enforce 는 이 도구 호출만 deny(Claude 는 다른 in-scope 파일로 진행 가능).
    """
    if not target:
        return False
    mode = lib.scope_mode(root)
    if mode == "off" or not lib.has_manifest(gate) or lib.scope_allows(target, gate, root):
        return False
    rel = lib._rel_to_root(root, target) or target
    if mode == "enforce":
        lib.log_audit(root, "scope_deny_enforced", gate_id=gate["id"], file=rel)
        lib.save_state(root, state)  # 편집 미발생 — 카운터 미증가
        _emit_deny(lib.format_scope_deny(rel, gate))
        return True
    lib.log_audit(root, "scope_deny_shadow", gate_id=gate["id"], file=rel)
    advisories.append(lib.format_scope_shadow(rel, "Edit"))
    return False


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    advisories: list[str] = []  # 환기 메시지 누적, 통과 분기에서 한 번에 emit

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {}) or {}

    if tool_name not in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return 0

    root = lib.find_project_root(data.get("cwd") or None)
    if root is None or not lib.is_plan_gate_enabled(root):
        return 0  # plan-gate 비활성화

    state = lib.load_state(root)
    gate = lib.current_gate(state)

    # ── created 게이트 자동 롤오버 (green Bash 수렴 경계) ─────────────────
    # 작은 미승인 편집은 approve·done 둘 다 불요. 직전 편집 이후 통과한 Bash 가
    # 작업을 사실상 마감했다고 보고, 다음 편집 때 게이트를 조용히 닫고(체크포인트
    # 정리) 새 게이트를 연다 — 사람이 /done 을 칠 필요가 없다(idle 경계 롤오버는
    # detect_task_boundary 가 담당). approved/verified 는 명시 /done 대상이라 제외.
    if gate and gate["state"] == "created" and lib.converged_since_last_edit(gate):
        lib.do_gate_done(root, state, gate)
        gate = None  # 아래 first-edit 블록이 새 게이트를 연다

    # ── 첫 Edit 직전 체크포인트 스냅샷 (D7) ──────────────────────────────
    if gate is None or gate["state"] in ("done", "rolled_back"):
        gate = lib.make_gate()
        # git repo + opt-out 안 함이면 프라이빗 ref 스냅샷, 아니면 cp 디렉토리 백엔드.
        # clean/dirty 무관하게 캡처(C1 의 dirty-skip 한계 제거).
        if not lib.prefers_no_git(root):
            commit = lib.create_snapshot(root, gate)
            if commit:
                gate["checkpoint_commit"] = commit
        lib.set_current_gate(state, gate)

        # ── 계획 감지 → 명시 승인 유도 (D8: 자동승인 제거, 260618 F-003) ──
        # 일반 원칙: 통제 체크포인트('approved')는 기계가 만들 수 있는 산출물
        # (tasks/todo.md 존재·품질)이 아니라 *사람의 명시 행동*(/approve-plan,
        # 또는 신뢰 가능한 Plan Mode Accept 신호)으로만 충족된다. 과거 D8 은
        # "품질 좋은 todo.md 존재" 를 곧 "사용자 승인" 으로 간주해, 사용자가 한 번도
        # 본 적 없는 계획이 무인(無人)으로 approved 되던 우회 경로였다.
        # → 여기서는 gate 를 created 로 유지하고, 품질 triage 후 /approve-plan 을
        #   강하게 유도(advisory)만 한다. 스코프 계약 적재는 명시 승인 경로
        #   (cmd_approve → apply_manifest)가 담당한다.
        # 가드: 직전 사이클 done 시 기록한 archived_todo_sha 와 동일하면
        #       새 계획이 아닌 잔존 파일 — 안내만(안티 패턴 C 방지)
        todo_path = root / "tasks" / "todo.md"
        try:
            todo_text = todo_path.read_text(encoding="utf-8", errors="ignore") if todo_path.exists() else ""
            if todo_text.strip():
                # 파일을 한 번만 읽어 sha 계산 (TOCTOU 방지).
                # /approve-plan 의 해시 검증이 사용자가 본 스냅샷과 대조하도록 캡처.
                current_sha = hashlib.sha256(todo_text.encode()).hexdigest()
                current_mtime = str(todo_path.stat().st_mtime)
                gate["todo_md_sha256"] = current_sha
                gate["todo_md_mtime"] = current_mtime
                prev_sha = lib.last_archived_todo_sha(state)
                if prev_sha and current_sha == prev_sha:
                    advisories.append(
                        "[plan-gate] ℹ️  tasks/todo.md가 이전 사이클과 동일합니다.\n"
                        "  새 계획을 작성하거나, 이 계획으로 진행하려면 /approve-plan 으로 명시 승인하세요."
                    )
                else:
                    ok, issues = lib.validate_todo_quality(root)
                    manifest = lib.parse_manifest(todo_text)
                    if not ok:
                        advisories.append(lib.format_todo_quality_hint(issues))
                    elif lib.manifest_has_broad_glob(manifest):
                        advisories.append(lib.format_broad_glob_hint(manifest))
                    else:
                        scope_note = (
                            f"\n  스코프 계약: {len(manifest['scope'])}개 패턴 선언됨"
                            f" (승인 시 강제={lib.scope_mode(root)})"
                            if manifest
                            else ""
                        )
                        advisories.append(
                            "[plan-gate] 📋 tasks/todo.md 계획 감지 (gate: created).\n"
                            "  계획을 검토하고, 진행하려면 /approve-plan 을 입력하세요 "
                            "— 승인 전까지 구현 게이트는 열리지 않습니다." + scope_note
                        )
        except Exception:
            pass

    # 미승인 created 게이트는 /done 을 강요하지 않는다 — 작은 편집은 approve·done
    # 둘 다 불요. 닫기는 자동 롤오버(green Bash 수렴=위 블록 / idle=detect_task_boundary)가
    # 담당하고, 수렴 없는 반복만 5회 트리거(아래)가 차단한다.

    # ── 세션 재진입 경고: 24시간 이상 된 approved gate 잔류 ──────────────
    if gate and gate["state"] == "approved":
        try:
            created_str = gate.get("created_at")
            if created_str:
                created = datetime.fromisoformat(created_str)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - created).total_seconds() > 86400:
                    advisories.append(
                        f"[plan-gate] ⚠️  24시간 이상 된 approved gate 잔류: {gate['id']}\n"
                        "  이전 세션에서 완료되지 않은 작업입니다. /done 또는 /rollback 으로 정리하세요."
                    )
        except Exception:
            pass

    # ── D1 lock: verifier ❌ 미해결 상태 ─────────────────────────────────
    if gate["state"] == "verified" and gate.get("verifier_status") == "❌":
        _print_stderr(lib.format_d1_lock_message(gate))
        lib.save_state(root, state)
        return 2

    # ── 카운터 누적 ──────────────────────────────────────────────────────
    target = lib.extract_target_file(tool_name, tool_input, project_root=root)

    # ── .plan-gateignore 무시 목록 확인 (자동 추가 포함) ────────────────
    if target:
        ignore_patterns = lib.load_gate_ignore(root)
        added = lib.auto_add_gate_ignore(target, root, ignore_patterns)
        if added:
            pattern, reason = added
            _print_stderr(f"\n[plan-gate] .plan-gateignore에 '{pattern}' 자동 추가 ({reason})\n")
            ignore_patterns = lib.load_gate_ignore(root)
        if lib.is_gate_ignored(target, root, ignore_patterns):
            lib.save_state(root, state)
            return 0

    # ── layer-1 스코프 강제 (R3/R4 — enforce=deny / shadow=환기) ──────────
    if _layer1_denied(root, gate, state, target, advisories):
        return 0  # enforce: 이 편집만 거부(deny JSON), 카운터 미증가

    # ── 편집 직전 touched 기록 (롤백 매니페스트) ─────────────────────────
    # git: 스냅샷 커밋이 내용 출처라 존재 비트만 / 비-git: cp 디렉토리에 복사.
    if target:
        lib.record_touched(root, gate, target)

    # ── hot-file 경고 (세션 간 패치 누적 감지) ───────────────────────────
    hot_level, hot_count = lib.hot_file_check(root, target)
    if hot_level:
        advisories.append(lib.format_hot_file_warn(target, hot_level, hot_count))

    # ── 동일 파일 재편집 힌트: Edit → MultiEdit 유도 ─────────────────────
    # 같은 gate 내에서 이미 수정한 파일을 Edit으로 다시 호출하면
    # 비차단 힌트만 누적하고 흐름은 계속 탄다 (카운터·트리거 정상 동작).
    # 주의: 여기서 early-return 하면 file_edit_counts가 1에 고정되어
    #       반복편집 트리거가 Edit 툴에서 영구히 죽는다 (v1.28.0 회귀 수정).
    if tool_name == "Edit" and target and target in gate["unique_files"]:
        advisories.append(lib.format_multi_edit_hint(target))

    gate["edit_count"] += 1
    gate["last_edit_ts"] = lib.now_iso()
    if gate["state"] == "approved":
        gate["edit_count_post_approval"] += 1  # verifier_remind 트리거용
    if target:
        if target not in gate["unique_files"]:
            gate["unique_files"].append(target)
        counts = gate.setdefault("file_edit_counts", {})
        counts[target] = counts.get(target, 0) + 1

    # ── 큰 미승인 변경 → /approve-plan 권장 (비차단 환기, 게이트당 1회) ────
    # 규모는 "계획 필요"의 강제 기준이 못 되므로(과거 볼륨 차단 v1 오탐) 차단하지
    # 않고 환기만 한다. created + 비자동승인에서, 단일 편집 코드량 또는 코드파일
    # fan-out 이 임계 이상이면 1회 dedup 으로 advisory 누적.
    if (
        gate["state"] == "created"
        and not gate.get("approved_auto")
        and not gate.get("large_advisory_seen")
    ):
        added = lib.edit_added_code_lines(tool_name, tool_input, root)
        fan = lib._unique_code_files(gate)
        if added >= lib.LARGE_OP_LINES or fan >= lib.LARGE_FAN_FILES:
            gate["large_advisory_seen"] = True
            lib.log_audit(root, "large_edit_advisory", gate_id=gate["id"], added=added, files=fan)
            advisories.append(lib.format_large_edit_advisory(added, fan))

    # ── soft hint (thrash 트리거 직전) ──────────────────────────────────
    # created/approved 모두 같은 파일 반복(thrash) 임박 시 부드러운 경고 (차단 X).
    # 판정은 target 파일 자체의 반복 횟수 — 게이트 전역 max 는 연좌 차단을 부른다.
    _repeat = lib._code_repeat_for(gate, target)
    if (
        gate["state"] in ("created", "approved")
        and not lib.trigger_threshold_exceeded(gate, target)
        and _repeat == lib.TRIGGER_REPEAT_RATIO - 1
    ):
        advisories.append(lib.format_soft_hint(gate))
        _emit_advisories(advisories)
        lib.save_state(root, state)
        return 0

    # ── 트리거 도달 → 차단 ──────────────────────────────────────────────
    if gate["state"] == "created" and lib.trigger_threshold_exceeded(gate, target):
        gate["initial_edit_count"] = gate["edit_count"]
        gate["initial_unique_files"] = len(gate["unique_files"])

        lib.log_audit(root, "trigger", gate_id=gate["id"],
                      edit_count=gate["edit_count"],
                      unique_files=len(gate["unique_files"]))

        # 체크포인트는 게이트 열 때 create_snapshot 으로 이미 캡처됨(C1/C2 해소).
        # 트리거 시점에 별도 stash/tag 생성하지 않는다.

        sha, mtime = lib.hash_todo_md(root)
        gate["todo_md_sha256"] = sha
        gate["todo_md_mtime"] = mtime

        lib.set_current_gate(state, gate)
        lib.save_state(root, state)

        # 메시지 빌더 호출 (UX 풍부화 + git diff 주입)
        show_intro = not lib.intro_seen(root)
        diff_summary = lib.git_diff_summary(root, max_diff_lines=80)
        _print_stderr(lib.format_trigger_message(gate, show_intro, diff_summary))
        if show_intro:
            lib.mark_intro_seen(root)
        return 2

    # ── 승인 후 thrash 차단 (flailing) ───────────────────────────────────
    # scope-creep 볼륨 차단(v1)은 제거됨 — 스코프 강제는 매니페스트(step3+)가 대체.
    # 단 '같은 파일을 수렴 없이 반복'은 승인 후에도 thrash 로 잡는다(사용자 가치 보존).
    if gate["state"] == "approved" and lib.trigger_threshold_exceeded(gate, target):
        lib.log_audit(root, "thrash_approved", gate_id=gate["id"],
                      max_repeat=lib._code_repeat_for(gate, target))
        lib.save_state(root, state)
        _print_stderr(lib.format_thrash_message(gate))
        return 2

    # ── 통과 ────────────────────────────────────────────────────────────
    _emit_advisories(advisories)
    lib.save_state(root, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
