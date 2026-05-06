#!/usr/bin/env python3
"""SessionStart hook — 세션 재개 시 plan-gate 상태 능동 보고.

새 세션(시작·재개·compact 후) 진입 시 활성 approved gate가 있으면
additionalContext 채널로 현황을 즉시 주입한다. Claude가 메시지를
받기 전에 컨텍스트가 로드되므로 첫 응답부터 올바른 상태를 반영한다.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_gate_lib as lib  # noqa: E402


def main() -> int:
    try:
        json.load(sys.stdin)
    except Exception:
        return 0

    root = lib.find_project_root()
    if root is None or not lib.is_plan_gate_enabled(root):
        return 0

    state = lib.load_state(root)
    gate = lib.current_gate(state)

    if gate is None:
        return 0

    g_state = gate["state"]
    if g_state not in ("created", "approved", "verified"):
        return 0

    limit = lib.post_approval_limit(gate)
    post = gate.get("edit_count_post_approval", 0)
    auto_label = "자동" if gate.get("approved_auto") else "명시"
    approved_at = gate.get("approved_at") or "-"
    clean_tag = gate.get("checkpoint_clean_tag") or "(없음)"

    # 경과 시간 계산
    ts_str = gate.get("last_edit_ts") or gate.get("approved_at") or gate.get("created_at")
    elapsed_str = "-"
    if ts_str:
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            elapsed = datetime.now(timezone.utc) - ts
            h = int(elapsed.total_seconds() // 3600)
            m = int((elapsed.total_seconds() % 3600) // 60)
            elapsed_str = f"{h}시간 {m}분 전"
        except Exception:
            pass

    lines = [
        "",
        "─" * 60,
        "📋 [plan-gate] 세션 재개 — 진행 중인 게이트 있음",
        "─" * 60,
        f"  id      : {gate['id']}",
        f"  state   : {g_state}",
        f"  edits   : {gate['edit_count']}회 (승인 후 {post}/{limit})",
        f"  승인    : {auto_label} ({approved_at})",
        f"  마지막  : {elapsed_str}",
        f"  tag     : {clean_tag}",
    ]

    if g_state == "verified" and gate.get("verifier_status") == "❌":
        lines += [
            "",
            "⚠️  verifier 검증 실패 상태입니다.",
            "  /retry 또는 /rollback 을 입력해 해결하세요.",
        ]
    elif g_state == "approved":
        remaining = limit - post
        if remaining <= 1:
            lines += [
                "",
                f"⚠️  편집 한도 임박 ({post}/{limit}) — 다음 편집 시 scope creep 차단.",
                "  작업을 마치려면 /done, 계획 조정은 /replan.",
            ]
        else:
            lines += [
                "",
                f"  편집 잔여 : {remaining}회",
                "  새 작업이면 /done 후 시작하세요.",
            ]
    elif g_state == "created":
        lines += [
            "",
            "  아직 계획 미승인 상태입니다. tasks/todo.md 작성 후 /approve-plan.",
        ]

    lines += ["─" * 60, ""]

    result = {"additionalContext": "\n".join(lines)}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
