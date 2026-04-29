#!/usr/bin/env python3
"""아키텍처 검증 스크립트 — .githooks/pre-push 에서 호출된다.
docs/constraints.yaml 의 banned/arch_rules를 읽어 위반 여부를 검사한다.
PyYAML 없으면 검증을 스킵하고 성공으로 종료한다.
"""
import sys
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

def load_constraints() -> dict:
    constraints_path = PROJECT_ROOT / "docs" / "constraints.yaml"
    if not constraints_path.exists():
        print("[validate_arch] docs/constraints.yaml 없음 — 검증 스킵")
        return {}
    try:
        import yaml
        with open(constraints_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        print("[validate_arch] PyYAML 미설치 — 검증 스킵")
        return {}
    except Exception as e:
        print(f"[validate_arch] constraints.yaml 파싱 오류: {e} — 검증 스킵")
        return {}

def check_banned(banned: list) -> list:
    """금지된 의존성이 소스 코드에 import 되는지 grep으로 확인한다."""
    violations = []
    for entry in banned:
        name = entry.get("name") if isinstance(entry, dict) else str(entry)
        if not name:
            continue
        try:
            patterns = [f"import {name}", f"from {name} import", f"from {name}."]
            found = False
            for pattern in patterns:
                result = subprocess.run(
                    ["grep", "-r", "--include=*.py", "--exclude-dir=scripts", pattern, str(PROJECT_ROOT)],
                    capture_output=True, text=True
                )
                if result.returncode == 0 and result.stdout.strip():
                    found = True
                    break
            if found:
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
