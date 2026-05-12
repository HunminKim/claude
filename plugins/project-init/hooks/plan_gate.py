#!/usr/bin/env python3
"""PreToolUse hook (matcher: Edit|Write|MultiEdit) — plan-gate 강제.

동작 (D1/D2/D5/D7 + UX 풍부화):
  1. 첫 Edit 직전: working tree clean이면 lightweight tag 생성 (롤백 지점)
  2. 호출마다 edit_count / unique_files / multi_edit_max 누적
  3. created 상태에서 임계값 직전(soft hint): exit 0 + stderr 부드러운 경고
  4. created 상태에서 임계값 도달:
       - 현재 dirty 변경을 stash (gate_id 마커)
       - clean tag 확보, todo.md 해시 캡처
       - exit 2 + stderr 풍부한 한국어 안내 (사용자+Claude 동시 노출)
       - 첫 차단이면 plan-gate 소개도 함께 표시 (dismissable)
  5. approved 상태에서 scope creep(post_approval limit) 도달 시 차단
  6. verified+❌ 미해결 상태에서 새 Edit 시도하면 D1 lock으로 차단
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
    if root is None or not lib.is_plan_gate_enabled(root):
        return 0  # plan-gate 비활성화

    state = lib.load_state(root)
    gate = lib.current_gate(state)

    # ── 첫 Edit 직전 clean tag (D7) ──────────────────────────────────────
    if gate is None or gate["state"] in ("done", "rolled_back"):
        gate = lib.make_gate()
        existing = lib.existing_clean_tag_for_head(root)
        if existing:
            gate["checkpoint_clean_tag"] = existing
        elif lib.has_git(root) and lib.working_tree_clean(root):
            tag = lib.create_clean_tag(root, gate["id"])
            if tag:
                gate["checkpoint_clean_tag"] = tag
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
                prev_sha = lib.last_archived_todo_sha(root)
                if prev_sha and current_sha == prev_sha:
                    _print_stderr(
                        "\n[plan-gate] ℹ️  tasks/todo.md가 이전 사이클과 동일 → 자동 승인 스킵.\n"
                        "  새 계획을 작성하거나 /approve-plan 으로 명시 승인하세요.\n"
                    )
                else:
                    ok, issues = lib.validate_todo_quality(root)
                    if not ok:
                        _print_stderr(lib.format_todo_quality_hint(issues))
                    else:
                        gate["state"] = "approved"
                        gate["approved_at"] = lib.now_iso()
                        gate["approved_auto"] = True
                        gate["edit_count_post_approval"] = 0
                        gate["file_edit_counts_post_approval"] = {}
                        gate["unique_files_post_approval"] = []
                        gate["initial_edit_count"] = 0
                        gate["initial_unique_files"] = 0
                        gate["todo_md_sha256"] = current_sha
                        gate["todo_md_mtime"] = current_mtime
                        _print_stderr(
                            f"\n[plan-gate] ✅ tasks/todo.md 감지 → 자동 승인: {gate['id']}\n"
                            f"  임계값: 단일 파일 {lib.TRIGGER_REPEAT_RATIO}회 반복 or"
                            f" 파일 {lib.TRIGGER_UNIQUE_FILES}개\n"
                        )
        except Exception:
            pass

    # ── stale created gate 경고: 편집이 쌓인 채 방치된 gate ────────────────
    # approved 이전 "created" 상태에서도 편집이 많이 쌓이면 /done 을 강하게 유도.
    if gate and gate["state"] == "created" and gate.get("edit_count", 0) >= 3:
        _print_stderr(
            f"\n[plan-gate] ⚠️  이전 작업 gate가 닫히지 않았습니다 (편집 {gate['edit_count']}회 누적).\n"
            "  이전 작업이 끝났다면 지금 /done 을 입력하세요.\n"
            "  /done 없이 계속하면 카운트가 누적되어 현재 작업이 차단됩니다.\n"
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
                    _print_stderr(
                        f"\n[plan-gate] ⚠️  24시간 이상 된 approved gate 잔류: {gate['id']}\n"
                        "  이전 세션에서 완료되지 않은 작업입니다. /done 또는 /rollback 으로 정리하세요.\n"
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
    multi_items = lib.count_multi_edit_items(tool_name, tool_input)

    # ── hot-file 경고 (세션 간 패치 누적 감지) ───────────────────────────
    hot_level, hot_count = lib.hot_file_check(root, target)
    if hot_level:
        _print_stderr(lib.format_hot_file_warn(target, hot_level, hot_count))

    gate["edit_count"] += 1
    gate["last_edit_ts"] = lib.now_iso()
    if gate["state"] == "approved":
        gate["edit_count_post_approval"] += 1
        if target:
            post_counts = gate.setdefault("file_edit_counts_post_approval", {})
            post_counts[target] = post_counts.get(target, 0) + 1
            post_unique = gate.setdefault("unique_files_post_approval", [])
            if target not in post_unique:
                post_unique.append(target)
    if target:
        if target not in gate["unique_files"]:
            gate["unique_files"].append(target)
        counts = gate.setdefault("file_edit_counts", {})
        counts[target] = counts.get(target, 0) + 1
    if multi_items > gate["multi_edit_max"]:
        gate["multi_edit_max"] = multi_items

    # ── soft hint (트리거 직전) ─────────────────────────────────────────
    # created 상태에서 트리거 임박 시 부드러운 경고만 출력 (차단 X).
    _max_repeat = lib._max_code_repeat(gate)
    if (
        gate["state"] == "created"
        and not lib.trigger_threshold_exceeded(gate)
        and (
            _max_repeat == lib.TRIGGER_REPEAT_RATIO - 1
            or lib._unique_code_files(gate) == lib.TRIGGER_UNIQUE_FILES - 1
        )
    ):
        _print_stderr(lib.format_soft_hint(gate))
        lib.save_state(root, state)
        return 0

    # ── 트리거 도달 → 차단 ──────────────────────────────────────────────
    if gate["state"] == "created" and lib.trigger_threshold_exceeded(gate):
        gate["initial_edit_count"] = gate["edit_count"]
        gate["initial_unique_files"] = len(gate["unique_files"])

        lib.log_audit(root, "trigger", gate_id=gate["id"],
                      edit_count=gate["edit_count"],
                      unique_files=len(gate["unique_files"]))

        # dirty 보존: stash (D4)
        if lib.has_git(root) and not lib.working_tree_clean(root):
            ref = lib.stash_dirty(root, gate["id"])
            if ref:
                gate["checkpoint_dirty_stash_ref"] = ref

        # stash 후 working tree clean이면 HEAD에 tag (없을 때만)
        if not gate.get("checkpoint_clean_tag") and lib.has_git(root):
            tag = lib.create_clean_tag(root, gate["id"])
            if tag:
                gate["checkpoint_clean_tag"] = tag

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

    # ── 승인 후 scope creep 차단 (D2) ────────────────────────────────────
    if gate["state"] == "approved" and lib.post_approval_limit_exceeded(gate):
        _print_stderr(lib.format_scope_creep_message(gate))
        lib.save_state(root, state)
        return 2

    # ── 통과 ────────────────────────────────────────────────────────────
    lib.save_state(root, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
