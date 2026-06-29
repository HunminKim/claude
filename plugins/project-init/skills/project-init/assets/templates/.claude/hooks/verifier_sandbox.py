#!/usr/bin/env python3
"""PreToolUse hook — production 경로 쓰기 의심 명령 경고.

Bash 명령이 production 경로(runs/, outputs/ 등)에 직접 쓰기를 시도하면
stderr에 경고를 출력하고 exit 2로 Claude 컨텍스트에 주입한다.
차단하지 않는다 — 메인이 인지하고 판단하도록 알리는 것이 목적이다.

감지 범위: shell redirect, Python open(쓰기 모드), 주요 직렬화 함수.
간접 쓰기(project 내부 함수 경유)는 감지 불가 — verifier.md 원칙으로 보완.

production_paths 는 docs/constraints.yaml 에서 프로젝트별 설정 가능.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

_DEFAULT_PROD_PATHS = ["runs/", "outputs/", "data/"]

# (regex_template, 설명) — {alt} 는 production 경로 alternation으로 치환
_PATTERNS: list[tuple[str, str]] = [
    (r">{{1,2}}\s*(?:{alt})", "shell redirect → production 경로"),
    (r"open\s*\(\s*(?:f?['\"])(?:{alt})[^'\"]*['\"],\s*['\"][wab+]", "Python open(쓰기 모드)"),
    (r"(?:torch\.save|np\.save(?:txt)?|pickle\.dump|json\.dump)\s*\([^)]*(?:{alt})", "직렬화 함수"),
    (r"(?:shutil\.copy2?|shutil\.move)\s*\([^)]*(?:{alt})", "shutil write"),
]


def _find_project_root() -> Path | None:
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root:
        return Path(env_root)
    for p in [Path.cwd(), *Path.cwd().parents]:
        if (p / "CLAUDE.md").exists():
            return p
    return None


def _load_prod_paths(root: Path) -> list[str]:
    try:
        import yaml
        with open(root / "docs" / "constraints.yaml", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        paths = data.get("production_paths", [])
        if paths:
            return [p.rstrip("/") + "/" for p in paths]
    except Exception:
        pass
    return _DEFAULT_PROD_PATHS


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "")
    if not command:
        return 0

    root = _find_project_root()
    prod_paths = _load_prod_paths(root) if root else _DEFAULT_PROD_PATHS
    alt = "|".join(re.escape(p) for p in prod_paths)

    matched: list[str] = []
    for template, label in _PATTERNS:
        if re.search(template.format(alt=alt), command, re.DOTALL):
            matched.append(label)

    if not matched:
        return 0

    preview = command[:200] + ("..." if len(command) > 200 else "")
    sys.stderr.write(
        "\n[verifier-sandbox] ⚠️  production 경로 쓰기 의심 명령 감지\n"
        f"  감지: {', '.join(matched)}\n"
        f"  명령: {preview}\n"
        "  verifier라면 /tmp/verifier_$$/ 격리 디렉토리를 사용하세요.\n"
        "  메인 Claude의 정상 호출이라면 무시하세요.\n\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
