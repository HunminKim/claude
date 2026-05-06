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
        todo_path = root / "tasks" / "todo.md"
        try:
            if (
                todo_path.exists()
                and todo_path.read_text(encoding="utf-8", errors="ignore").strip()
            ):
                ok, issues = lib.validate_todo_quality(root)
                if not ok:
                    _print_stderr(lib.format_todo_quality_hint(issues))
                else:
                    sha, mtime = lib.hash_todo_md(root)
                    gate["state"] = "approved"
                    gate["approved_at"] = lib.now_iso()
                    gate["approved_auto"] = True
                    gate["edit_count_post_approval"] = 0
                    gate["initial_edit_count"] = 0
                    gate["initial_unique_files"] = 0
                    gate["todo_md_sha256"] = sha
                    gate["todo_md_mtime"] = mtime
                    _print_stderr(
                        f"\n[plan-gate] ✅ tasks/todo.md 감지 → 자동 승인: {gate['id']}\n"
                        f"  limit={lib.post_approval_limit(gate)} edits"
                        f" (자동 승인 — 보수적 임계값)\n"
                    )
        except Exception:
            pass

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
    if target and target not in gate["unique_files"]:
        gate["unique_files"].append(target)
    if multi_items > gate["multi_edit_max"]:
        gate["multi_edit_max"] = multi_items

    # ── soft hint (트리거 직전) ─────────────────────────────────────────
    # created 상태에서 임계값 바로 아래(2회 또는 2파일)면 부드러운 경고만 출력.
    # 차단 X. 사용자가 큰 작업이라면 미리 todo.md 작성하도록 유도.
    if (
        gate["state"] == "created"
        and not lib.trigger_threshold_exceeded(gate)
        and (
            gate["edit_count"] == lib.TRIGGER_EDIT_COUNT - 1
            or len(gate["unique_files"]) == lib.TRIGGER_UNIQUE_FILES - 1
        )
    ):
        _print_stderr(lib.format_soft_hint(gate))
        lib.save_state(root, state)
        return 0

    # ── 트리거 도달 → 차단 ──────────────────────────────────────────────
    if gate["state"] == "created" and lib.trigger_threshold_exceeded(gate):
        gate["initial_edit_count"] = gate["edit_count"]
        gate["initial_unique_files"] = len(gate["unique_files"])

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
