#!/usr/bin/env python3
"""PostToolUse + PostToolUseFailure hook (Bash) — layer-2 스코프 스윕 + green-Bash thrash 리셋.

역할 1 (layer-2 스코프 강제, R1): Bash 가 스코프 밖 파일을 만들거나 수정했는지
git status 로 훑어, enforce 면 체크포인트 상태로 롤백하고 shadow 면 기록만 한다.
touched 매니페스트가 아닌 실제 working tree 변경을 보므로 `echo > 스코프밖파일`
같은 Edit 우회까지 잡는다(rev.3 R1). 성공/실패 무관(실패한 Bash 도 파일을 남길 수 있다)
— 공식 스펙상 PostToolUse 는 성공 시에만 발화하므로 PostToolUseFailure 에도 배선한다
(hooks.json 양쪽 등록). 실패 이벤트에서는 스윕만 하고 green-reset 은 하지 않는다.

역할 2 (green-bash 수렴 신호, D9): Bash 성공(exit 0)이면 현재 게이트의 파일별
반복 편집 카운터(file_edit_counts)를 리셋해, 수렴 중인 정상 반복은 thrash 트리거에
걸리지 않고 수렴 없는 flailing 만 도달하게 한다.

역할 3 (Bash 전용 작업의 verifier 상기): verifier_remind 는 매처가 Edit|Write 계열이라
`docker build`·학습 API 호출처럼 파일을 고치지 않는 작업에선 영원히 발화하지 않았다.
성공한 실질 Bash(내장 read-only 집합 밖)를 세어 같은 총계·같은 조건으로 상기한다.
채널이 역할 1 과 동일(additionalContext)하므로 훅을 늘리지 않고 한 프로세스에서 처리한다.

동작 단계:
  1. stdin JSON 에서 tool_name=Bash + 활성 게이트 확인 (아니면 no-op)
  2. scope_mode != off + 매니페스트 선언 시 layer-2 스윕(enforce 롤백 / shadow 기록)
  3. exit 0 이면 green-bash 리셋
  4. exit 0 + 실질 명령이면 bash_count_post_approval 증가 → verifier 상기 판정
  5. 스코프 위반·verifier 상기가 있었으면 Claude 에 환기(additionalContext, 병합 1회)

출력 채널: 환기 (스코프 위반 시 exit 0 + stdout hookSpecificOutput.additionalContext
JSON — Claude 가 롤백·위반을 인지해 desync 방지). 위반 없으면 무출력(silent exit 0).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_gate_lib as lib  # noqa: E402

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _active_gate(data: dict[str, Any]):
    """Bash + 활성 게이트면 (root, state, gate), 아니면 None. (exit code 무관)"""
    if data.get("tool_name") != "Bash":
        return None
    root = lib.find_project_root(data.get("cwd") or None)
    if root is None or not lib.is_plan_gate_enabled(root):
        return None
    state = lib.load_state(root)
    gate = lib.current_gate(state)
    if gate is None or gate.get("state") in ("done", "rolled_back"):
        return None
    return root, state, gate


def _emit_advisory(msg: str) -> None:
    """스코프 위반 환기 — additionalContext 로 Claude context 에 주입."""
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": msg,
        }
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def _sweep_advisory(root, gate, mode: str) -> str | None:
    """layer-2 스윕 실행 후 환기 메시지(있으면). off/매니페스트 없음/위반 없음 → None."""
    if mode == "off" or not lib.has_manifest(gate):
        return None
    res = lib.scope_sweep(root, gate, mode)
    removed, warned = res["removed"], res["warned"]
    if not removed and not warned:
        return None
    effective = lib.sweep_effective_mode(root, gate, mode)
    return lib.format_scope_sweep(removed, warned, effective, mode)


def _verifier_reminder(gate) -> str | None:
    """승인 후 실질 작업이 짝수번째면 verifier 상기 메시지. 조건은 verifier_remind.py 와 동일."""
    if gate.get("state") != "approved":
        return None
    if gate.get("verifier_status") is not None or gate.get("approved_auto"):
        return None
    count = lib.verifier_remind_count(gate)
    if count < 2 or count % 2 != 0:
        return None
    return (
        f"[plan-gate] 💡 승인 후 작업 {count}회 — @verifier 검증이 아직 없습니다.\n"
        "  빌드·배포·학습처럼 파일을 고치지 않는 작업도 검증 대상입니다. "
        "종료 코드 0 과 HTTP 2xx 는 작업 성공이 아닙니다 — @verifier 를 호출하세요."
    )


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    ctx = _active_gate(data)
    if ctx is None:
        return 0
    root, state, gate = ctx
    # 실패 이벤트(PostToolUseFailure)는 절대 수렴 신호가 아니다 — tool_response 에
    # exit_code 가 없더라도 이벤트명으로 fail-closed 판정한다(기본값 0 오인 방지).
    failed_event = data.get("hook_event_name") == "PostToolUseFailure"
    exit_code = (data.get("tool_response") or {}).get("exit_code", 0)

    # ── layer-2 스코프 스윕 (R1) — 성공/실패 무관 ──────────────────────────
    advisory = _sweep_advisory(root, gate, lib.scope_mode(root))

    # ── green Bash = 수렴 신호 → 반복(thrash) 카운터 리셋 ──────────────────
    # exit 0 이라도 "검증 명령"(테스트·빌드·린트)만 수렴으로 인정한다. ls·cat·git
    # status 같은 읽기전용 성공이 카운터를 리셋하면 thrash·롤오버 신호가 무력화된다.
    command = (data.get("tool_input") or {}).get("command", "")
    green = not failed_event and exit_code == 0
    if green and lib.is_verification_command(command):
        had_counts = bool(gate.get("file_edit_counts"))
        gate["last_successful_bash_ts"] = lib.now_iso()
        gate["file_edit_counts"] = {}
        lib.save_state(root, state)
        if had_counts:
            lib.log_audit(root, "green_bash_reset", gate_id=gate["id"])

    # ── 실질 Bash 누적 → verifier 상기 (Bash 전용 작업 사각지대 봉합) ────────
    # 성공한 실질 작업만 센다. 실패한 빌드는 "검증할 것이 생겼다"는 신호가 아니다.
    reminder = None
    if green and lib.is_substantive_command(command):
        gate["bash_count_post_approval"] = gate.get("bash_count_post_approval", 0) + 1
        lib.save_state(root, state)
        reminder = _verifier_reminder(gate)

    msgs = [m for m in (advisory, reminder) if m]
    if msgs:
        _emit_advisory("\n\n".join(msgs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
