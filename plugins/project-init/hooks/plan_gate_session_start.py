#!/usr/bin/env python3
"""SessionStart hook — 세션 재개 시 plan-gate 상태 능동 보고.

출력 채널: 환기 (exit 0 + stdout hookSpecificOutput.additionalContext JSON)

새 세션(시작·재개·compact 후) 진입 시 활성 gate가 있으면
hookSpecificOutput.additionalContext 채널로 현황을 즉시 주입한다.
Claude가 메시지를 받기 전에 컨텍스트가 로드되므로 첫 응답부터
올바른 상태를 반영한다.

주의: top-level {"additionalContext": ...} 는 공식 스펙에 없는 형태라
무시된다 — 반드시 hookSpecificOutput 래퍼로 감싼다 (v1.28.0 수정).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_gate_lib as lib  # noqa: E402

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


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

    max_repeat = lib._max_code_repeat(gate)
    auto_label = "자동" if gate.get("approved_auto") else "명시"
    approved_at = gate.get("approved_at") or "-"
    ckpt = (gate.get("checkpoint_commit") or "")[:12] or ("cp" if gate.get("cp_snapshot") else "(없음)")

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
        f"  edits   : {gate['edit_count']}회 (승인 후 파일최대 {max_repeat}/{lib.TRIGGER_REPEAT_RATIO}회)",
        f"  승인    : {auto_label} ({approved_at})",
        f"  마지막  : {elapsed_str}",
        f"  ckpt    : {ckpt}",
    ]

    if g_state == "verified" and gate.get("verifier_status") == "❌":
        lines += [
            "",
            "⚠️  verifier 검증 실패 상태입니다.",
            "  /retry 또는 /rollback 을 입력해 해결하세요.",
        ]
    elif g_state == "approved":
        near_repeat = max_repeat >= lib.TRIGGER_REPEAT_RATIO - 1
        if near_repeat:
            lines += [
                "",
                f"⚠️  같은 파일 반복 임박 ({max_repeat}/{lib.TRIGGER_REPEAT_RATIO}, 테스트 통과 시 리셋)",
                "  작업을 마치려면 /done, 계획 조정은 /replan.",
            ]
        else:
            lines += [
                "",
                f"  승인 후 : 파일최대 {max_repeat}/{lib.TRIGGER_REPEAT_RATIO}",
                "  새 작업이면 /done 후 시작하세요.",
            ]
    elif g_state == "created":
        lines += [
            "",
            "  아직 계획 미승인 상태입니다. tasks/todo.md 작성 후 /approve-plan.",
        ]

    lines += ["─" * 60, ""]

    result = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n".join(lines),
        }
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
