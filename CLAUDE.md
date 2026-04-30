# CLAUDE.md — claude 마켓플레이스 저장소

> HunminKim의 Claude Code 플러그인 마켓플레이스(project-init / harness-check /
> prompt-log)를 배포하는 저장소. 이 파일은 *마켓플레이스 자체* 운영용이다 —
> `plugins/project-init/skills/.../templates/CLAUDE.md` (생성될 프로젝트용
> placeholder)와 혼동 금지.

## 디렉토리

```
.claude/                     이 저장소 전용 훅 (ruff_check 등)
.claude-plugin/              marketplace.json — 외부 노출 메타데이터
plugins/
  project-init/              하네스/검증/체크포인트 자동화
  harness-check/             진단 스킬
  prompt-log/                제거 가능 prompt 통계 (default deny)
install.sh                   마켓플레이스 + 플러그인 일괄 설치
```

## 훅 컨벤션 (모든 훅 작성 시 필수)

- shebang `#!/usr/bin/env python3` + 한국어 docstring (역할 + 동작 단계)
- `from __future__ import annotations` + 타입 힌트 (`dict[str, Any]`, `Path | None`)
- 표준 라이브러리만 — 외부 의존 추가 시 install.sh 갱신
- f-string, Python 3.10+ (PEP 604/585)
- snake_case 파일명
- stdin: `{"tool_name", "tool_input", "session_id", ...}` JSON
- stderr: 사용자 + Claude 둘 다 보는 채널. 한국어
- exit code: `0`=정상, `2`=경고/피드백(Claude 컨텍스트 주입), 그 외=오류
- stdin 파싱 실패는 silent exit 0 (훅이 흐름을 막지 않는다)
- 루트 settings.json: `python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/<name>.py`
- 플러그인 hooks.json: `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/<name>.py`

## 코드 품질 (PostToolUse 자동)

`.claude/hooks/ruff_check.py`가 Edit/Write/MultiEdit 직후 자동 실행:
1. `ruff format <file>` 정렬
2. `ruff check --fix <file>` 자동 수정
3. 잔존 위반은 stderr + exit 2 → Claude가 다음 턴에 수정

ruff 미설치 시 graceful skip(세션당 1회 안내). 설정은 `pyproject.toml [tool.ruff]`.
룰셋은 보수적 — `E/W/F/I/B`만. 새 룰 추가 전 기존 훅에 폭격 가는지 측정.

## 작업 의례 (Subtraction-First)

- 새 훅·기능 추가 전 기존 `plan_gate_lib`, `prompt_log_lib`에서 흡수 가능한지 검토
- 커밋 전 `git diff` 자체 검토 (특히 `plugins/*/hooks/*.json` 매처 변경)
- ruff 잔존 오류는 같은 PR 안에서 fix — 다음 PR로 미루지 않는다
- prompt-log 관련 코드는 `[prompt-log]` 마커로 `grep -rn` 검색 가능해야 함

## 커밋 / PR

```
type(scope?): English title
- 한국어 변경 내용 / 변경 이유
```

type: `feat` `fix` `refactor` `docs` `chore`
scope: `plan-gate`, `prompt-log`, `project-init` 등. 루트 영향 시 생략 가능.

## 주의

- `.verifier_result.json`은 verifier 임시 파일 — 커밋 금지(.gitignore). 훅이 처리 후 자동 삭제
- `plugins/*/skills/*/assets/templates/` 하위는 placeholder 코드 — ruff 대상 외
- 마켓플레이스 메타(`marketplace.json`, 각 `plugin.json`) 변경 시 README.md 동기화
