#!/usr/bin/env python3
"""
Bash 실행 실패를 누적 추적하여 연속 실패 루프를 감지한다.
1회 실패: stdout 소프트 힌트 (exit 0).
2회 연속: stderr 하드 경고 + exit 2 (hook error 블록으로 주입).
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

THRESHOLD = 2
MAX_ENTRIES = 10
EXPIRY_MINUTES = 30
ERROR_TAIL_LENGTH = 200


def load_log(log_path: Path) -> dict:
    if log_path.exists():
        try:
            return json.loads(log_path.read_text())
        except Exception:
            pass
    return {
        "consecutive_failures": 0,
        "last_reset": datetime.utcnow().isoformat(),
        "working_dir": "",
        "entries": [],
    }


def save_log(log_path: Path, log: dict) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2))


def is_expired(log: dict) -> bool:
    try:
        last_reset = datetime.fromisoformat(log.get("last_reset", ""))
        return datetime.utcnow() - last_reset > timedelta(minutes=EXPIRY_MINUTES)
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


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if data.get("tool_name") != "Bash":
        sys.exit(0)

    tool_response = data.get("tool_response", {})
    exit_code = tool_response.get("exit_code", 0)
    output = tool_response.get("output", "")
    command = data.get("tool_input", {}).get("command", "")
    working_dir = os.getcwd()

    # 프로젝트 .claude/ 위치 탐색
    cwd = Path(working_dir)
    log_path = None
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".claude").exists():
            log_path = parent / ".claude" / "failure_log.json"
            break
    if log_path is None:
        log_path = cwd / ".claude" / "failure_log.json"

    log = load_log(log_path)

    # 만료 또는 working_dir 변경 시 리셋
    if is_expired(log) or (log.get("working_dir") and log["working_dir"] != working_dir):
        log["consecutive_failures"] = 0
        log["entries"] = []
        log["last_reset"] = datetime.utcnow().isoformat()

    log["working_dir"] = working_dir

    # 성공 시 리셋
    if exit_code == 0:
        log["consecutive_failures"] = 0
        log["entries"] = []
        log["last_reset"] = datetime.utcnow().isoformat()
        save_log(log_path, log)
        sys.exit(0)

    # 실패 누적
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "command": command[:120],
        "exit_code": exit_code,
        "error_tail": output[-ERROR_TAIL_LENGTH:],
    }
    log["entries"].append(entry)
    log["entries"] = log["entries"][-MAX_ENTRIES:]
    log["consecutive_failures"] += 1
    log["last_reset"] = datetime.utcnow().isoformat()
    save_log(log_path, log)

    # 1회 실패: 소프트 힌트 (exit 0, 카운터 유지)
    if log["consecutive_failures"] == 1:
        print(format_soft_hint(log["entries"][-1]))
        sys.exit(0)

    # 임계값 도달: 하드 경고 + exit 2 (hook error 블록으로 주입)
    if log["consecutive_failures"] >= THRESHOLD:
        sys.stderr.write(format_warning(log["entries"]) + "\n")
        log["consecutive_failures"] = 0
        log["entries"] = []
        log["last_reset"] = datetime.utcnow().isoformat()
        save_log(log_path, log)
        sys.exit(2)


if __name__ == "__main__":
    main()
