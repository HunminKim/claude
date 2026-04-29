#!/usr/bin/env python3
"""SessionEnd hook — 오래된 plan-gate 체크포인트 GC.

정책 (D7): 30일 이상 경과한 .claude/gate/*/clean tag와 [plan-gate] stash를 삭제.
state 파일의 done/rolled_back gate 기록도 30일 후 정리.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_gate_lib as lib  # noqa: E402


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
        ts = _parse_iso(g.get("created_at", ""))
        if ts is None or ts >= cutoff:
            keep[gid] = g
            continue
        removed += 1
    state["gates"] = keep
    return removed


def gc_git_tags(root, cutoff: datetime) -> int:
    """`.claude/gate/*/clean` tag 중 30일 이상된 것 삭제."""
    if not lib.has_git(root):
        return 0
    r = lib._git(root, "tag", "--list", f"{lib.TAG_PREFIX}*/clean",
                 "--format=%(refname:short)|%(creatordate:iso-strict)")
    if r.returncode != 0:
        return 0
    removed = 0
    for line in r.stdout.splitlines():
        if "|" not in line:
            continue
        tag, created = line.split("|", 1)
        try:
            ts = datetime.fromisoformat(created.strip())
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if ts < cutoff:
            if lib.delete_tag(root, tag.strip()):
                removed += 1
    return removed


def gc_stashes(root, cutoff: datetime) -> int:
    """[plan-gate] 마커가 붙은 stash 중 30일 이상된 것 drop.
    stash 시간은 git stash list --date=iso로 확인.
    """
    if not lib.has_git(root):
        return 0
    r = lib._git(
        root, "stash", "list", "--date=iso",
        "--format=%gd|%ci|%s",
    )
    if r.returncode != 0:
        return 0
    drops: list[str] = []
    for line in r.stdout.splitlines():
        parts = line.split("|", 2)
        if len(parts) < 3:
            continue
        ref, ci, msg = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if lib.STASH_PREFIX.strip() not in msg:
            continue
        try:
            ts = datetime.fromisoformat(ci)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if ts < cutoff:
            drops.append(ref)
    # 뒤에서부터 drop (인덱스 안정성)
    removed = 0
    for ref in reversed(drops):
        if lib.stash_drop(root, ref):
            removed += 1
    return removed


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

    removed_tags = gc_git_tags(root, cutoff)
    removed_stashes = gc_stashes(root, cutoff)

    if removed_state or removed_tags or removed_stashes:
        sys.stdout.write(
            f"[plan-gate gc] 정리 완료: state={removed_state} "
            f"tags={removed_tags} stashes={removed_stashes}\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
