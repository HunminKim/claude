#!/usr/bin/env python3
"""UserPromptSubmit hook — 작업 경계 자동 감지.

출력 채널: 환기 (exit 0 + stdout hookSpecificOutput.additionalContext JSON)

Layer 0 — 새 요청 진입(열린 게이트 없음) 시 '계획 필요 여부 자가 판단' cue:
  게이트가 없거나 닫힌 상태에서 새 프롬프트가 들어오면, 클로드가 작업 성격을
  스스로 판단하도록 가벼운 환기를 1회 주입한다(slash·짧은 프롬프트·스로틀 제외).
  정책 본문은 workflow.md 상주 — 여기선 재무장만. 강제 아님(클로드 판단이 1차).

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

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def emit_advisory(msg: str) -> None:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": msg,
        }
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))


def _cue_due(state: dict) -> bool:
    """plan-worthiness cue 스로틀: 직전 cue 로부터 임계 분(分) 경과했으면 True."""
    ts = state.get("last_semantic_cue_ts")
    if not ts:
        return True
    try:
        last = datetime.fromisoformat(ts)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last) >= timedelta(minutes=lib.SEMANTIC_CUE_THROTTLE_MIN)
    except Exception:
        return True


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    root = lib.find_project_root(payload.get("cwd") or None)
    if root is None or not lib.is_plan_gate_enabled(root):
        return 0

    state = lib.load_state(root)
    gate = lib.current_gate(state)

    prompt_text = (payload.get("message") or payload.get("prompt") or "").strip()
    is_slash = prompt_text.startswith("/")

    # ── 새 요청 진입 (열린 게이트 없음) → 계획 필요 여부 자가 판단 cue ──────
    # 게이트가 없거나 이미 닫힌(새 작업) 상태에서만. slash·짧은 프롬프트·스로틀로
    # 매 프롬프트 도배를 막는다. 강제 아닌 환기 — 클로드 판단이 1차, 정책은 workflow.md.
    if gate is None or gate["state"] in ("done", "rolled_back"):
        if not is_slash and len(prompt_text) >= lib.MIN_PROMPT_CHARS and _cue_due(state):
            state["last_semantic_cue_ts"] = lib.now_iso()
            lib.save_state(root, state)
            emit_advisory(lib.format_plan_worthiness_cue())
        return 0

    if gate["state"] not in ("approved", "created"):
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
        emit_advisory(
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
    if is_slash:
        return 0

    if gate["state"] == "approved":
        max_repeat = lib._max_code_repeat(gate)
        auto_label = "자동" if gate.get("approved_auto") else "명시"
        near_msg = (
            f"\n  ⚠️  같은 파일 반복 {max_repeat}/{lib.TRIGGER_REPEAT_RATIO} (수렴 안 되면 재검토)"
            if max_repeat >= lib.TRIGGER_REPEAT_RATIO - 1
            else ""
        )
        emit_advisory(
            f"[plan-gate] 진행 중인 작업 있음 (approved/{auto_label}, 편집 {edit_count}회){near_msg}\n"
            f"  구현 시작 전 사용자에게 반드시 물어보세요:\n\n"
            f'  "새 요청이 들어왔습니다. 어떻게 진행할까요?\n'
            f"   1) /replan  — 새 요청을 현재 계획에 추가하고 바로 진행\n"
            f'   2) 완료 우선 — 지금 작업 끝낸 뒤 /done, 그다음 새 요청 시작"\n\n'
            f"  답변 전 코드 작성·파일 수정을 시작하지 마세요.\n\n"
        )
    # state == "created": 미승인 게이트엔 /done 을 강요하지 않는다 — 작은 편집은
    # approve·done 둘 다 불요. 닫기는 자동 롤오버(green Bash 수렴 / 위 Layer 1 idle)가
    # 담당한다. 수렴 없는 반복만 plan_gate 의 5회 트리거가 차단한다.

    return 0


if __name__ == "__main__":
    sys.exit(main())
