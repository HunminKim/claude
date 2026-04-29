#!/usr/bin/env python3
"""UserPromptSubmit hook — 한국어 교정 신호 감지 시 메인 Claude에 alert.
직접 lessons.md를 Edit하지 않는다 (4컬럼 패턴 추출은 LLM 판단 필요).
"""
import json, re, sys
from pathlib import Path

STRONG = [r"아니야", r"아니지", r"틀렸", r"잘못(됐|했|된)", r"그게\s*아니",
          r"하지\s*마", r"하면\s*안\s*돼", r"다시\s*해"]
MEDIUM = [r"말고", r"대신", r"~?로\s*바꿔", r"왜\s+\S+했", r"아까\s+왜",
          r"방금\s+왜", r"빠뜨렸", r"안\s*날라", r"\bno,\b", r"\bwrong\b",
          r"\bdon'?t\b", r"\binstead\b"]

def detect(text):
    for p in STRONG:
        if re.search(p, text, re.IGNORECASE): return "STRONG"
    for p in MEDIUM:
        if re.search(p, text, re.IGNORECASE): return "MEDIUM"
    return None

def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    prompt = data.get("prompt", "")
    if not prompt: sys.exit(0)
    level = detect(prompt)
    if not level: sys.exit(0)
    cwd = Path.cwd()
    lessons = None
    for p in [cwd] + list(cwd.parents):
        cand = p / "tasks" / "lessons.md"
        if cand.exists():
            lessons = cand
            break
    if lessons is None: sys.exit(0)
    div = "━" * 57
    print("\n".join([
        "", div,
        f"[USER CORRECTION DETECTED — {level}]", div, "",
        "이번 사용자 메시지에 교정 신호가 감지되었다.",
        "다음 응답 시작 전 반드시:",
        f"  1. {lessons} '행동 교정 패턴' 표에 항목 추가",
        "     컬럼: 날짜(KST) | 상황 | 잘못한 접근 | 올바른 접근",
        "  2. 동일 실수 재발 방지 규칙 1줄 도출",
        "  3. 그 후 사용자 요청에 응답",
        div, "",
    ]))

if __name__ == "__main__":
    main()
