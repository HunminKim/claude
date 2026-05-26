#!/usr/bin/env python3
"""SessionStart hook — .githooks 를 git hooksPath 로 (재)설정한다.

원격(web) 세션은 매번 새 컨테이너로 clone 되어 core.hooksPath 설정이 사라진다.
또한 .githooks/post-checkout 는 clone 시점에 hooksPath 가 아직 기본값이라
실행되지 못한다(닭-달걀). 따라서 세션 시작마다 멱등하게 hooksPath 를 설정해
pre-commit/pre-push 가 실제로 동작하도록 보장한다.

동작 단계:
1. stdin JSON 파싱 (실패해도 흐름을 막지 않는다)
2. 프로젝트 루트의 .githooks 디렉토리 확인 (없으면 종료)
3. git config core.hooksPath .githooks 설정 + 훅 실행 권한 부여
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def project_root() -> Path:
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env_root) if env_root else Path.cwd()


def main() -> None:
    try:
        json.load(sys.stdin)
    except Exception:
        pass

    root = project_root()
    hooks_dir = root / ".githooks"
    if not hooks_dir.is_dir():
        sys.exit(0)

    try:
        subprocess.run(
            ["git", "config", "core.hooksPath", ".githooks"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception as e:
        print(f"[session-start] git 훅 활성화 실패: {e}", file=sys.stderr)
        sys.exit(0)

    for hook in hooks_dir.iterdir():
        if hook.is_file():
            hook.chmod(0o755)

    print("[session-start] git 훅 활성화: core.hooksPath=.githooks")


if __name__ == "__main__":
    main()
