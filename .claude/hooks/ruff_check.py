#!/usr/bin/env python3
"""PostToolUse hook (Edit|Write|MultiEdit) — ruff 자동 정렬 + 잔존 lint 보고 + ast 복잡도 경고.

출력 채널:
- 차단 (잔존 lint 위반): exit 2 + stderr — Claude blocking error 주입
- 환기 (ast 복잡도 경고, 관찰 모드): exit 0 + stdout hookSpecificOutput.additionalContext JSON
  — plain stderr 는 Claude 에 도달하지 않아 환기가 무효였음 (채널 교정)
- 사용자전용 (ruff 미설치 안내): exit 0 + stderr, 세션당 1회

동작:
  1. stdin JSON에서 tool_name / tool_input.file_path 추출
  2. .py 파일이 아니거나 파일이 없으면 exit 0 (no-op)
  3. ruff 미설치면 세션당 1회 stderr 안내 후 exit 0 (graceful skip)
  4. ruff check --fix <file> (format은 Surgical Changes 원칙에 따라 비활성화)
  5. 잔존 ruff 오류 있으면 stderr + exit 2 (Claude 컨텍스트 주입)
  6. ast 복잡도 분석: 함수 길이·CC 초과 시 advisory 환기 (차단 분기에선 생략 — 차단 우선)
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ── ast 복잡도 임계값 (현재 경고만 — 차단은 2주 관찰 후 결정) ────────────
FUNC_LEN_WARN = 50
FUNC_LEN_BLOCK = 100  # 향후 차단 예정
CC_WARN = 10
CC_BLOCK = 20  # 향후 차단 예정

_BRANCH_TYPES = (
    ast.If,
    ast.For,
    ast.While,
    ast.Try,
    ast.ExceptHandler,
    ast.With,
    ast.Assert,
    ast.BoolOp,
)

WARN_FLAG = ".claude/state/ruff_warned.flag"


def _read_event() -> dict[str, Any] | None:
    try:
        return json.load(sys.stdin)
    except Exception:
        return None


def _extract_file_paths(tool_name: str, tool_input: dict[str, Any]) -> list[str]:
    """Edit/Write → 단일 파일, MultiEdit → edits[] 배열에서 모든 파일 추출."""
    if tool_name not in ("Edit", "Write", "MultiEdit"):
        return []
    if tool_name in ("Edit", "Write"):
        fp = tool_input.get("file_path")
        return [fp] if isinstance(fp, str) else []
    # MultiEdit: edits 배열에서 file_path 수집 (중복 제거, 순서 유지)
    seen: set[str] = set()
    paths: list[str] = []
    for edit in tool_input.get("edits", []) or []:
        if isinstance(edit, dict):
            fp = edit.get("file_path")
            if isinstance(fp, str) and fp not in seen:
                seen.add(fp)
                paths.append(fp)
    return paths


def _project_root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path.cwd()


def _warn_once(root: Path, session_id: str, msg: str) -> None:
    """세션당 1회 안내 — 플래그에 session_id 를 기록해 세션이 바뀌면 재안내.

    (과거: 존재 여부만 검사해 첫 안내 후 모든 미래 세션에서 영구 침묵 —
     docstring "세션당 한 번" 과 불일치)
    """
    flag = root / WARN_FLAG
    try:
        if flag.exists() and flag.read_text(encoding="utf-8", errors="ignore").strip() == session_id:
            return
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text(session_id + "\n", encoding="utf-8")
    except Exception:
        pass
    sys.stderr.write(msg + "\n")


def _cyclomatic_complexity(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    return sum(1 for n in ast.walk(func_node) if isinstance(n, _BRANCH_TYPES)) + 1


def _check_ast_complexity(target: Path) -> list[str]:
    """함수 길이와 복잡도를 검사해 경고 문자열 목록을 반환."""
    try:
        source = target.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source, filename=str(target))
    except SyntaxError:
        return []  # ruff 가 이미 보고
    except Exception:
        return []

    warnings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        length = (node.end_lineno or node.lineno) - node.lineno + 1
        cc = _cyclomatic_complexity(node)
        name = f"{node.name}() L{node.lineno}"

        if length >= FUNC_LEN_BLOCK:
            warnings.append(f"  {name}: 함수 길이 {length}줄 ⚠️  (차단예정 ≥{FUNC_LEN_BLOCK})")
        elif length >= FUNC_LEN_WARN:
            warnings.append(f"  {name}: 함수 길이 {length}줄 (임계 ≥{FUNC_LEN_WARN})")

        if cc >= CC_BLOCK:
            warnings.append(f"  {name}: 복잡도 {cc} ⚠️  (차단예정 ≥{CC_BLOCK})")
        elif cc >= CC_WARN:
            warnings.append(f"  {name}: 복잡도 {cc} (임계 ≥{CC_WARN})")

    return warnings


def _run_ruff(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ruff", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def main() -> int:
    event = _read_event()
    if event is None:
        return 0

    tool_name = event.get("tool_name", "")
    tool_input = event.get("tool_input", {}) or {}
    file_paths = _extract_file_paths(tool_name, tool_input)
    if not file_paths:
        return 0

    if shutil.which("ruff") is None:
        _warn_once(
            _project_root(),
            event.get("session_id") or "unknown",
            "[ruff_check] ruff 미설치 — Python 품질 훅이 비활성화됩니다.\n"
            "             설치: pip install ruff (또는 pipx install ruff)\n"
            "             이 안내는 세션당 한 번만 표시됩니다.",
        )
        return 0

    exit_code = 0
    advisories: list[str] = []
    for file_path in file_paths:
        target = Path(file_path)
        if target.suffix != ".py" or not target.exists():
            continue

        _run_ruff(["check", "--fix", str(target)])

        final = _run_ruff(["check", str(target)])
        if final.returncode != 0:
            sys.stderr.write(f"\n[ruff_check] {target}: 자동 수정 후 잔존 lint 위반\n")
            if final.stdout:
                sys.stderr.write(final.stdout)
            if final.stderr:
                sys.stderr.write(final.stderr)
            sys.stderr.write(
                "→ Claude: 위 오류를 수정하세요. 반복되면 "
                "pyproject.toml [tool.ruff] ignore/per-file-ignores 검토.\n"
            )
            exit_code = 2
            continue

        # ── ast 복잡도 경고 (관찰 모드 — 환기 채널에 누적) ─────────────────
        complexity_warns = _check_ast_complexity(target)
        if complexity_warns:
            advisories.append(
                f"[ast_check] {target.name}: 복잡도 경고 (작업 계속됩니다)\n"
                + "\n".join(complexity_warns)
                + "\n→ 리팩터링을 고려하세요. ⚠️  표시 항목은 향후 차단 예정입니다."
            )

    # 차단 분기에선 advisory 생략 (차단 우선) — 통과 시에만 환기 JSON 출력
    if exit_code == 0 and advisories:
        sys.stdout.write(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": "\n\n".join(advisories),
                    }
                },
                ensure_ascii=False,
            )
        )

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
