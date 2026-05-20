#!/usr/bin/env python3
"""UserPromptSubmit hook — 작업 경계 자동 감지.

Layer 1 — 시간 기반 자동 done (신뢰도 높음):
  approved/created 게이트의 last_edit_ts로부터 BOUNDARY_TIMEOUT_MINUTES 이상 경과하면
  자동으로 done 처리한다. (점심·자리 비움 등 긴 공백 후 새 작업 복귀 대응)

Layer 2 — 새 요청 진입 시 양자택일 안내 (차단 없음):
  approved 게이트에 편집이 1회 이상 누적된 상태에서 새 프롬프트가 들어오면
  Claude에게 사용자에게 물어보도록 강제한다: /replan(계획 추가) vs 완료 우선(/done 후 시작).
  /로 시작하는 커맨드 입력은 사용자 의도가 명확하므로 스킵한다.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_gate_lib as lib  # noqa: E402


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    root = lib.find_project_root()
    if root is None or not lib.is_plan_gate_enabled(root):
        return 0

    state = lib.load_state(root)
    gate = lib.current_gate(state)

    if gate is None or gate["state"] not in ("approved", "created"):
        return 0

    edit_count = gate.get("edit_count", 0)
    if edit_count == 0:
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

    # ── Layer 2: 새 요청 진입 시 양자택일 안내 ────────────────────────────
    # 편집 1회 이상 + 1분 이상 경과 시 매 프롬프트마다 출력.
    if elapsed < timedelta(minutes=1):
        return 0

    # 커맨드 입력(/done, /replan 등)이면 사용자 의도 명확 — 스킵
    prompt_text = payload.get("message") or payload.get("prompt") or ""
    if prompt_text.strip().startswith("/"):
        return 0

    if gate["state"] == "approved":
        max_repeat, post_unique = lib.post_approval_stats(gate)
        auto_label = "자동" if gate.get("approved_auto") else "명시"
        if lib.post_approval_limit_exceeded(gate):
            return 0
        near_limit = max_repeat >= lib.TRIGGER_REPEAT_RATIO - 1
        near_msg = (
            f"\n  ⚠️  scope 임박 — 파일최대 {max_repeat}/{lib.TRIGGER_REPEAT_RATIO} (다음 편집 시 차단)"
            if near_limit
            else ""
        )
        sys.stderr.write(
            f"[plan-gate] 진행 중인 작업 있음 (approved/{auto_label}, 편집 {edit_count}회){near_msg}\n"
            f"  구현 시작 전 사용자에게 반드시 물어보세요:\n\n"
            f'  "새 요청이 들어왔습니다. 어떻게 진행할까요?\n'
            f"   1) /replan  — 새 요청을 현재 계획에 추가하고 바로 진행\n"
            f'   2) 완료 우선 — 지금 작업 끝낸 뒤 /done, 그다음 새 요청 시작"\n\n'
            f"  답변 전 코드 작성·파일 수정을 시작하지 마세요.\n\n"
        )
    else:
        # state == "created": 트리거 전이지만 편집이 쌓인 상태
        sys.stderr.write(
            f"[gate] created — 편집 {edit_count}회 누적 중\n"
            f"  ★ 이전 작업이 완료됐으면 반드시 /done 을 입력하세요. 입력하지 않으면\n"
            f"    카운트가 계속 누적되어 새 작업이 차단될 수 있습니다.\n\n"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
