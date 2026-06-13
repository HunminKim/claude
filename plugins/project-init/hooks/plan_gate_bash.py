#!/usr/bin/env python3
"""PostToolUse hook (Bash) — green-Bash 수렴 신호로 thrash 카운터 리셋.

역할: Bash 가 성공(exit 0)하면 "현재 작업이 수렴 중"이라는 신호로 보고,
현재 게이트의 파일별 반복 편집 카운터(file_edit_counts)를 리셋한다. 이렇게 하면
정상적으로 열심히 반복 편집하는 경우(중간에 테스트가 통과)는 반복 트리거(thrash)에
걸리지 않고, 수렴 없이 같은 파일만 계속 패치하는 flailing(계속 실패)만 트리거에
도달한다 — v1 반복 트리거 오탐의 핵심 원인을 제거하는 가드(설계 D9).

동작 단계:
  1. stdin JSON 에서 tool_response.exit_code 확인 (Bash 외 / 실패 시 no-op)
  2. plan-gate 활성 + 활성 게이트 존재 시에만 동작
  3. green(exit 0) → gate['last_successful_bash_ts'] 기록 + 반복 카운터 리셋

출력 채널: 없음 (상태 부수효과 — 사용자/Claude 메시지 없음, silent exit 0)
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_gate_lib as lib  # noqa: E402


def _active_gate(data: dict[str, Any]):
    """green Bash 처리 대상이면 (root, state, gate), 아니면 None."""
    if data.get("tool_name") != "Bash":
        return None
    if (data.get("tool_response") or {}).get("exit_code", 0) != 0:
        return None  # 실패/비정상 종료는 수렴 신호가 아니다
    root = lib.find_project_root()
    if root is None or not lib.is_plan_gate_enabled(root):
        return None
    state = lib.load_state(root)
    gate = lib.current_gate(state)
    if gate is None or gate.get("state") in ("done", "rolled_back"):
        return None
    return root, state, gate


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    ctx = _active_gate(data)
    if ctx is None:
        return 0
    root, state, gate = ctx

    # green Bash = 수렴 신호 → 반복 편집 카운터 리셋 + 성공 시각 기록.
    had_counts = bool(
        gate.get("file_edit_counts") or gate.get("file_edit_counts_post_approval")
    )
    gate["last_successful_bash_ts"] = lib.now_iso()
    gate["file_edit_counts"] = {}
    gate["file_edit_counts_post_approval"] = {}
    lib.save_state(root, state)
    if had_counts:
        lib.log_audit(root, "green_bash_reset", gate_id=gate["id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
