#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash) — 이미지 빌드 직전 의존성 lock 부재 감지.

출력 채널: 권한 승격 (exit 0 + stdout hookSpecificOutput.permissionDecision=ask JSON)

사고 배경 (daesung 2026-07-10):
  `uv.lock` 이 .gitignore 에 들어간 저장소를 clone → `docker build` 성공 → "lock 을
  만들 이유가 없어졌다"고 판정 → mlflow 가 최신 3.14.0 으로 설치돼 log_model 기본
  직렬화가 cloudpickle→skops 로 바뀜(서버 이미지는 v3.6.0 핀) → 학습 14건 전부 실패.
  Dockerfile 의 `COPY pyproject.toml uv.lock* ./` 에서 글로브 `*` 가 lock 부재를
  빌드 실패가 아니라 **조용한 재해상도**로 바꾼 것이 기술적 원인이다.

  → 빌드 성공은 의존성 정합성의 증거가 아니다. lock 부재는 빌드가 아니라 런타임에 터진다.

설계:
  - **이미지 빌드 명령에만** 발화한다. 30~60분을 태우기 직전이 비용 대비 가치가 최대인
    유일한 지점이고, 발화 지점을 좁혀야 노이즈가 없다. `uv sync` 같은 로컬 명령은 대상 아님.
  - 술어는 **결정론적**이다: 매니페스트는 git 에 추적되는데 대응 lock 이 추적되지 않으면 결함.
    파일 존재 검사가 아니라 `git ls-files` 를 본다 — lock 이 디스크엔 있고 gitignore 된
    daesung 케이스는 존재 검사로는 잡히지 않는다.
  - 차단(exit 2)이 아니라 `ask` 다. 라이브러리처럼 lock 을 의도적으로 커밋하지 않는
    정당한 경우가 있고, 그때 사용자가 넘길 수 있어야 한다. 영구 해제는
    `.claude/constraints.yaml` 의 `lock_policy: none`
    (v2.18.0 이전 프로젝트의 `docs/constraints.yaml` 도 폴백으로 읽는다).
  - 대조는 여기서 하지 않는다. 서버/클라이언트 경계 핀 대조는 verifier 의 infra 프로파일이
    `constraints.yaml` 의 `boundary_pins` 만 보고 수행한다 (전수 대조 금지).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# 이미지 빌드 명령 — 여기서만 발화한다 (재빌드 비용이 큰 지점)
_BUILD_RE = re.compile(
    r"\b(docker\s+(buildx\s+)?build|docker\s+compose\s+build|docker-compose\s+build|podman\s+build)\b",
    re.IGNORECASE,
)

# (매니페스트, lock 후보들) — lock 은 생태계별로 복수 허용
_MANIFEST_LOCKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pyproject.toml", ("uv.lock", "poetry.lock", "pdm.lock")),
    ("package.json", ("package-lock.json", "yarn.lock", "pnpm-lock.yaml")),
    ("Cargo.toml", ("Cargo.lock",)),
)

_LOCK_POLICY_RE = re.compile(r"^\s*lock_policy\s*:\s*([A-Za-z_]+)", re.MULTILINE)


def _git(root: Path, *args: str) -> str | None:
    """git 명령 stdout. 실패·비-git 이면 None (fail-open — 훅이 흐름을 막지 않는다)."""
    try:
        r = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _lock_policy(root: Path) -> str:
    """constraints.yaml 의 lock_policy. 파일·키 부재 시 'required'.

    `.claude/constraints.yaml` 우선, 구세대(v2.18.0 이전) 프로젝트의
    `docs/constraints.yaml` 폴백 — 먼저 존재하는 파일 하나만 읽는다.
    stdlib 만 쓰므로 yaml 파서 없이 단일 스칼라 키만 정규식으로 읽는다.
    """
    for path in (root / ".claude" / "constraints.yaml", root / "docs" / "constraints.yaml"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        m = _LOCK_POLICY_RE.search(text)
        return m.group(1).lower() if m else "required"
    return "required"


def _ignored_by(root: Path, rel: str) -> str | None:
    """rel 이 .gitignore 로 무시되면 '<파일>:<행>' 근거, 아니면 None."""
    out = _git(root, "check-ignore", "-v", rel)
    if not out:
        return None
    parts = out.strip().split("\t")[0].split(":")
    return f"{parts[0]}:{parts[1]}" if len(parts) >= 2 else None


def _findings(root: Path) -> list[str]:
    """추적된 매니페스트마다 대응 lock 이 추적되는지 검사. 결함 설명 목록을 돌려준다."""
    tracked = _git(root, "ls-files")
    if tracked is None:
        return []  # 비-git → 판단 불가, fail-open
    files = set(tracked.splitlines())

    out: list[str] = []
    for manifest, locks in _MANIFEST_LOCKS:
        for path in (p for p in files if p == manifest or p.endswith(f"/{manifest}")):
            parent = path[: -len(manifest)]  # 'backend/' 또는 ''
            if any(f"{parent}{lock}" in files for lock in locks):
                continue
            hint = ""
            for lock in locks:
                src = _ignored_by(root, f"{parent}{lock}")
                if src:
                    hint = f" ({lock} 이 {src} 로 무시됨)"
                    break
            out.append(f"{path} 는 추적되는데 lock({'/'.join(locks)})이 git 에 없습니다{hint}")
    return out


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    if data.get("tool_name") != "Bash":
        return 0
    command = (data.get("tool_input") or {}).get("command", "")
    if not command or not _BUILD_RE.search(command):
        return 0

    cwd = data.get("cwd")
    root = Path(cwd) if isinstance(cwd, str) and cwd else Path.cwd()
    if _lock_policy(root) == "none":
        return 0

    findings = _findings(root)
    if not findings:
        return 0

    detail = "\n".join(f"  - {f}" for f in findings)
    reason = (
        "[의존성] lock 파일 없이 이미지를 빌드하려 합니다.\n"
        f"{detail}\n"
        "  lock 이 없으면 빌드마다 의존성이 새로 해상도돼 서버와 클라이언트 버전이 어긋날 수 있고, "
        "그 실패는 빌드가 아니라 런타임에 드러납니다. 빌드 통과를 정합성의 증거로 삼지 마세요.\n"
        "  해법: lock 을 생성·커밋하고(`uv add`/`npm install` 이 자동 갱신) 빌드를 "
        "`uv sync --frozen`·`npm ci` 처럼 lock 강제 모드로 바꾸세요.\n"
        "  의도적으로 lock 을 커밋하지 않는 저장소면 .claude/constraints.yaml 에 `lock_policy: none` 을 넣으세요."
    )
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
