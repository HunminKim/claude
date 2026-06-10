#!/usr/bin/env python3
"""UserPromptSubmit hook — plan-gate 토큰 fallback 처리.

출력 채널: 환기 (exit 0 + stdout hookSpecificOutput.additionalContext JSON
— verified ✅ 분기. CLI 위임 출력은 CLI 의 채널을 그대로 전달)

슬래시 커맨드(`/approve-plan` 등)는 commands/*.md 정의가 1차로 처리하지만,
사용자가 슬래시 커맨드 형태가 아닌 평문 메시지로 토큰을 입력해도 동일하게
plan-gate 상태를 갱신할 수 있도록 fallback을 제공한다.

idempotent: 같은 토큰을 슬래시 커맨드 + 메시지 둘 다로 받아도 안전.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_gate_lib as lib  # noqa: E402

# 슬래시 유무 모두 처리: /approve-plan, approve-plan, /approve 모두 동작
#
# 전이 경로는 2개이며 둘 다 사용자 게이트키퍼를 지킨다 (CLI 가 idempotent 라 중복 안전):
# 1. 슬래시 커맨드 commands/<token>.md — disable-model-invocation: true 라
#    사용자만 호출 가능 (Claude Skill 도구 자율 호출 차단, 공식 frontmatter).
#    현행 CLI 는 미등록 슬래시 입력을 거부하므로 커맨드 등록이 필수다.
# 2. 슬래시 없는 평문 토큰("done" 등) — 이 UserPromptSubmit 훅이 fallback 처리.
_ACTION_TOKENS = {
    "approve-plan": "approve",
    "approve": "approve",
    "done": "done",
    "skip": "skip",
    "keep": "skip",
    "skip-verify": "skip-verify",
    "rollback": "rollback",
    "retry": "retry",
    "replan": "replan",
}


def _run_cli(cli: Path, action: str, root: Path) -> None:
    try:
        r = subprocess.run(
            [sys.executable, str(cli), action],
            capture_output=True,
            text=True,
            cwd=str(root),
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(root)},
        )
        if r.stdout:
            sys.stdout.write(r.stdout)
        if r.stderr:
            sys.stderr.write(r.stderr)
    except Exception as e:
        sys.stderr.write(f"[plan-gate approval] CLI 실행 실패: {e}\n")


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    prompt = (data.get("prompt") or "").strip()

    root = lib.find_project_root()
    if root is None or not lib.is_plan_gate_enabled(root):
        return 0

    cli = Path(__file__).parent / "plan_gate_cli.py"

    normalized = prompt.lstrip("/")
    if normalized in _ACTION_TOKENS:
        # 슬래시 유무 무관하게 처리 (/approve-plan, approve-plan 모두 동작)
        _run_cli(cli, _ACTION_TOKENS[normalized], root)
    elif prompt:
        # verified ✅ 상태에서 비-슬래시 프롬프트 → 환기만 (자동 done 하지 않음)
        # 사용자 질의("결과 어땠지?")와 새 작업을 구분할 수 없으므로 자동 실행 금지
        state = lib.load_state(root)
        gate = lib.current_gate(state)
        if gate and gate["state"] == "verified" and gate.get("verifier_status") == "✅":
            advisory = {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": (
                        "[plan-gate] 이전 gate verified ✅ — "
                        "작업이 끝났으면 /done 입력. 이어서 질문 중이면 무시."
                    ),
                }
            }
            sys.stdout.write(json.dumps(advisory, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
