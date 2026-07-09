#!/usr/bin/env python3
"""PostToolUse hook (matcher: Edit|Write|MultiEdit|NotebookEdit) — plan-gate 편집 후처리.

두 가지 책임을 한 프로세스에서 처리한다 (매처가 동일해 훅을 늘리지 않는다):

1. todo.md TOCTOU 기준점 재캡처 (아래 참조) — 출력 없음
2. verifier 미호출 상기 — 환기

출력 채널: 환기 (exit 0 + stdout hookSpecificOutput.additionalContext JSON)

── 1. todo.md 기준점 재캡처 ──
plan_gate.py 는 PreToolUse 라 편집 *직전* 상태만 본다. 따라서 게이트 개시 시점에
캡처한 todo_md_sha256 은 "계획을 쓰기도 전의 todo.md" 이고, 이후 계획을 다듬으면
/approve-plan 이 해시 불일치로 1차 실패(rearm)한다 — 정상 흐름에서 오탐이 보장됐다.
여기(PostToolUse)서 todo.md 가 추적 가능한 도구(Edit/Write/…)로 바뀔 때마다 기준점을
갱신한다. 그 결과 해시 불일치의 의미가 뒤집힌다:
  "plan-gate 가 관측하지 못한 경로(Bash sed -i · 외부 에디터)로 todo.md 가 바뀌었다"
= 실제로 은폐 가능한 유일한 변경 벡터만 탐지한다.
(PreToolUse 만으로는 편집 후 내용을 관측할 수 없어 추적/은폐 편집을 구분할 수 없다.)

── 2. verifier 미호출 상기 ──
코드 파일 수정 후 @verifier 를 한 번도 호출하지 않았으면 Claude context 에
verifier 호출 환기 메시지를 주입한다. 차단하지 않는다.

출력 조건 (AND):
  - gate["state"] == "approved" AND verifier_status is None
  - approved_auto == False (project-init 스캐폴딩 제외)
  - 수정 파일이 코드 파일 (docs/ tasks/ .claude/ .git 제외)
  - edit_count_post_approval >= 2 (첫 편집엔 출력 X)
  - 짝수 편집 횟수마다 1번 출력
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_gate_lib as lib  # noqa: E402

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

_SKIP_PREFIXES = ("docs/", "tasks/", ".claude/", ".git")


def _is_todo_md(target: str, root: Path) -> bool:
    """편집 대상이 tasks/todo.md 인가 (절대·상대 경로 모두 대응)."""
    try:
        return Path(target).resolve() == lib.todo_md_path(root).resolve()
    except OSError:
        return False


def _rel_to_root(target: str, root: Path) -> str:
    """프로젝트 루트 기준 상대경로(POSIX 구분자). 밖이면 원본 그대로.

    tool_input.file_path 는 절대경로로 들어오므로 _SKIP_PREFIXES 를 그대로
    startswith 하면 영원히 거짓이 되어 제외 목록이 죽는다(문서 편집에도 상기 발화).
    """
    try:
        return Path(target).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return target


def _recapture_todo_baseline(root: Path, state: dict, gate: dict) -> None:
    """추적된 todo.md 편집 직후 TOCTOU 기준점을 현재 내용으로 갱신한다."""
    sha, mtime = lib.hash_todo_md(root)
    gate["todo_md_sha256"] = sha
    gate["todo_md_mtime"] = mtime
    lib.save_state(root, state)


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    tool_name = data.get("tool_name", "")
    # NotebookEdit 포함 — hooks.json matcher 와 일치 (plan_gate 의 notebook 처리와 대칭)
    if tool_name not in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return 0

    root = lib.find_project_root()
    if root is None or not lib.is_plan_gate_enabled(root):
        return 0

    state = lib.load_state(root)
    gate = lib.current_gate(state)

    tool_input = data.get("tool_input", {}) or {}
    target = lib.extract_target_file(tool_name, tool_input, project_root=root)

    # ── 1) todo.md 기준점 재캡처 ────────────────────────────────────────
    # 게이트 상태와 무관하게 갱신한다 (created 에서 approve 가 소비. approved 이후
    # 갱신은 무해 — replan 이 None 으로 리셋한다). todo.md 는 tasks/ 라 아래
    # verifier 상기 대상이 아니므로 여기서 종료한다.
    if gate is not None and target and _is_todo_md(target, root):
        _recapture_todo_baseline(root, state, gate)
        return 0

    # ── 2) verifier 미호출 상기 ─────────────────────────────────────────
    if gate is None or gate["state"] != "approved":
        return 0
    if gate.get("verifier_status") is not None:
        return 0
    if gate.get("approved_auto"):
        return 0

    if not target:
        return 0
    if any(_rel_to_root(target, root).startswith(p) for p in _SKIP_PREFIXES):
        return 0

    count = gate.get("edit_count_post_approval", 0)
    if count < 2 or count % 2 != 0:
        return 0

    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": (
                f"[plan-gate] 💡 코드 수정 {count}회 — @verifier 검증이 아직 없습니다.\n"
                "  기능 구현이 완료됐으면 @verifier 를 호출해 검증하세요."
            ),
        }
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
