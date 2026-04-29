#!/usr/bin/env python3
"""Stop hook — 임시 파일 패턴 감지 시 Claude에게 정리 제안.
docs/constraints.yaml 의 temp_patterns 기준으로 스캔.
임시 파일이 없으면 무음 종료.
"""
import json, os, sys
from pathlib import Path
from datetime import datetime

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "env", ".env", ".mypy_cache", ".pytest_cache", ".ruff_cache",
}

DEFAULT_PATTERNS = {
    "prefixes": ["tmp_", "scratch_", "debug_", "exp_"],
    "suffixes": ["_tmp", "_scratch", "_debug"],
    "dirs": ["tmp/", "scratch/", ".experiments/"],
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


def scan_temp_files(root: Path, patterns: dict) -> list[Path]:
    prefixes = patterns.get("prefixes", DEFAULT_PATTERNS["prefixes"])
    suffixes = patterns.get("suffixes", DEFAULT_PATTERNS["suffixes"])
    temp_dirs = [d.rstrip("/") for d in patterns.get("dirs", DEFAULT_PATTERNS["dirs"])]

    found = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
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
        "삭제할 파일을 지정하거나 '모두 삭제'라고 말씀해 주세요.",
        div, "",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
