#!/usr/bin/env python3
"""Stop hook — 임시 파일 패턴 감지 시 Claude에게 정리 제안.

출력 채널: 환기 (exit 0 + stdout hookSpecificOutput.additionalContext JSON)

공식 스펙: Stop 훅의 additionalContext는 턴 끝에 주입되어 Claude가
다음 응답에서 반영한다. systemMessage는 사용자 터미널 전용이라
"Claude에게 정리 제안" 의도에 맞지 않는다 (채널 교정).

docs/constraints.yaml 의 temp_patterns 기준으로 스캔.
임시 파일이 없으면 무음 종료.
"""
import json, os, subprocess, sys, time
from pathlib import Path
from datetime import datetime

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "env", ".env", ".mypy_cache", ".pytest_cache", ".ruff_cache",
}

SCAN_BUDGET_SEC = 5.0

DEFAULT_PATTERNS = {
    "prefixes": ["tmp_", "scratch_", "debug_"],
    "suffixes": ["_tmp", "_scratch", "_debug"],
    "dirs": ["tmp/", "scratch/", ".experiments/"],
    "exclude_dirs": [],
}


def find_project_root() -> Path | None:
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root:
        p = Path(env_root)
        if (p / "docs" / "constraints.yaml").exists() or (p / "CLAUDE.md").exists():
            return p
        return None
    for p in [Path.cwd()] + list(Path.cwd().parents):
        if (p / "docs" / "constraints.yaml").exists():
            return p
    return None


def load_patterns(root: Path) -> dict:
    try:
        import yaml
        with open(root / "docs" / "constraints.yaml") as f:
            data = yaml.safe_load(f) or {}
        patterns = data.get("temp_patterns", {})
        if patterns:
            return patterns
    except Exception:
        pass
    return DEFAULT_PATTERNS


def _git_tracked_and_untracked(root: Path) -> list[Path] | None:
    """git 트래킹/언트래킹(.gitignore 적용) 파일 목록. git 미사용 시 None."""
    if not (root / ".git").exists():
        return None
    try:
        res = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard"],
            capture_output=True, text=True, timeout=3,
        )
        if res.returncode != 0:
            return None
        return [root / line for line in res.stdout.splitlines() if line]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def scan_temp_files(root: Path, patterns: dict) -> list[Path]:
    prefixes = patterns.get("prefixes", DEFAULT_PATTERNS["prefixes"])
    suffixes = patterns.get("suffixes", DEFAULT_PATTERNS["suffixes"])
    temp_dirs = [d.rstrip("/") for d in patterns.get("dirs", DEFAULT_PATTERNS["dirs"])]
    user_skip = {d.rstrip("/") for d in patterns.get("exclude_dirs", [])}
    skip = SKIP_DIRS | user_skip

    deadline = time.monotonic() + SCAN_BUDGET_SEC
    candidates = _git_tracked_and_untracked(root)

    found: list[Path] = []
    iterator = candidates if candidates is not None else root.rglob("*")
    for path in iterator:
        if time.monotonic() > deadline:
            return []
        if not path.is_file():
            continue
        if any(part in skip for part in path.relative_to(root).parts):
            continue
        rel = path.relative_to(root)
        name = path.name
        stem = path.stem
        str_rel = str(rel)

        if any(str_rel.startswith(d) for d in temp_dirs):
            found.append(path)
            continue
        if any(name.startswith(p) for p in prefixes):
            found.append(path)
            continue
        if any(stem.endswith(s) for s in suffixes):
            found.append(path)

    return sorted(found)


def fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1_048_576:
        return f"{n / 1024:.1f}KB"
    return f"{n / 1_048_576:.1f}MB"


def fmt_mtime(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%m/%d %H:%M")


def main():
    try:
        json.load(sys.stdin)
    except Exception:
        pass

    root = find_project_root()
    if root is None:
        sys.exit(0)

    patterns = load_patterns(root)
    files = scan_temp_files(root, patterns)
    if not files:
        sys.exit(0)

    div = "━" * 57
    lines = ["", div, f"[CLEANUP] 임시 파일 {len(files)}개 감지됨", div, ""]
    for f in files:
        st = f.stat()
        lines.append(f"  {f.relative_to(root)}  ({fmt_size(st.st_size)}, {fmt_mtime(st.st_mtime)})")
    lines += [
        "",
        "docs/constraints.yaml > temp_patterns 네이밍 규칙에 해당하는 파일입니다.",
        "→ Claude: 위 임시 파일 정리를 사용자에게 제안하라.",
        "  사용자가 삭제할 파일을 지정하거나 '모두 삭제'라고 답하면 삭제를 진행한다.",
        div, "",
    ]
    advisory = {
        "hookSpecificOutput": {
            "hookEventName": "Stop",
            "additionalContext": "\n".join(lines),
        }
    }
    sys.stdout.write(json.dumps(advisory, ensure_ascii=False))


if __name__ == "__main__":
    main()
