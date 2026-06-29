#!/usr/bin/env python3
"""PostToolUse hook (matcher: Bash) — Bash 실행 실패 누적 추적 → 연속 실패 루프 감지.

출력 채널:
- 1회 실패: 환기 (exit 0 + stdout hookSpecificOutput.additionalContext JSON)
- 2회 연속: 차단 (exit 2 + stderr — hook error 블록으로 주입)

1회 실패에서 Claude 가 환기 메시지를 받아 "같은 접근 재시도 전 원인 확인" 으로
행동을 보정하지 못하면 2회차 차단으로 진입한다.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

THRESHOLD = 2
MAX_ENTRIES = 10
EXPIRY_MINUTES = 30
ERROR_TAIL_LENGTH = 200


def load_log(log_path: Path) -> dict:
    if log_path.exists():
        try:
            return json.loads(log_path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            pass
    return {
        "consecutive_failures": 0,
        "last_reset": datetime.now(timezone.utc).isoformat(),
        "working_dir": "",
        "entries": [],
    }


def save_log(log_path: Path, log: dict) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def is_expired(log: dict) -> bool:
    try:
        last_reset = datetime.fromisoformat(log.get("last_reset", ""))
        if last_reset.tzinfo is None:  # 구버전(naive utcnow) 상태 파일 호환
            last_reset = last_reset.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - last_reset > timedelta(minutes=EXPIRY_MINUTES)
    except Exception:
        return True


def format_soft_hint(entry: dict) -> str:
    cmd = entry.get("command", "")[:60]
    return (
        f"\n[failure-loop] Bash 실패 1회 — 같은 접근 재시도 전 원인을 확인하세요.\n  명령: {cmd}\n"
    )


def format_warning(entries: list) -> str:
    divider = "━" * 57
    lines = [
        "",
        divider,
        f"[FAILURE LOOP DETECTED] Bash 실패 {len(entries)}회 연속",
        divider,
        "",
        "최근 실패 패턴:",
    ]
    for i, e in enumerate(entries, 1):
        ts = e.get("timestamp", "")[:16].replace("T", " ")
        cmd = e.get("command", "")[:60]
        err_lines = e.get("error_tail", "").strip().splitlines()
        err_last = err_lines[-1][:80] if err_lines else "(출력 없음)"
        lines.append(f"  {i}. {ts} | {cmd}")
        lines.append(f"     → {err_last}")
    lines += [
        "",
        "필수 행동:",
        "  1. 즉시 실행을 중단한다",
        "  2. 표면 에러가 아닌 입출력 형식·설계 전체를 재검증한다",
        "  3. 분석 결과를 사용자에게 보고하고 다음 단계를 확인한다",
        "",
        "패치 재시도 금지.",
        divider,
        "",
    ]
    return "\n".join(lines)


def _parse_exit_code(error: str | None) -> int:
    """top-level error 문자열("Exit code 1")에서 종료코드 추출. 못 찾으면 1."""
    if not error:
        return 1
    m = re.search(r"-?\d+", error)
    return int(m.group()) if m else 1


def classify_outcome(data: dict) -> tuple[str, int, str]:
    """런타임 스키마 차이를 흡수해 (outcome, exit_code, error_tail) 반환.

    outcome ∈ {success, failure, interrupt}. 이 Claude Code 런타임 실측(260619):
    - 성공: hook_event_name=PostToolUse, tool_response=dict(stdout/stderr/...), **exit_code 키 없음**
    - 실패: hook_event_name=PostToolUseFailure, tool_response=None, top-level error="Exit code N", is_interrupt
    구버전 호환: 성공·실패 모두 PostToolUse + tool_response.exit_code 인 런타임도 처리.
    멀티 신호(event명 / top-level error / exit_code)로 판정 → 런타임 버전에 무관하게 동작.
    과거 회귀: exit_code 단일 가정 + PostToolUse 단일 구독으로 실패를 영영 못 봐 가드가 죽었음(F-008).
    """
    if data.get("is_interrupt"):
        return "interrupt", 0, ""
    event = data.get("hook_event_name") or ""
    tr = data.get("tool_response")
    top_error = data.get("error")

    # 명시적 실패: 실패 이벤트 또는 top-level error 존재
    if event == "PostToolUseFailure" or top_error:
        code = _parse_exit_code(top_error)
        tail = top_error or ""
        if isinstance(tr, dict):  # 구버전: 실패에도 tool_response 채워짐
            code = tr.get("exit_code", code) or code
            tail = tr.get("stderr") or tr.get("stdout") or tr.get("output") or tail
        return "failure", code, tail

    # tool_response 에 exit_code 명시(구버전 PostToolUse 성공·실패 공용 경로)
    if isinstance(tr, dict) and "exit_code" in tr:
        code = tr.get("exit_code", 0)
        if code != 0:
            tail = tr.get("stderr") or tr.get("stdout") or tr.get("output") or ""
            return "failure", code, tail
        return "success", 0, ""

    # 그 외(신버전 성공: exit_code 없는 PostToolUse) → 성공
    return "success", 0, ""


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if data.get("tool_name") != "Bash":
        sys.exit(0)

    outcome, exit_code, error_tail = classify_outcome(data)
    # 사용자 중단(interrupt)은 실패 루프 신호가 아니다 — 카운터 불변
    if outcome == "interrupt":
        sys.exit(0)
    command = data.get("tool_input", {}).get("command", "")
    working_dir = os.getcwd()

    # 프로젝트 .claude/ 위치 탐색
    cwd = Path(working_dir)
    log_path = None
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".claude").exists():
            log_path = parent / ".claude" / "state" / "failure_log.json"
            break
    if log_path is None:
        log_path = cwd / ".claude" / "state" / "failure_log.json"

    log = load_log(log_path)

    # 만료 또는 working_dir 변경 시 리셋
    if is_expired(log) or (log.get("working_dir") and log["working_dir"] != working_dir):
        log["consecutive_failures"] = 0
        log["entries"] = []
        log["last_reset"] = datetime.now(timezone.utc).isoformat()

    log["working_dir"] = working_dir

    # 성공 시 리셋
    if outcome == "success":
        log["consecutive_failures"] = 0
        log["entries"] = []
        log["last_reset"] = datetime.now(timezone.utc).isoformat()
        save_log(log_path, log)
        sys.exit(0)

    # 실패 누적
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "command": command[:120],
        "exit_code": exit_code,
        "error_tail": error_tail[-ERROR_TAIL_LENGTH:],
    }
    log["entries"].append(entry)
    log["entries"] = log["entries"][-MAX_ENTRIES:]
    log["consecutive_failures"] += 1
    log["last_reset"] = datetime.now(timezone.utc).isoformat()
    save_log(log_path, log)

    # 1회 실패: 소프트 힌트 (advisory, 카운터 유지)
    # hookEventName 은 실제 발동 이벤트로 맞춘다 — 실패는 PostToolUseFailure 로 온다.
    # (실측 260619: PostToolUseFailure 의 additionalContext·exit2 둘 다 Claude 에 전달됨)
    if log["consecutive_failures"] == 1:
        payload = {
            "hookSpecificOutput": {
                "hookEventName": data.get("hook_event_name") or "PostToolUse",
                "additionalContext": format_soft_hint(log["entries"][-1]),
            }
        }
        sys.stdout.write(json.dumps(payload, ensure_ascii=False))
        sys.exit(0)

    # 임계값 도달: 하드 경고 + exit 2 (hook error 블록으로 주입)
    if log["consecutive_failures"] >= THRESHOLD:
        sys.stderr.write(format_warning(log["entries"]) + "\n")
        log["consecutive_failures"] = 0
        log["entries"] = []
        log["last_reset"] = datetime.now(timezone.utc).isoformat()
        save_log(log_path, log)
        sys.exit(2)


if __name__ == "__main__":
    main()
