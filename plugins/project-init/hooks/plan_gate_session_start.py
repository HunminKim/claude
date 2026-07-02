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
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


HARNESS_UPDATE_MARKER = ".claude/state/.harness_update_in_progress"


def _emit_context(text: str) -> None:
    """SessionStart additionalContext JSON 출력 (환기 채널)."""
    result = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


def _heal_interrupted_update(root) -> str | None:
    """중단된 /harness-update 가 끈 plan-gate 를 자동 복구한다. 복구 시 환기 메시지 반환.

    harness-update 는 대량 파일 갱신 동안 plan-gate 를 끄고(마커 생성 + enabled 삭제)
    완료 시 복원한다. 세션이 중간에 끊기면 복원 단계에 도달하지 못해 plan-gate 가
    조용히 영구 비활성으로 남는다 — 새 세션 진입 = 그 업데이트는 죽었다는 뜻이므로
    여기서 enabled 를 되살리고 마커를 지운다 (Claude 성실함 의존 제거).
    """
    marker = root / HARNESS_UPDATE_MARKER
    if not marker.exists():
        return None
    try:
        marker.unlink()
        lib.enable_plan_gate(root)
    except OSError:
        return None
    return (
        "[plan-gate] ♻️  이전 세션의 /harness-update 가 완료되지 못한 채 끊겨 "
        "plan-gate 가 꺼진 상태였습니다 — 자동으로 다시 켰습니다.\n"
        "  하네스 업데이트가 미완일 수 있으니 필요하면 /harness-update 를 재실행하세요."
    )


def main() -> int:
    try:
        json.load(sys.stdin)
    except Exception:
        return 0

    root = lib.find_project_root()
    if root is None:
        return 0

    # 중단된 harness-update 자가복구 — enabled 가 꺼진 상태를 고치는 단계라
    # is_plan_gate_enabled 체크보다 반드시 앞서야 한다.
    heal_msg = _heal_interrupted_update(root)

    if not lib.is_plan_gate_enabled(root):
        return 0

    state = lib.load_state(root)
    gate = lib.current_gate(state)

    # 보고할 게이트가 없어도 자가복구가 있었으면 그 사실은 환기한다
    if gate is None or gate["state"] not in ("created", "approved", "verified"):
        if heal_msg:
            _emit_context(heal_msg)
        return 0
    body = _gate_report(gate)
    if heal_msg:
        body = heal_msg + "\n" + body
    _emit_context(body)
    return 0


def _elapsed_human(gate: dict) -> str:
    """마지막 활동으로부터 경과 시간을 사람이 읽는 형태로."""
    ts_str = gate.get("last_edit_ts") or gate.get("approved_at") or gate.get("created_at")
    if not ts_str:
        return "-"
    try:
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        elapsed = datetime.now(timezone.utc) - ts
        h = int(elapsed.total_seconds() // 3600)
        m = int((elapsed.total_seconds() % 3600) // 60)
        return f"{h}시간 {m}분 전"
    except Exception:
        return "-"


def _gate_report(gate: dict) -> str:
    """세션 재개 시 주입할 게이트 현황 리포트 본문."""
    g_state = gate["state"]
    max_repeat = lib._max_code_repeat(gate)
    auto_label = "자동" if gate.get("approved_auto") else "명시"
    approved_at = gate.get("approved_at") or "-"
    ckpt = (gate.get("checkpoint_commit") or "")[:12] or ("cp" if gate.get("cp_snapshot") else "(없음)")
    elapsed_str = _elapsed_human(gate)

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
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
