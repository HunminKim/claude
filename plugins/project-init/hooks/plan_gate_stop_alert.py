#!/usr/bin/env python3
"""Stop hook — Claude 응답 종료 직전 plan-gate 상태 리마인더.

approved 게이트가 활성 상태이고 응답 중에 편집이 발생했을 때
(last_edit_ts가 응답 시작 이후로 갱신된 경우) 현재 한도 소모 현황을
사용자에게 보여준다. 편집이 없었던 응답(조회·대화)에는 출력하지 않아
노이즈를 최소화한다.

한도의 70% 이상 소진 시 /compact 권고 + compact 후 이어받기 프롬프트를 함께 출력한다.

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

    # verifier 미호출 경고 — 편집이 있는데 verifier 를 한 번도 안 불렀으면 리마인드
    # auto-approved 게이트(project-init 등 스캐폴딩)는 verifier 대상 아님
    if (
        gate.get("edit_count", 0) > 0
        and gate.get("verifier_status") is None
        and not gate.get("approved_auto")
    ):
        sys.stderr.write(
            "\n[plan-gate] ⚠️  @verifier 미호출\n"
            "  편집이 있었지만 검증이 없습니다. /done 전 @verifier 호출 필수.\n"
            "  건너뛰려면 /skip-verify 를 명시적으로 입력.\n\n"
        )

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

    max_repeat, post_unique = lib.post_approval_stats(gate)
    auto_label = "자동" if gate.get("approved_auto") else "명시"

    if lib.post_approval_limit_exceeded(gate):
        # 이미 차단 — scope creep 메시지가 이미 나왔을 것
        return 0

    near_limit = max_repeat >= lib.TRIGGER_REPEAT_RATIO - 1
    if near_limit:
        sys.stderr.write(
            f"\n[plan-gate] ⚠️  approved({auto_label})"
            f" 파일최대 {max_repeat}/{lib.TRIGGER_REPEAT_RATIO}"
            f" — 다음 편집 시 차단됩니다. 작업 완료면 /done\n\n"
        )
    else:
        sys.stderr.write(
            f"\n[plan-gate] approved({auto_label})"
            f" 파일최대 {max_repeat}/{lib.TRIGGER_REPEAT_RATIO}"
            f" — 새 작업이면 /done\n\n"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
