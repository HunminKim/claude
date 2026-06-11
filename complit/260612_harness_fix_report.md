# 하네스 수정 레포트

- **프로젝트**: adas-workspace
- **점검일**: 2026-06-11 (KST)
- **판정**: ❌ (Critical)
- **점검 방식**: harness-check 스킬 — harness-inspector(opus) 독립 진단 + claude-code-guide·general-purpose(opus) 정밀 분석

---

## 발견된 문제 요약

| # | 문제 | 심각도 | 유형 |
|---|------|--------|------|
| 1 | 프로젝트 로컬 훅 3종이 Python 3.8에서 `TypeError`로 죽음 (`from __future__ import annotations` 누락 + PEP604 `X \| None` 사용) | ❌ | 템플릿 구조 |
| 2 | plan-gate 상태머신 갭 — `created` 상태로 승인 없이 작업 진행 가능, `/done`이 `created`에서 거부 | ⚠️ | 템플릿 구조 |

### 문제 #1 근본 원인
- 훅은 shell-form(`sh -c 'python3 ...'`)으로 실행 → **셔뱅 무시**, PATH상 `python3`(=우리 환경 `/usr/local/bin/python3` 3.8.0 shim)로 실행.
- `def f() -> Path | None:`(PEP604, 3.10+ 문법)은 `from __future__ import annotations`(PEP563, 3.7+) 없으면 함수 정의 시점에 평가 → 3.8에서 `TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'`.
- exit 1(non-blocking)이라 차단은 없지만 **기능이 조용히 누락**: Stop(cleanup 제안)·UserPromptSubmit(design-precheck)·SessionStart compact(**CLAUDE.md 핵심 재주입**) 무력화. 특히 post-compact 재주입 실패는 compact 후 하네스 규칙 소실로 이어져 위험.
- 워크스페이스 3개 파일 = 플러그인 캐시 템플릿과 **byte-identical** → upstream 템플릿 결함.

---

## Upstream 수정 대상 (claude_skills 레포지토리)

> 아래는 템플릿 구조 결함으로, claude_skills 레포(project-init 플러그인)에서 수정해야 한다.
> 정답 레퍼런스: 같은 템플릿 디렉토리의 `verifier_sandbox.py`(셔뱅 → docstring → `from __future__ import annotations` → import).

### [문제 #1] 템플릿 훅 3종에 `from __future__ import annotations` 누락

**영향 파일 (claude_skills 레포 기준, `plugins/project-init/skills/project-init/assets/templates/.claude/hooks/`)**
- `cleanup_suggest.py` — PEP604 2곳 (line 32 `-> Path | None`, line 58 `-> list[Path] | None`)
- `design-precheck.py` — PEP604 1곳 (line 26 `-> Path | None`)
- `post-compact.py` — PEP604 1곳 (line 43 `-> Path | None`)

**현재 코드 (각 파일 module docstring 닫힘 직후)**
```
cleanup_suggest.py:  12: """      13: import json, os, subprocess, sys, time
design-precheck.py:   7: """       8: import json, os, re, sys
post-compact.py:     16: """      17: import json, os, sys
```

**수정 방향 — 각 파일의 docstring 닫는 `"""` 바로 다음 줄(첫 import 앞)에 1줄 삽입**
```python
"""
from __future__ import annotations   # ← 추가
import ...
```

**수정 이유**: PEP604/제네릭 서브스크립트(`X | None`, `list[...]`)를 Python 3.7~3.9에서도 안전하게 쓰기 위함. 셔뱅 고정은 shell-form에서 무시되므로 해법이 아니고, future-import가 정답(플러그인 훅 19/19, 템플릿 훅 3/6이 이미 채택).

### [문제 #1 회귀 방지] CI/lint 가드 추가

**수정 방향**: project-init 빌드/CI에 가드 추가 —
```bash
# future-import 없이 PEP604/제네릭을 쓰는 훅을 탐지해 실패 처리
grep -L 'from __future__ import annotations' <hooks>/*.py \
  | xargs -r grep -lE '\| None|\| [A-Z]|list\[|dict\[|tuple\[' && echo "FAIL" 
# 또는 최저 지원 버전(예: 3.8) 인터프리터로 각 훅 import 스모크 테스트
```
**수정 이유**: 같은 디렉토리에서도 일부 파일만 누락된 "불일치"가 사고 원인. 기계적 게이트로 전수 강제.

### [문제 #2] plan-gate 상태머신 — `created`에서 `/done` 거부 갭

**영향 파일 (추정)**: `plugins/project-init/.../hooks/plan_gate_cli.py` (+ 상태 전이 로직)

**현재 동작**: 게이트가 `created`(승인 전) 상태일 때, 코드 편집이 5회 임계 미만이면 plan-gate가 발동하지 않아 **승인 없이 작업이 끝까지 진행**되고, 이후 `/done` 호출 시 `created`에서는 전이 불가로 거부됨(`현재 상태 'created'에서는 완료 불가`).

**수정 방향 (택1 제안)**:
- (a) `/done`이 `created`(체크포인트 없음) 상태일 때 **우아하게 no-op 종료**(정리할 것 없음 안내 + 게이트 닫기) 처리.
- (b) 또는 `created` 상태에서 코드 편집 시작 시 "승인 필요" 안내를 더 일찍 노출(임계 미달이어도 첫 코드 편집에서 1회 환기).

**수정 이유**: cp/문서 위주 작업은 plan-gate 임계를 안 건드려 승인 절차가 스킵되는데, 종료 시 `/done`이 막혀 사용자 혼란. 상태머신이 `created` 종결 경로를 가져야 함. (심각도 ⚠️ — 기능 차단은 아님)

---

## 이 프로젝트 내 즉시 조치 사항

> 템플릿 수정과 별개로, 이 프로젝트(/workspace)에서 당장 적용 (로컬 파일은 템플릿과 동일하므로 같은 1줄 수정).

- [ ] `/workspace/.claude/hooks/cleanup_suggest.py` — docstring 닫힘 직후 `from __future__ import annotations` 삽입
- [ ] `/workspace/.claude/hooks/design-precheck.py` — 동일
- [ ] `/workspace/.claude/hooks/post-compact.py` — 동일
- [ ] 수정 후 3개 훅을 `python3`(3.8)로 더미 stdin 실행해 exit 0 확인
- [ ] (참고) `git_hooks_setup.py`의 git config 실패는 `/workspace`가 git repo 아님이 원인 — 치명적 아님, 인지만
