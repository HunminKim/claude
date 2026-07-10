#!/usr/bin/env python3
"""SessionStart hook — 세션 작업 환경 환기 (타 세션 활동 + worktree 위치).

출력 채널: 환기 (exit 0 + stdout hookSpecificOutput.additionalContext JSON)

환기 1 — 같은 경로 타 세션 활동:
같은 프로젝트 디렉토리에서 병렬 세션이 편집하면 tasks/todo.md·plan-gate 상태를
공유해 충돌한다(TOCTOU 오탐·카운터 오염·전이 충돌). 세션 진입 시 다른 세션의
최근 활동이 보이면 "확인해보세요" 환기만 한다 — 판정·이동은 사용자 몫이다.

판정 2단 (오탐 억제):
  1. 활동: 트랜스크립트 디렉토리의 타 세션 *.jsonl 중 최근 갱신된 것이 있나
     (mtime — 세션은 턴마다 자기 jsonl 을 갱신한다. agent-* 사이드체인은 제외)
  2. 생존: 같은 cwd 의 다른 claude 프로세스가 실제로 살아 있나 (/proc 스캔 —
     방금 닫힌 세션의 신선한 잔재 jsonl 오탐을 걸러낸다. /proc 없는 OS 는
     판정 불가 → fail-open 으로 환기 유지, 환기 채널이라 오탐 무해)

compact/resume 은 대화가 이어지는 중 — 같은 사실을 반복 환기하는 노이즈라 억제.
프로세스 수만으로 판정하지 않는 근거: resume 후 방치된 탭이 프로세스로는 잡혀
"사용 중 2개 vs 프로세스 3개" 불일치가 실측됨 — 활동(mtime)이 사용자 체감과 일치.

환기 2 — worktree 작업 위치 앵커:
링크된 git worktree(격리 사본)에서 작업 중이면 위치·브랜치·본체를 환기한다.
타 세션 환기와 달리 compact 후에도 재주입한다 — 요약에서 위치 사실이 유실되면
Claude 가 본체 체크아웃으로 착각할 수 있다(템플릿 CLAUDE.md compact 언어앵커와
같은 원리). 단 "사용자에게 알리라" 지시는 세션 진입 시에만 붙인다(반복 공지 방지).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# 이 시간 안에 갱신된 타 세션 트랜스크립트 = "최근 활동" (탭 전환 사용 패턴 커버)
ACTIVE_WINDOW_SECS = 600

# 테스트 주입 지점 — 가짜 /proc 트리로 프로세스 생존 판정을 행위 검증한다
_PROC_ROOT = Path(os.environ.get("SESSION_CHECK_PROC_ROOT", "/proc"))


def _recent_other_transcripts(transcript_path: str) -> list[float]:
    """타 세션 jsonl 중 최근 갱신된 것들의 경과초 목록 (자기 자신·agent-* 제외)."""
    own = Path(transcript_path)
    now = time.time()
    ages = []
    try:
        siblings = list(own.parent.glob("*.jsonl"))
    except OSError:
        return []
    for f in siblings:
        if f.name == own.name or f.name.startswith("agent-"):
            continue
        try:
            age = now - f.stat().st_mtime
        except OSError:
            continue
        if 0 <= age <= ACTIVE_WINDOW_SECS:
            ages.append(age)
    return ages


def _ancestor_pids() -> set[int]:
    """자기 프로세스 조상 pid 집합 — 자기 세션의 claude 를 타 세션으로 오인 방지."""
    ancestors: set[int] = set()
    pid = os.getpid()
    for _ in range(64):
        ancestors.add(pid)
        try:
            status = (_PROC_ROOT / str(pid) / "status").read_text(
                encoding="utf-8", errors="ignore"
            )
        except OSError:
            break
        ppid = 0
        for line in status.splitlines():
            if line.startswith("PPid:"):
                ppid = int(line.split()[1])
                break
        if ppid <= 1:
            break
        pid = ppid
    return ancestors


def _other_claude_alive(own_cwd: str) -> bool | None:
    """같은 cwd 의 다른 claude 프로세스 존재 여부. /proc 미지원 환경은 None (판정 불가)."""
    if not _PROC_ROOT.is_dir():
        return None
    own_real = os.path.realpath(own_cwd)
    ancestors = _ancestor_pids()
    try:
        entries = list(_PROC_ROOT.iterdir())
    except OSError:
        return None
    for p in entries:
        if not p.name.isdigit() or int(p.name) in ancestors:
            continue
        try:
            comm = (p / "comm").read_text(encoding="utf-8", errors="ignore").strip()
            if comm != "claude":
                continue
            # 좀비는 cwd readlink 가 OSError → 자동 제외 (부재 취급)
            if os.path.realpath(os.readlink(str(p / "cwd"))) == own_real:
                return True
        except OSError:
            continue
    return False


def _worktree_notice(cwd: str, source: str | None) -> str | None:
    """링크된 worktree 에서 작업 중이면 환기 문구, 본체·비 git 이면 None.

    판별: --git-dir 과 --git-common-dir 이 다르면 링크된 worktree (본체는 동일).
    """
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--git-dir", "--git-common-dir"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = r.stdout.splitlines()
    if r.returncode != 0 or len(lines) != 2:
        return None
    # 상대 경로(.git 등)로 나올 수 있어 cwd 기준 절대화 후 비교
    git_dir, common_dir = (os.path.realpath(os.path.join(cwd, x)) for x in lines)
    if git_dir == common_dir:
        return None  # 본체 체크아웃

    main_root = os.path.dirname(common_dir)  # <본체>/.git → <본체>
    try:
        b = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
        branch = b.stdout.strip() or "?"
    except (OSError, subprocess.SubprocessError):
        branch = "?"

    notice = (
        f"[worktree] 🌿 이 세션은 git worktree(격리 사본)에서 작업 중입니다 "
        f"(브랜치: {branch} · 본체: {main_root})."
    )
    if source not in ("compact", "resume"):
        notice += (
            "\n사용자에게 첫 응답에서 워크트리 작업 중임을 알리세요. "
            "작업 완료 시 본체에서 merge 가 필요하다는 점을 기억하세요."
        )
    return notice


def _concurrent_notice(data: dict, cwd: str) -> str | None:
    """타 세션 최근 활동이 감지되면 환기 문구, 아니면 None."""
    transcript_path = data.get("transcript_path") or ""
    if not transcript_path:
        return None

    ages = _recent_other_transcripts(transcript_path)
    if not ages:
        return None

    if _other_claude_alive(cwd) is False:
        return None  # 신선한 잔재 jsonl 만 있고 프로세스는 없음 — 방금 닫힌 세션

    minutes = int(min(ages) // 60)
    last_seen = f"{minutes}분 전" if minutes else "1분 이내"
    return (
        f"[session-check] 👀 같은 프로젝트 경로에서 다른 Claude 세션의 최근 활동이 "
        f"감지되었습니다 (활성 추정 {len(ages)}개 · 마지막 활동 {last_seen}).\n"
        "사용자에게 첫 응답에서 알리세요: 다른 Claude 세션이 이 경로에서 활동 중인 것 "
        "같습니다 — 의도한 병렬 작업인지 확인해보세요.\n"
        "병렬로 편집 작업을 할 계획이라면 git worktree 분리를 권장하세요 "
        "(같은 경로 병렬 편집은 tasks/todo.md·plan-gate 상태를 공유해 충돌합니다)."
    )


def _emit_context(text: str) -> None:
    result = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    source = data.get("source")
    cwd = data.get("cwd") or os.getcwd()
    messages = []

    # worktree 위치 앵커 — compact/resume 에도 재주입 (요약 유실 방지)
    wt = _worktree_notice(cwd, source)
    if wt:
        messages.append(wt)

    # 타 세션 감지는 세션 진입 시 1회만 — compact/resume 재환기는 노이즈
    if source not in ("compact", "resume"):
        cs = _concurrent_notice(data, cwd)
        if cs:
            messages.append(cs)

    if messages:
        _emit_context("\n\n".join(messages))
    return 0


if __name__ == "__main__":
    sys.exit(main())
