#!/usr/bin/env python3
"""PostCompact hook — /compact 후 CLAUDE.md 핵심 섹션을 Claude context에 재주입.
컨텍스트 압축 후 워크플로우 규칙·제약이 소실되지 않도록 한다.
"""
import json, sys
from pathlib import Path

CRITICAL_SECTIONS = [
    "## 개발 워크플로우",
    "## 서브에이전트 전략",
    "## 알려진 버그 / 제약",
]

def extract_sections(claude_md: Path) -> str:
    lines = claude_md.read_text(encoding="utf-8").splitlines()
    result = []
    capturing = False
    for line in lines:
        if line.startswith("## "):
            capturing = line.strip() in CRITICAL_SECTIONS
        if capturing:
            result.append(line)
    return "\n".join(result).strip()

def find_claude_md(cwd: Path):
    for p in [cwd] + list(cwd.parents):
        cand = p / "CLAUDE.md"
        if cand.exists():
            return cand
    return None

def main():
    try:
        json.load(sys.stdin)
    except Exception:
        pass
    claude_md = find_claude_md(Path.cwd())
    if claude_md is None:
        sys.exit(0)
    content = extract_sections(claude_md)
    if not content:
        sys.exit(0)
    div = "━" * 57
    print("\n".join([
        "", div,
        "[POST-COMPACT] CLAUDE.md 핵심 규칙 재주입",
        div, "",
        content,
        "", div, "",
    ]))

if __name__ == "__main__":
    main()
