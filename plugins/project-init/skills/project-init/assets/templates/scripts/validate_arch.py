#!/usr/bin/env python3
"""아키텍처 검증 스크립트 — .githooks/pre-push 에서 호출된다.
.claude/constraints.yaml 의 banned/arch_rules를 읽어 위반 여부를 검사한다.
PyYAML 없으면 검증을 스킵하고 성공으로 종료한다.

다언어 지원: banned 의존성을 언어별 import 문법(Python/JS·TS/Go/Rust)으로 검사한다.
해당 언어 소스가 없으면 그 패턴은 자연히 매치되지 않는다.
"""
from __future__ import annotations  # 3.8 등 구버전에서 모듈 레벨 제네릭 주석 즉사 방지

import subprocess
import sys
from pathlib import Path

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

PROJECT_ROOT = Path(__file__).parent.parent

# 언어별 (대상 확장자들, import 패턴 템플릿). {name} 에 banned 의존성명이 들어간다.
LANG_RULES: list[tuple[tuple[str, ...], list[str]]] = [
    (("*.py",), ["import {name}", "from {name} import", "from {name}."]),
    (
        ("*.js", "*.jsx", "*.ts", "*.tsx", "*.mjs", "*.cjs"),
        ["require('{name}')", 'require("{name}")', "from '{name}'", 'from "{name}"'],
    ),
    (("*.go",), ['"{name}"', '"{name}/']),
    (("*.rs",), ["use {name}", "extern crate {name}"]),
]

def load_constraints() -> dict:
    constraints_path = PROJECT_ROOT / ".claude" / "constraints.yaml"
    if not constraints_path.exists():
        print("[validate_arch] .claude/constraints.yaml 없음 — 검증 스킵")
        return {}
    try:
        import yaml
        with open(constraints_path, encoding="utf-8", errors="ignore") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        print("[validate_arch] PyYAML 미설치 — 검증 스킵")
        return {}
    except Exception as e:
        print(f"[validate_arch] constraints.yaml 파싱 오류: {e} — 검증 스킵")
        return {}

# 벤더/생성물 디렉토리 제외 — .venv 안의 `import requests` 가 매치되어 본인 코드는
# 깨끗한데 push 가 영구 차단되던 오탐 방지 (+대형 node_modules 스캔 성능).
_EXCLUDE_DIRS = (
    "scripts", ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    "site-packages", "dist", "build", ".tox", ".mypy_cache", ".ruff_cache", "vendor",
)


def _imports_dependency(name: str) -> bool:
    """name 의존성이 어떤 언어 소스에든 import/use 되면 True (언어별 문법 검사)."""
    exclude_args = []
    for d in _EXCLUDE_DIRS:
        exclude_args += [f"--exclude-dir={d}"]
    for includes, templates in LANG_RULES:
        include_args = []
        for inc in includes:
            include_args += ["--include", inc]
        for template in templates:
            pattern = template.format(name=name)
            # -F: 패턴을 리터럴로 — 의존성명에 정규식 특수문자가 있어도 오동작 없음
            result = subprocess.run(
                ["grep", "-rF", *include_args, *exclude_args, pattern, str(PROJECT_ROOT)],
                capture_output=True, text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                return True
    return False


def check_banned(banned: list) -> list:
    """금지된 의존성이 소스 코드에 import 되는지 grep으로 확인한다 (다언어)."""
    violations = []
    for entry in banned:
        name = entry.get("name") if isinstance(entry, dict) else str(entry)
        if not name:
            continue
        try:
            if _imports_dependency(name):
                reason = entry.get("reason", "") if isinstance(entry, dict) else ""
                violations.append(f"금지된 의존성 '{name}' 발견 — {reason}")
        except Exception:
            pass
    return violations

def main():
    constraints = load_constraints()
    if not constraints:
        sys.exit(0)

    violations = []
    banned = constraints.get("banned", [])
    if banned:
        violations.extend(check_banned(banned))

    if violations:
        print("[validate_arch] 아키텍처 위반 발견:")
        for v in violations:
            print(f"  - {v}")
        sys.exit(1)

    print("[validate_arch] 아키텍처 검증 통과")
    sys.exit(0)

if __name__ == "__main__":
    main()
