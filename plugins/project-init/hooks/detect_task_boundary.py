#!/usr/bin/env python3
"""UserPromptSubmit hook — 작업 경계 자동 감지.

Layer 1 — 시간 기반 자동 done (신뢰도 높음):
  approved 게이트의 last_edit_ts로부터 BOUNDARY_TIMEOUT_MINUTES 이상 경과하면
  자동으로 done 처리한다. (점심·자리 비움 등 긴 공백 후 새 작업 복귀 대응)

Layer 2 — 활성 세션 현황 안내 (차단 없음):
  타임아웃 미달이지만 편집이 2회 이상 누적된 경우,
  새 작업이면 /done 을 입력하라는 안내만 출력한다.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_gate_lib as lib  # noqa: E402

# Layer 2 안내는 마지막 편집으로부터 이 시간 이상 경과한 경우에만 출력 (노이즈 억제)
_NUDGE_MIN_GAP_MINUTES = 5


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

    if gate is None or gate["state"] != "approved":
        return 0

    # last_edit_ts 없으면 approved_at 또는 created_at 으로 대체
    ts_str = gate.get("last_edit_ts") or gate.get("approved_at") or gate.get("created_at")
    if ts_str is None:
        return 0

    try:
        last_ts = datetime.fromisoformat(ts_str)
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
        elapsed = datetime.now(timezone.utc) - last_ts
    except Exception:
        return 0

    timeout = timedelta(minutes=lib.BOUNDARY_TIMEOUT_MINUTES)

    # ── Layer 1: 타임아웃 초과 → 자동 done ──────────────────────────────
    if elapsed >= timeout:
        elapsed_h = elapsed.total_seconds() / 3600
        edit_count = gate.get("edit_count", 0)
        gate_id_short = gate["id"][:28] + "…"
        lib.do_gate_done(root, state, gate)
        sys.stderr.write(
            f"\n[task-boundary] 게이트 자동 종료\n"
            f"  마지막 편집으로부터 {elapsed_h:.1f}시간 경과"
            f" (임계 {lib.BOUNDARY_TIMEOUT_MINUTES}분)\n"
            f"  {gate_id_short} — 편집 {edit_count}회, 체크포인트 정리됨\n"
            f"  새 작업은 새 게이트에서 시작됩니다.\n\n"
        )
        return 0

    # ── Layer 2: 활성 세션 중 현황 안내 ─────────────────────────────────
    edit_count = gate.get("edit_count", 0)
    nudge_gap = timedelta(minutes=_NUDGE_MIN_GAP_MINUTES)
    if edit_count >= 2 and elapsed >= nudge_gap:
        limit = lib.post_approval_limit(gate)
        post = gate.get("edit_count_post_approval", 0)
        sys.stderr.write(
            f"\n[task-boundary] 게이트 활성 중 —"
            f" 편집 {edit_count}회 / 승인 후 {post}/{limit}\n"
            f"  이전 작업을 마쳤다면 /done 으로 게이트를 닫으세요.\n\n"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
