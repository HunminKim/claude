#!/usr/bin/env python3
"""Stop hook — Claude 응답 종료 직전 plan-gate 상태 리마인더.

출력 채널: 환기 (exit 0 + stdout hookSpecificOutput.additionalContext JSON)

공식 스펙: Stop 훅의 additionalContext는 턴 끝에 주입되고 대화가
이어지므로 Claude가 다음 응답에서 반영할 수 있다. systemMessage는
사용자 터미널 전용이라 환기 의도에 맞지 않는다 (v1.28.0 채널 교정).

approved 게이트가 활성 상태이고 응답 중에 편집이 발생했을 때
(last_edit_ts가 응답 시작 이후로 갱신된 경우) 현재 한도 소모 현황을
Claude에게 환기한다. 편집이 없었던 응답(조회·대화)에는 출력하지 않아
노이즈를 최소화한다.

한도의 70% 이상 소진 시 /compact 권고 + compact 후 이어받기 프롬프트를 함께 출력한다.

차단하지 않는다 (정보 제공 전용 — Claude 가 다음 응답에서 verifier 호출,
/done 안내 등 자기 흐름에 반영).
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

# 이번 응답 내에서 편집이 있었다고 간주할 최대 경과 시간
_RECENT_EDIT_WINDOW_SECONDS = 300  # 5분 이내 편집 = 이번 응답의 작업


def _emit_advisories(items: list[str]) -> None:
    if not items:
        return
    combined = "\n\n".join(items)
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "Stop",
            "additionalContext": combined,
        }
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def _verifier_advisory(root, state, gate) -> str | None:
    """verifier 미호출 경고 — 같은 편집 배치에서 1회만 (edit_count 기반 dedup).

    매 턴 같은 경고가 반복되던 문제(리포트 260612 B-3) 해소: 1회 emit 후 현재
    edit_count 를 gate 에 기록하고, 새 편집(edit_count 전진) 전까지 억제한다.
    /retry·/replan 은 edit_count 를 유지/리셋하므로 자연히 다음 편집에서 재발화한다.
    auto-approved 게이트(스캐폴딩)는 verifier 대상이 아니므로 제외.
    """
    if not (
        gate.get("edit_count", 0) > 0
        and gate.get("verifier_status") is None
        and not gate.get("approved_auto")
    ):
        return None
    edit_count = gate.get("edit_count", 0)
    if gate.get("verifier_advisory_seen_at_edit") == edit_count:
        return None  # 이 편집 배치에서 이미 경고함 — 중복 억제
    gate["verifier_advisory_seen_at_edit"] = edit_count
    lib.save_state(root, state)
    return (
        "[plan-gate] ⚠️  @verifier 미호출\n"
        "  편집이 있었지만 검증이 없습니다. /done 전 @verifier 호출 필수.\n"
        "  건너뛰려면 /skip-verify 를 명시적으로 입력."
    )


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    # stop_hook_active=true → 이미 직전 Stop 훅 때문에 대화가 이어지는 중이다.
    # Stop 의 additionalContext 는 decision:block 과 동일하게 "대화 계속"을 유발하므로,
    # 여기서 또 주입하면 해소되지 않는 조건으로 최대 8회(Claude Code 하드캡)까지
    # 불필요하게 턴이 연장된다 → 재주입을 억제한다 (무한 연장 방지).
    if data.get("stop_hook_active"):
        return 0

    advisories: list[str] = []

    root = lib.find_project_root()
    if root is None or not lib.is_plan_gate_enabled(root):
        return 0

    state = lib.load_state(root)
    gate = lib.current_gate(state)

    if gate is None or gate["state"] != "approved":
        return 0

    # verifier 미호출 경고 (편집 배치당 1회 — dedup)
    verifier_msg = _verifier_advisory(root, state, gate)
    if verifier_msg:
        advisories.append(verifier_msg)

    # 이번 응답 중 편집이 있었는지 확인 (last_edit_ts 기준)
    last_edit_str = gate.get("last_edit_ts")
    if not last_edit_str:
        _emit_advisories(advisories)
        return 0

    try:
        last_edit = datetime.fromisoformat(last_edit_str)
        if last_edit.tzinfo is None:
            last_edit = last_edit.replace(tzinfo=timezone.utc)
        elapsed = datetime.now(timezone.utc) - last_edit
        if elapsed > timedelta(seconds=_RECENT_EDIT_WINDOW_SECONDS):
            _emit_advisories(advisories)
            return 0  # 최근 편집 없음 — 이번 응답에서 코드 수정 없었음
    except Exception:
        _emit_advisories(advisories)
        return 0

    # thrash(같은 파일 반복) 임박 시에만 환기 — 평시 always-on 환기는 제거
    # (Stop 훅 turn-extension 노이즈 방지, 이전 리뷰 C-8).
    max_repeat = lib._max_code_repeat(gate)
    if max_repeat >= lib.TRIGGER_REPEAT_RATIO - 1:
        auto_label = "자동" if gate.get("approved_auto") else "명시"
        advisories.append(
            f"[plan-gate] ⚠️  approved({auto_label}) 같은 파일 반복"
            f" {max_repeat}/{lib.TRIGGER_REPEAT_RATIO}"
            f" — 수렴 안 되면 멈추고 재검토(테스트 통과 시 리셋). 완료면 /done"
        )

    _emit_advisories(advisories)
    return 0


if __name__ == "__main__":
    sys.exit(main())
