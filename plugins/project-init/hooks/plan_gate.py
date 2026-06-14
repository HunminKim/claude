#!/usr/bin/env python3
"""PreToolUse hook (matcher: Edit|Write|MultiEdit) — plan-gate 강제.

출력 채널:
- 차단 (D1 lock / 트리거 / scope creep): exit 2 + stderr — Claude blocking error 주입
- 환기 (자동 승인 / stale gate / 24h 잔류 / hot-file / soft hint / multi-edit hint): exit 0 + stdout hookSpecificOutput.additionalContext JSON — Claude context 주입
- 사용자 터미널 전용 (.plan-gateignore 자동 추가): exit 0 + stderr

동작 (D1/D2/D5/D7 + UX 풍부화):
  1. 첫 Edit 직전: working tree clean이면 lightweight tag 생성 (롤백 지점)
  2. 호출마다 edit_count / unique_files 누적
  3. created 상태에서 임계값 직전(soft hint): advisory 환기
  4. created 상태에서 임계값 도달:
       - 현재 dirty 변경을 stash (gate_id 마커)
       - clean tag 확보, todo.md 해시 캡처
       - exit 2 + stderr 풍부한 한국어 안내 (차단)
       - 첫 차단이면 plan-gate 소개도 함께 표시 (dismissable)
  5. approved 상태에서 scope creep(post_approval limit) 도달 시 차단
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


def _print_stderr(msg: str) -> None:
    sys.stderr.write(msg + "\n")


def _emit_advisories(items: list[str]) -> None:
    """누적된 환기 메시지를 hookSpecificOutput.additionalContext JSON 한 번으로 출력."""
    if not items:
        return
    combined = "\n\n".join(items)
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "additionalContext": combined,
        }
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    advisories: list[str] = []  # 환기 메시지 누적, 통과 분기에서 한 번에 emit

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {}) or {}

    if tool_name not in ("Edit", "Write", "MultiEdit"):
        return 0

    root = lib.find_project_root()
    if root is None or not lib.is_plan_gate_enabled(root):
        return 0  # plan-gate 비활성화

    state = lib.load_state(root)
    gate = lib.current_gate(state)

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

        # ── Plan Mode 자동 승인: tasks/todo.md 존재 + 품질 통과 시 즉시 approved (D8) ──
        # 가드: 직전 사이클 done 시 기록한 archived_todo_sha 와 동일하면
        #       새 계획이 아닌 잔존 파일 — 자동 승인 스킵 (안티 패턴 C 방지)
        todo_path = root / "tasks" / "todo.md"
        try:
            todo_text = todo_path.read_text(encoding="utf-8", errors="ignore") if todo_path.exists() else ""
            if todo_text.strip():
                # 파일을 한 번만 읽어 sha 계산 (TOCTOU 방지)
                current_sha = hashlib.sha256(todo_text.encode()).hexdigest()
                current_mtime = str(todo_path.stat().st_mtime)
                prev_sha = lib.last_archived_todo_sha(state)
                if prev_sha and current_sha == prev_sha:
                    advisories.append(
                        "[plan-gate] ℹ️  tasks/todo.md가 이전 사이클과 동일 → 자동 승인 스킵.\n"
                        "  새 계획을 작성하거나 /approve-plan 으로 명시 승인하세요."
                    )
                else:
                    ok, issues = lib.validate_todo_quality(root)
                    manifest = lib.parse_manifest(todo_text)
                    if not ok:
                        advisories.append(lib.format_todo_quality_hint(issues))
                    elif lib.manifest_has_broad_glob(manifest):
                        # D6: 넓은 글롭은 자동 승인 비활성 → 사람 /approve-plan 강제.
                        # 자동 승인하지 않고 created 상태로 남긴다(차단 아님 — advisory).
                        advisories.append(lib.format_broad_glob_hint(manifest))
                    else:
                        lib.transition(gate, "approve_auto")
                        gate["todo_md_sha256"] = current_sha
                        gate["todo_md_mtime"] = current_mtime
                        # 매니페스트 선언 시 스코프 계약 저장(노출만 — 강제는 step 5)
                        if manifest:
                            gate["scope"] = manifest["scope"]
                            gate["do_not_touch"] = manifest["do_not_touch"]
                            gate["manifest_sha256"] = lib.manifest_sha(todo_text)
                        scope_note = (
                            f"\n  스코프 계약: {len(manifest['scope'])}개 패턴 선언됨"
                            " (현재 노출만 — 강제는 다음 버전)"
                            if manifest
                            else ""
                        )
                        advisories.append(
                            f"[plan-gate] ✅ tasks/todo.md 감지 → 자동 승인: {gate['id']}\n"
                            f"  임계값: 단일 파일 {lib.TRIGGER_REPEAT_RATIO}회 반복" + scope_note
                        )
        except Exception:
            pass

    # ── stale created gate 경고: 편집이 쌓인 채 방치된 gate ────────────────
    # approved 이전 "created" 상태에서도 편집이 많이 쌓이면 /done 을 강하게 유도.
    if gate and gate["state"] == "created" and gate.get("edit_count", 0) >= 3:
        advisories.append(
            f"[plan-gate] ⚠️  이전 작업 gate가 닫히지 않았습니다 (편집 {gate['edit_count']}회 누적).\n"
            "  이전 작업이 끝났다면 지금 /done 을 입력하세요.\n"
            "  /done 없이 계속하면 카운트가 누적되어 현재 작업이 차단됩니다."
        )

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

    # ── soft hint (thrash 트리거 직전) ──────────────────────────────────
    # created/approved 모두 같은 파일 반복(thrash) 임박 시 부드러운 경고 (차단 X).
    _max_repeat = lib._max_code_repeat(gate)
    if (
        gate["state"] in ("created", "approved")
        and not lib.trigger_threshold_exceeded(gate)
        and _max_repeat == lib.TRIGGER_REPEAT_RATIO - 1
    ):
        advisories.append(lib.format_soft_hint(gate))
        _emit_advisories(advisories)
        lib.save_state(root, state)
        return 0

    # ── 트리거 도달 → 차단 ──────────────────────────────────────────────
    if gate["state"] == "created" and lib.trigger_threshold_exceeded(gate):
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
    if gate["state"] == "approved" and lib.trigger_threshold_exceeded(gate):
        lib.log_audit(root, "thrash_approved", gate_id=gate["id"],
                      max_repeat=lib._max_code_repeat(gate))
        lib.save_state(root, state)
        _print_stderr(lib.format_thrash_message(gate))
        return 2

    # ── 통과 ────────────────────────────────────────────────────────────
    _emit_advisories(advisories)
    lib.save_state(root, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
