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
import plan_gate_cli  # noqa: E402  — COMMANDS SSOT (import 시 main 미실행)

import plan_gate_lib as lib  # noqa: E402

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# 슬래시 유무 모두 처리: /approve-plan, approve-plan, /approve 모두 동작
#
# 전이 경로는 2개이며 둘 다 사용자 게이트키퍼를 지킨다 (CLI 가 idempotent 라 중복 안전):
# 1. 슬래시 커맨드 commands/<token>.md — disable-model-invocation: true 라
#    사용자만 호출 가능 (Claude Skill 도구 자율 호출 차단, 공식 frontmatter).
#    현행 CLI 는 미등록 슬래시 입력을 거부하므로 커맨드 등록이 필수다.
# 2. 슬래시 없는 평문 토큰("done" 등) — 이 UserPromptSubmit 훅이 fallback 처리.
#
# 전이 토큰(인자 없음) — prompt-log PL_TOKEN_VALUES 와 동기 유지(smoke 가 대조).
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

# 명령 토큰(슬래시명 → CLI 액션명, 이름이 다른 것만). F-010: 이 런타임에선 슬래시
# command bash-block 이 자동 실행되지 않아(실측 260619), scope/subplan/status 슬래시가
# silent no-op 됐다. 전이 토큰처럼 UserPromptSubmit fallback 으로도 동작하게 통일한다.
# on/off/git 토글은 의도적 제외(enabled 가드 chicken-egg·혼동 방지).
_COMMAND_ALIASES = {
    "plan-gate-scope-enforce": "scope-enforce",
    "plan-gate-scope-shadow": "scope-shadow",
    "plan-gate-scope-off": "scope-off",
}
# fallback 으로 받을 명령 토큰(슬래시명). subplan 은 인자(<패턴>)를 받는다.
_FALLBACK_COMMANDS = {"status", "subplan", *_COMMAND_ALIASES}


def _resolve_command_token(token: str) -> str | None:
    """명령 토큰(슬래시명) → CLI 액션명. plan_gate_cli.COMMANDS(SSOT)로 검증.

    하드코딩 목록을 늘리는 게 아니라, alias 해소 후 실제 CLI 액션 집합에 존재할
    때만 통과시킨다 — 오타·제거된 명령은 None(미동작) 으로 안전 처리.
    """
    if token not in _FALLBACK_COMMANDS:
        return None
    action = _COMMAND_ALIASES.get(token, token)
    return action if action in plan_gate_cli.COMMANDS else None


def _run_cli(cli: Path, cli_args: list[str], root: Path) -> None:
    try:
        r = subprocess.run(
            [sys.executable, str(cli), *cli_args],
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


def _emit_verified_advisory(root: Path) -> None:
    """verified ✅ 상태에서 평문 프롬프트 → /done 환기만 (자동 done 금지).

    사용자 질의("결과 어땠지?")와 새 작업을 구분할 수 없으므로 자동 실행하지 않는다.
    """
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

    # 토큰 + 인자 분리 (subplan <패턴> 지원). 첫 단어만 정규화한다.
    # 정규화 SSOT(lib.strip_command_prefix): 슬래시·플러그인 네임스페이스 모두 흡수.
    # 인라인 lstrip("/") 만으로는 "/project-init:done" 네임스페이스 prefix 를 못 벗겨
    # 전이가 silent 실패하던 drift 를 제거한다 (260618 F-005).
    parts = prompt.split()
    token = lib.strip_command_prefix(parts[0]) if parts else ""
    extra_args = parts[1:]

    # 1) 전이 토큰(인자 없음) — /done, done, /project-init:done 모두 동작
    if token in _ACTION_TOKENS:
        _run_cli(cli, [_ACTION_TOKENS[token]], root)
        return 0

    # 2) 명령 토큰(scope/subplan/status) — 슬래시 bash-block 미실행 런타임 fallback (F-010)
    action = _resolve_command_token(token)
    if action is not None:
        _run_cli(cli, [action, *extra_args], root)
        return 0

    # 3) verified ✅ 상태에서 평문 프롬프트 → 환기만 (자동 done 금지)
    if prompt:
        _emit_verified_advisory(root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
