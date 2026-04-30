#!/usr/bin/env python3
# [prompt-log] removable plugin — see plugins/prompt-log/README.md
"""UserPromptSubmit 훅 — 새 prompt 도착 시 처리.

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
    is_token = sanitized in pl.PL_TOKEN_SET

    active = pl.pl_load_active(root)

    # 토큰 입력은 직전 active record에만 추가하고 새 prompt 시작 X
    if is_token and active is not None:
        tokens = active.setdefault("user_tokens_during", [])
        tokens.append({"token": sanitized, "ts": pl.pl_now_iso()})
        pl.pl_save_active(root, active)
        return 0

    # 기존 active가 있으면 finalize 후 flush
    if active is not None:
        ended_by = "next_prompt"
        record = pl.pl_finalize_record(active, root, ended_by)
        try:
            pl.pl_append_record(record)
        except Exception as e:
            sys.stderr.write(f"[prompt-log] flush 실패: {e}\n")
        pl.pl_clear_active(root)

    # 새 active 시작
    new_active = pl.pl_make_active_record(root, prompt_text, session_id)
    if is_token:
        # active 없는데 토큰이 들어온 경우 (드뭄): 별개 record로 기록 가능하도록 유지
        new_active["prompt"]["is_token"] = True
        new_active["prompt"]["token_value"] = sanitized
    pl.pl_save_active(root, new_active)
    return 0


if __name__ == "__main__":
    sys.exit(main())
