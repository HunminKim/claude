#!/usr/bin/env python3
# [prompt-log] removable plugin — see plugins/prompt-log/README.md
"""UserPromptSubmit 훅 — 새 prompt 도착 시 처리.

출력 채널: 사용자전용 (exit 0 + stderr — flush 실패 경고만. 평시 무출력)

동작:
1. 동의 검사 (글로벌 whitelist + 프로젝트 marker). 미동의면 즉시 exit 0
2. 기존 active prompt가 있으면 finalize → 월별 jsonl flush
3. 새 active record 생성 (sanitized prompt, 시작 ts)
4. 사용자 토큰(/approve-plan 등)이면 직전 active record의 user_tokens에 append만 하고
   새 active 생성 X — 토큰은 plan-gate 동작이지 별개 prompt 아님

이 훅은 plan-gate / detect_user_correction.py 등 다른 훅 동작을 막지 않는다.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prompt_log_lib as pl  # noqa: E402

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    prompt_text = data.get("prompt") or ""
    session_id = data.get("session_id") or ""
    if not prompt_text:
        return 0

    root = pl.pl_find_project_root()
    if root is None:
        return 0

    if not pl.pl_is_consented(root):
        return 0  # default deny: 동의 안 한 프로젝트는 저장 X

    sanitized = pl.pl_sanitize(prompt_text).strip()
    # 평문("done")·슬래시("/done")·네임스페이스("/project-init:done") 모두 정규화
    token_value = pl.pl_normalize_token(sanitized)

    active = pl.pl_load_active(root)
    # 같은 세션의 active 인지 — 다른 세션(동시 세션) 것이면 흡수·통계 오염 방지.
    # active 의 session_id 가 비었거나(구버전 레코드) stdin session_id 가 비면(식별 불가)
    # 같은 세션으로 간주 — session_finalize 의 폴백과 대칭.
    same_session = active is not None and (
        not session_id or active.get("session_id", "") in ("", session_id)
    )

    # 토큰 입력은 직전 active record에만 추가하고 새 prompt 시작 X
    if token_value is not None and active is not None:
        if same_session:
            def _add_token(a):  # 락 안에서 재적용 (RMW race 방지)
                a.setdefault("user_tokens_during", []).append(
                    {"token": token_value, "ts": pl.pl_now_iso()}
                )
            pl.pl_update_active(root, _add_token)
        return 0  # 다른 세션의 active 면 오염 방지를 위해 조용히 무시

    # 기존 active가 있으면 finalize 후 flush — 다른 세션 것이면 경계를 표시해 flush
    if active is not None:
        ended_by = "next_prompt" if same_session else "superseded_by_other_session"
        record = pl.pl_finalize_record(active, root, ended_by)
        warn = pl.pl_flush_record(record)  # 실패 시 dead-letter 보존
        if warn:
            sys.stderr.write(warn + "\n")
        pl.pl_clear_active(root)

    # 새 active 시작 (토큰 여부는 pl_make_active_record 가 pl_normalize_token 으로 판정)
    new_active = pl.pl_make_active_record(root, prompt_text, session_id)
    pl.pl_save_active(root, new_active)
    return 0


if __name__ == "__main__":
    sys.exit(main())
