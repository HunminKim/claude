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
- **Simplicity First 적용**: 하네스 코드(`hooks/`, `plugins/`)도 동일하게 단순성 우선
  - 새 훅 추가 전 기존 훅 확장으로 해결 불가능한지 먼저 확인
  - 미사용 매개변수·헬퍼 함수를 "나중을 위해" 추가 금지
  - "이 훅이 없으면 실제로 발생하는 문제가 있나?" — 아니라면 추가하지 않는다
- **외과적 변경 원칙 적용**: 플러그인 수정 시에도
  - 인접 훅·설정 파일의 포맷·주석 변경 금지
  - 오래된 미사용 훅은 언급만 (삭제는 명시 요청 시에만)
  - 자신이 추가한 새 코드의 미사용 부분은 즉시 제거

## 플러그인 버전 관리 (필수)

플러그인 파일을 변경할 때마다 해당 플러그인의 `plugin.json` 버전을 반드시 올린다.
버전이 같으면 사용자 캐시가 갱신되지 않아 변경사항이 적용되지 않는다.

- `plugins/project-init/` 변경 → `plugins/project-init/.claude-plugin/plugin.json` 버전 번프
- `plugins/harness-check/` 변경 → `plugins/harness-check/.claude-plugin/plugin.json` 버전 번프
- `plugins/prompt-log/` 변경 → `plugins/prompt-log/.claude-plugin/plugin.json` 버전 번프
- 버전 형식: semver — 변경 규모로 판단:
  - `patch` (x.y.Z): 버그픽스, 오탈자, 메시지 수정 등 작은 수정
  - `minor` (x.Y.0): 기능 추가, 훅 개선, 새 파일 추가 등 하위 호환 변경
  - `major` (X.0.0): 하네스 구조 변경, 기존 동작 파괴적 변경

## 커밋 / PR

```
type(scope?): English title
- 한국어 변경 내용 / 변경 이유
```

type: `feat` `fix` `refactor` `docs` `chore`

## 릴리스 (태그 필수)

플러그인 변경을 푸시할 때는 반드시 SemVer 태그를 함께 달아야 한다.
**태그 없이 푸시하면 마켓플레이스가 정식 릴리스로 인식하지 못한다.**

```bash
git tag -a vX.Y.Z -m "한 줄 요약"
git push origin main --tags
```

- `git push origin main` 만 하면 태그가 올라가지 않는다 → 반드시 `--tags` 포함
- 태그 버전은 변경된 플러그인의 `plugin.json` 버전과 일치시킨다
- Annotated 태그(`-a`)를 사용한다 (태거·날짜·메시지 보존)
scope: `plan-gate`, `prompt-log`, `project-init` 등. 루트 영향 시 생략 가능.

## 요청 유형 구분 (필독)

| 사용자 표현 | 의미 | 수정 대상 |
|-----------|------|---------|
| "하네스 고쳐줘" | 플러그인·스킬 코드 수정 | `plugins/*/hooks/*.py`, `hooks.json` → 버전 번프 → 커밋·태그·푸시 |
| "로컬 파일 수정해" | 현재 컨텍스트의 로컬 파일 수정 | `CLAUDE.md`, `~/.claude/settings.json` 등 저장소 외부 파일 |

**"하네스 고쳐줘"** 를 받으면 `~/.claude/settings.json` 등 로컬 설정에 절대 손대지 않는다.
반드시 플러그인 파일 → `plugin.json` 버전 번프 → `git push origin main --tags` 순서로 처리한다.

## 주의

- `.verifier_result.json`은 verifier 임시 파일 — 커밋 금지(.gitignore). 훅이 처리 후 자동 삭제
- `plugins/*/skills/*/assets/templates/` 하위는 placeholder 코드 — ruff 대상 외
- 마켓플레이스 메타(`marketplace.json`, 각 `plugin.json`) 변경 시 README.md 동기화
