#!/usr/bin/env python3
"""PostToolUse hook (Edit|Write|MultiEdit) — ruff 자동 정렬 + 잔존 lint 보고 + ast 복잡도 경고.

동작:
  1. stdin JSON에서 tool_name / tool_input.file_path 추출
  2. .py 파일이 아니거나 파일이 없으면 exit 0 (no-op)
  3. ruff 미설치면 세션당 1회 stderr 안내 후 exit 0 (graceful skip)
  4. ruff format <file> → ruff check --fix <file>
  5. 잔존 ruff 오류 있으면 stderr + exit 2 (Claude 컨텍스트 주입)
  6. ast 복잡도 분석: 함수 길이·CC 초과 시 stderr 경고 (exit 0, 관찰 모드)
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


def _extract_file_path(tool_name: str, tool_input: dict[str, Any]) -> str | None:
    if tool_name not in ("Edit", "Write", "MultiEdit"):
        return None
    fp = tool_input.get("file_path")
    return fp if isinstance(fp, str) else None


def _project_root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path.cwd()


def _warn_once(root: Path, msg: str) -> None:
    flag = root / WARN_FLAG
    if flag.exists():
        return
    try:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("1\n", encoding="utf-8")
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
    file_path = _extract_file_path(tool_name, tool_input)
    if not file_path:
        return 0

    target = Path(file_path)
    if target.suffix != ".py" or not target.exists():
        return 0

    if shutil.which("ruff") is None:
        _warn_once(
            _project_root(),
            "[ruff_check] ruff 미설치 — Python 품질 훅이 비활성화됩니다.\n"
            "             설치: pip install ruff (또는 pipx install ruff)\n"
            "             이 안내는 세션당 한 번만 표시됩니다.",
        )
        return 0

    _run_ruff(["format", str(target)])
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
        return 2

    # ── ast 복잡도 경고 (관찰 모드 — exit 0) ────────────────────────────
    complexity_warns = _check_ast_complexity(target)
    if complexity_warns:
        sys.stderr.write(f"\n[ast_check] {target.name}: 복잡도 경고 (작업 계속됩니다)\n")
        sys.stderr.write("\n".join(complexity_warns) + "\n")
        sys.stderr.write(
            "→ 리팩터링을 고려하세요."
            " ⚠️  표시 항목은 향후 차단 예정입니다.\n\n"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
