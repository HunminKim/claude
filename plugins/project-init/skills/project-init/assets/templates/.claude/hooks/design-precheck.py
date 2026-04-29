#!/usr/bin/env python3
"""UserPromptSubmit hook — 설계 키워드 감지 시 5단계 체크리스트를 Claude context에 주입.
docs/constraints.yaml 이 없으면 무음 종료 (초기화 전 오작동 방지).
"""
import json, os, re, sys
from pathlib import Path

KO_DESIGN = [
    r"설계", r"아키텍처", r"구조\s*[를을]?\s*잡", r"어떻게\s*만들",
    r"어떻게\s*구현", r"접근\s*방식", r"패턴\s*선택", r"모듈\s*나누",
    r"계층\s*구조", r"의존성\s*설계", r"인터페이스\s*설계",
]
EN_DESIGN = [
    r"\bdesign\b", r"\barchitect", r"\bhow\s+to\s+build", r"\bhow\s+to\s+implement",
    r"\bapproach\b", r"\bstructure\b", r"\binterface\s+design", r"\bdependency\b",
]

def detect_design(text: str) -> bool:
    ko_hits = sum(1 for p in KO_DESIGN if re.search(p, text, re.IGNORECASE))
    en_hits = sum(1 for p in EN_DESIGN if re.search(p, text, re.IGNORECASE))
    return ko_hits >= 1 or en_hits >= 2

def find_project_root() -> Path | None:
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root:
        p = Path(env_root)
        if (p / "docs" / "constraints.yaml").exists():
            return p
        return None
    for p in [Path.cwd()] + list(Path.cwd().parents):
        if (p / "docs" / "constraints.yaml").exists():
            return p
    return None

def main():
    try:
        data = json.load(sys.stdin)
    except Exception as e:
        print(f"[design-precheck] stdin 파싱 오류: {e}", file=sys.stderr)
        sys.exit(0)
    prompt = data.get("prompt", "")
    if not prompt:
        sys.exit(0)
    if not detect_design(prompt):
        sys.exit(0)
    root = find_project_root()
    if root is None:
        sys.exit(0)
    div = "━" * 57
    print("\n".join([
        "", div,
        "[DESIGN PRECHECK] 설계 요청 감지 — 구현 전 5단계 확인",
        div, "",
        "1. docs/constraints.yaml 확인 — 이 설계가 기존 의존성 규칙을 위반하는가?",
        "2. docs/decisions.md 확인 — 이미 결정된 사항과 충돌하는가? (D-번호 참조)",
        "3. docs/glossary.yaml 확인 — 새 개념의 용어가 기존 용어와 일치하는가?",
        "4. docs/plan.md 확인 — 현재 Sprint/Phase 범위 내 작업인가?",
        "5. .claude/memory/workflow.md 확인 — TDD 순서, Phase gate를 지키고 있는가?",
        "",
        "위 5단계 확인 후 설계 결과를 docs/decisions.md 에 D-번호로 기록할 것.",
        div, "",
    ]))

if __name__ == "__main__":
    main()
