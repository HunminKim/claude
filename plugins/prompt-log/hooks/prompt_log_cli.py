#!/usr/bin/env python3
# [prompt-log] removable plugin — see plugins/prompt-log/README.md
"""prompt-log CLI — /prompt-log-status 등 슬래시 커맨드 백엔드."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import prompt_log_lib as lib


def cmd_status() -> int:
    root = lib.pl_find_project_root()
    if root is None:
        print("[prompt-log] 프로젝트 루트를 찾을 수 없습니다.")
        return 1

    consented = lib.pl_is_consented(root)
    consent_label = "✅ 동의됨" if consented else "❌ 동의 안 됨"

    # 글로벌 통계: 이번 달 + 전체 파일
    log_dir = lib.pl_home()
    jsonl_files = sorted(log_dir.glob("prompts-*.jsonl"))
    total_records = 0
    total_bytes = 0
    for f in jsonl_files:
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
            total_records += len([l for l in lines if l.strip()])
            total_bytes += f.stat().st_size
        except Exception:
            pass

    size_kb = total_bytes / 1024
    months = len(jsonl_files)

    print(
        f"[prompt-log status]\n"
        f"  이 프로젝트 동의 여부 = {consent_label}\n"
        f"  저장 위치             = {log_dir}\n"
        f"  월별 파일 수          = {months}개\n"
        f"  누적 레코드           = {total_records}건\n"
        f"  총 데이터 크기        = {size_kb:.1f} KB\n"
    )

    if not consented:
        print(
            "  → 이 프로젝트에서 수집을 활성화하려면:\n"
            "    plugins/prompt-log/README.md의 '수동 활성화' 섹션 참고"
        )
    return 0


COMMANDS = {"status": cmd_status}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"usage: prompt_log_cli.py {{{', '.join(COMMANDS)}}}", file=sys.stderr)
        sys.exit(1)
    sys.exit(COMMANDS[sys.argv[1]]())
