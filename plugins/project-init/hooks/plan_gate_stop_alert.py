#!/usr/bin/env python3
"""Stop hook — Claude 응답 종료 직전 plan-gate 상태 리마인더.

approved 게이트가 활성 상태이고 응답 중에 편집이 발생했을 때
(last_edit_ts가 응답 시작 이후로 갱신된 경우) 현재 한도 소모 현황을
사용자에게 보여준다. 편집이 없었던 응답(조회·대화)에는 출력하지 않아
노이즈를 최소화한다.

exit 2 로 응답을 차단하지 않는다 (정보 제공 전용).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_gate_lib as lib  # noqa: E402

# 이번 응답 내에서 편집이 있었다고 간주할 최대 경과 시간
_RECENT_EDIT_WINDOW_SECONDS = 300  # 5분 이내 편집 = 이번 응답의 작업


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

    # 이번 응답 중 편집이 있었는지 확인 (last_edit_ts 기준)
    last_edit_str = gate.get("last_edit_ts")
    if not last_edit_str:
        return 0

    try:
        last_edit = datetime.fromisoformat(last_edit_str)
        if last_edit.tzinfo is None:
            last_edit = last_edit.replace(tzinfo=timezone.utc)
        elapsed = datetime.now(timezone.utc) - last_edit
        if elapsed > timedelta(seconds=_RECENT_EDIT_WINDOW_SECONDS):
            return 0  # 최근 편집 없음 — 이번 응답에서 코드 수정 없었음
    except Exception:
        return 0

    limit = lib.post_approval_limit(gate)
    post = gate.get("edit_count_post_approval", 0)
    remaining = limit - post
    auto_label = "자동" if gate.get("approved_auto") else "명시"

    if remaining <= 0:
        # 이미 차단 직전 or 초과 — scope creep 메시지가 이미 나왔을 것
        return 0

    if remaining == 1:
        sys.stderr.write(
            f"\n[plan-gate] ⚠️  approved({auto_label}) {post}/{limit}"
            f" — 다음 편집 시 차단됩니다. 작업 완료면 /done\n\n"
        )
    else:
        sys.stderr.write(
            f"\n[plan-gate] approved({auto_label}) {post}/{limit}"
            f" — 잔여 {remaining}회. 새 작업이면 /done\n\n"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
