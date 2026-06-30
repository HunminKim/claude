#!/usr/bin/env python3
"""delegation 훅 공통 유틸 — 위임 due diligence 훅들이 공유하는 단일 출처.

delegation_due_diligence(UserPromptSubmit)·delegation_prompt_check(PreToolUse)가
같은 유틸-에이전트 화이트리스트와 "정의된 도메인 에이전트인가" 판정을 쓴다. 두 파일에
복붙되어 드리프트하던 것(6번째 유틸 에이전트 추가 시 두 곳을 동시에 고쳐야 했음)을
여기로 통합한다. stdin 을 읽지 않는 lib 모듈이라 stdio reconfigure 블록은 두지 않는다.
"""

from __future__ import annotations

from pathlib import Path

# 내장·유틸 에이전트 — 도메인 위임이 아니므로 due diligence 검사에서 제외한다.
UTILITY_SUBAGENTS: frozenset[str] = frozenset(
    {"Plan", "Explore", "verifier", "general-purpose", "statusline-setup"}
)


def is_defined_domain_agent(root: str | None, name: str) -> bool:
    """name 이 프로젝트의 커스텀 도메인 에이전트(.claude/agents/<name>.md)면 True.

    특정 이름(backend 등)을 박지 않고 프로젝트가 정의한 어떤 에이전트(@data, @mobile)에도
    일반화한다. 유틸 에이전트(Plan/Explore/verifier 등)·미정의 에이전트는 False.
    """
    if not name or name in UTILITY_SUBAGENTS:
        return False
    if not root:
        return False
    return (Path(root) / ".claude" / "agents" / f"{name}.md").exists()
