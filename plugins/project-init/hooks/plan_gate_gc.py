#!/usr/bin/env python3
"""SessionEnd hook — 오래된 plan-gate 체크포인트 GC.

출력 채널: 사용자전용 (exit 0 + stderr — SessionEnd 는 Claude 주입 불가 이벤트)

정책 (D7/v2): 현재 게이트가 아닌 모든 체크포인트(프라이빗 ref refs/plan-gate/* +
cp 디렉토리)를 정리한다 — 닫힌 게이트 ref 는 cleanup_checkpoint 가 이미 지우므로
남은 것은 세션 중도 종료로 생긴 고아이고, 현재 게이트가 아니면 되돌릴 수 없으니 안전.
state 파일의 done/rolled_back gate 기록은 닫힌 시각(closed_at, 없으면 created_at)
기준 30일 후 정리.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_gate_lib as lib  # noqa: E402

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def _parse_iso(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def gc_state(state: dict, cutoff: datetime) -> int:
    removed = 0
    gates = state.get("gates", {})
    keep = {}
    current_id = state.get("current_gate_id")
    for gid, g in gates.items():
        if gid == current_id:
            keep[gid] = g
            continue
        if g["state"] not in ("done", "rolled_back"):
            keep[gid] = g
            continue
        # 닫힌 시각 기준 만료 — 오래 살다 최근에 닫힌 gate 가 조기 GC 되지 않게
        ts = _parse_iso(g.get("closed_at") or g.get("created_at", ""))
        if ts is None or ts >= cutoff:
            keep[gid] = g
            continue
        removed += 1
    state["gates"] = keep
    return removed


def _gc_refs(root, current: str | None) -> int:
    """현재 게이트가 아닌 프라이빗 ref(refs/plan-gate/<gid>/checkpoint) 삭제."""
    if not lib.has_git(root):
        return 0
    r = lib._git(root, "for-each-ref", "--format=%(refname)", lib.PLAN_GATE_REF_PREFIX)
    if r.returncode != 0:
        return 0
    removed = 0
    for ref in r.stdout.splitlines():
        ref = ref.strip()
        parts = ref.split("/")  # refs/plan-gate/<gid>/checkpoint
        gid = parts[2] if len(parts) >= 4 else None
        if not gid or gid == current:
            continue
        if lib._git(root, "update-ref", "-d", ref).returncode == 0:
            removed += 1
    return removed


def _gc_cpdirs(root, current: str | None) -> int:
    """현재 게이트가 아닌 cp 디렉토리(.claude/state/checkpoints/<gid>) 삭제."""
    cpbase = root / ".claude" / "state" / "checkpoints"
    if not cpbase.is_dir():
        return 0
    removed = 0
    for d in cpbase.iterdir():
        if d.is_dir() and d.name != current:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    return removed


def gc_checkpoints(root, current: str | None) -> int:
    """현재 게이트가 아닌 체크포인트(프라이빗 ref + cp 디렉토리)를 정리.

    체크포인트는 현재 게이트의 /rollback 에만 필요하다. 비-현재 게이트의 ref/디렉토리는
    닫혔거나(이미 정리됨) 세션 중도 종료로 생긴 고아 — 되돌릴 수 없으므로 안전히 삭제.
    """
    return _gc_refs(root, current) + _gc_cpdirs(root, current)


def main() -> int:
    try:
        json.load(sys.stdin)  # input 폐기
    except Exception:
        pass

    root = lib.find_project_root()
    if root is None or not lib.is_plan_gate_enabled(root):
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=lib.GC_MAX_AGE_DAYS)
    state = lib.load_state(root)

    removed_state = gc_state(state, cutoff)
    if removed_state:
        lib.save_state(root, state)

    removed_ckpt = gc_checkpoints(root, state.get("current_gate_id"))

    if removed_state or removed_ckpt:
        sys.stderr.write(
            f"[plan-gate gc] 정리 완료: state={removed_state} checkpoints={removed_ckpt}\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
