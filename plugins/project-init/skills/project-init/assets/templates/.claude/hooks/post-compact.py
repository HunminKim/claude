#!/usr/bin/env python3
"""SessionStart(matcher: compact) hook — compact 후 CLAUDE.md 핵심 섹션 재주입 + plan-gate 자동 복구.

출력 채널:
- CLAUDE.md 재주입: 환기 (exit 0 + stdout hookSpecificOutput.additionalContext JSON)
- plan-gate 복구 알림: 사용자 터미널 전용 (stderr)

공식 스펙: PostCompact 이벤트는 side-effect 전용이라 컨텍스트 주입이
불가능하다. 재주입은 SessionStart(matcher: compact)에서만 동작한다 —
settings.json 배선도 SessionStart.compact 로 등록돼 있다 (채널 교정).

동작 단계:
1. CLAUDE.md 핵심 섹션을 additionalContext 로 Claude context 에 재주입 (워크플로우 규칙 소실 방지)
2. .claude/plan_gate_enabled 자동 복구 — /compact 도중 마커가 휘발돼 plan-gate가 침묵하는 사고 방지
   단, 사용자가 명시 비활성화한 경우(`.claude/plan_gate_off_explicit` 마커)는 복구하지 않는다.
"""
import json, os, sys
from pathlib import Path

CRITICAL_SECTIONS = [
    "## 개발 워크플로우",
    "## 서브에이전트 전략",
    "## 알려진 버그 / 제약",
]

def find_project_root() -> Path:
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root:
        return Path(env_root)
    return Path.cwd()

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

def find_claude_md(root: Path) -> Path | None:
    cand = root / "CLAUDE.md"
    if cand.exists():
        return cand
    for p in [Path.cwd()] + list(Path.cwd().parents):
        cand = p / "CLAUDE.md"
        if cand.exists():
            return cand
    return None

def restore_plan_gate(root: Path) -> None:
    """plan_gate_enabled 마커 자동 복구. explicit off 마커가 있으면 건너뜀."""
    claude_dir = root / ".claude"
    if not claude_dir.exists():
        return
    off_explicit = claude_dir / "plan_gate_off_explicit"
    if off_explicit.exists():
        return
    enabled = claude_dir / "plan_gate_enabled"
    if enabled.exists():
        return
    try:
        enabled.touch()
        print(f"[post-compact] plan-gate 자동 복구: {enabled}", file=sys.stderr)
    except OSError as exc:
        print(f"[post-compact] plan-gate 복구 실패: {exc}", file=sys.stderr)

def main() -> None:
    try:
        json.load(sys.stdin)
    except Exception:
        pass  # stdin 파싱 실패는 silent — 훅이 흐름을 막지 않는다
    root = find_project_root()
    restore_plan_gate(root)
    claude_md = find_claude_md(root)
    if claude_md is None:
        sys.exit(0)
    content = extract_sections(claude_md)
    if not content:
        sys.exit(0)
    div = "━" * 57
    msg = "\n".join([
        "", div,
        "[POST-COMPACT] CLAUDE.md 핵심 규칙 재주입",
        div, "",
        content,
        "", div, "",
    ])
    advisory = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": msg,
        }
    }
    sys.stdout.write(json.dumps(advisory, ensure_ascii=False))

if __name__ == "__main__":
    main()
