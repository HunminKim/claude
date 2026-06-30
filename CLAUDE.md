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
complit/                     하네스 수정·회고 리포트 누적
tests/                       smoke_test.py — 훅 행위 검증 (커밋 전 실행 필수)
install.sh                   마켓플레이스 + 플러그인 일괄 설치
```

## 훅 컨벤션 (모든 훅 작성 시 필수)

- shebang `#!/usr/bin/env python3` + 한국어 docstring (역할 + 동작 단계 + 출력 채널 명시)
- `from __future__ import annotations` + 타입 힌트 (`dict[str, Any]`, `Path | None`)
- 표준 라이브러리만 — 외부 의존 추가 시 install.sh 갱신
- f-string, Python 3.10+ (PEP 604/585)
- snake_case 파일명
- stdin: `{"tool_name", "tool_input", "session_id", ...}` JSON
- 모든 메시지는 한국어
- stdin 파싱 실패는 silent exit 0 (훅이 흐름을 막지 않는다)
- **stdio UTF-8 고정 필수** (import 직후): `for _s in (sys.stdin, sys.stdout, sys.stderr): try: _s.reconfigure(encoding="utf-8", errors="replace") except (AttributeError, ValueError): pass`.
  Windows cp949(한국어) 콘솔에서 이모지·em-dash(—) 입출력 시 UnicodeError 로 훅이 죽는다
  (PyYAML 미설치 `— 검증 스킵` 안내문까지 깨져 'pyyaml 에러'로 오인됨). `errors="replace"` 는
  stdin 으로 들어온 cp949/깨진 바이트의 디코드 크래시까지 막는다. smoke_test `[17]` 가 강제.
- **파일 텍스트 I/O 는 encoding 명시 필수**: `write_text`/`read_text`/`open()`(텍스트 모드)은
  항상 `encoding="utf-8"` 를 단다 — 미지정 시 Windows locale(cp949) 기본으로 동작해 비-ASCII
  파일쓰기가 UnicodeEncodeError 로 죽는다. POSIX 는 기본이 utf-8 이라 명시해도 no-op(회귀 0).
  **읽기는 `encoding="utf-8", errors="ignore"`** — 구버전이 cp949 로 쓴 기존 파일을 utf-8 strict
  로 읽다 죽는 마이그레이션 크래시를 피한다(쓰기엔 errors 금지 — 데이터 손상 은폐). stdio 가드와
  별개 사각(파일 I/O 는 reconfigure 가 못 덮음)이라 smoke_test `[37]` 가 AST 로 누락을 강제한다.
- 루트 settings.json: `python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/<name>.py`
- 플러그인 hooks.json: `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/<name>.py`
- 서브에이전트 matcher 는 `Agent|Task` (v2.1.63에서 Task→Agent 개명, 구버전 호환).
  matcher 의 letter-only 토큰은 tool_name **정확일치** — 죽은 토큰(MultiEdit 등)은
  무해하지만, 개명된 툴을 옛 이름 단독으로 매치하면 훅이 조용히 죽는다

### 출력 채널 (의도별 단일 선택 — 혼용 금지)

훅이 메시지를 출력할 때 의도에 맞는 채널을 선택한다. docstring 첫 줄에
`출력 채널: <차단|환기|사용자전용>` 을 명시한다. 같은 의도의 훅들이 서로 다른
채널로 갈라지는 것이 사고의 원인이었다 — 의도와 채널을 1:1 로 맞춘다.

- **차단/강제** (편집 거부·중단·위반 차단): `exit 2 + stderr`
  - Claude 에게 blocking error 로 주입 → 다음 턴에 행동 보정
  - 예: `plan_gate`, `dangerous_bash_check`, `detect_failure_loop`(2회 분기), `delegation_prompt_check`(블록 누락 분기 — PreToolUse)
- **비차단 환기** (Claude 행동 환기·정보 주입·advisory): `exit 0 + stdout` 으로 `hookSpecificOutput.additionalContext` JSON 출력
  - 차단 없이 Claude context 에 메시지 주입 → Claude 가 자기 응답에 반영
  - 예: `delegation_prompt_check` 통과 분기, `delegation_due_diligence`(UserPromptSubmit — exit 2 는 프롬프트를 지워 환기가 무효라 환기 채널 사용), `time_context`, `detect_bug_report`, `verifier_remind`, `plan_gate_session_start`(SessionStart)
  - ⚠️ **Stop 훅 예외 — additionalContext 는 비차단이 아니다**: Stop 이벤트에서
    `additionalContext`/`decision:block` 은 **대화를 강제로 잇는다**(공식 문서: decision:block 과
    동일한 "대화 계속" 효과, transcript 표기만 hook feedback). 매 종료마다 무조건 주입하면
    해소되지 않는 조건에서 stdin `stop_hook_active` 미체크 시 **최대 8회**(Claude Code 하드캡)까지
    턴이 연장된다 — 자리 비운 사용자에게 토큰·시간 낭비 + 의도치 않은 행동. 따라서 **Stop 훅은
    반드시 `stop_hook_active == true` 면 아무 JSON 없이 `exit 0` 으로 억제**하고, 조건 충족 시에만
    주입한다. (`plan_gate_stop_alert`·`cleanup_suggest` 가 이 가드를 가진 참조 구현.)
- **사용자 터미널 전용** (Claude 행동 영향 없음, 사용자 정보 알림): `exit 0 + stderr`
  - 사용자 터미널에만 보임 — Claude 는 못 봄
  - 예: `plan_gate_gc` (SessionEnd 시점 정보 — Claude 주입 불가 이벤트)
  - decision control 없는 이벤트 주의: `PostCompact`/`SessionEnd` 는 side-effect 용도로 분류돼 있어 환기 채널을 기대하지 않는다 — compact 후 재주입은 `SessionStart(matcher: compact)` 가 공식 권장 경로

**금지 패턴**: `exit 0 + plain stderr` 또는 `exit 0 + plain stdout` 으로 **Claude 환기 메시지**를 출력하지 않는다 — 사용자 터미널만 보이고 Claude context 진입 안 됨, 환기가 무효가 된다. Claude 가 봐야 하는 메시지는 반드시 `hookSpecificOutput.additionalContext` JSON 으로 감싼다.

참고: https://code.claude.com/docs/en/hooks.md (PreToolUse hookSpecificOutput 스펙)

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
- **커밋 전 `python3 tests/smoke_test.py` 실행 필수** — 훅은 조용히 실패하므로
  행위 검증 없이는 회귀를 알 수 없다 (Edit 카운터 dead 회귀가 실제 사고 사례)
- ruff 잔존 오류는 같은 PR 안에서 fix — 다음 PR로 미루지 않는다
- prompt-log 관련 코드는 `[prompt-log]` 마커로 `grep -rn` 검색 가능해야 함
- **Simplicity First 적용**: 하네스 코드(`hooks/`, `plugins/`)도 동일하게 단순성 우선
  - 새 훅 추가 전 기존 훅 확장으로 해결 불가능한지 먼저 확인
  - 미사용 매개변수·헬퍼 함수를 "나중을 위해" 추가 금지
  - "이 훅이 없으면 실제로 발생하는 문제가 있나?" — 아니라면 추가하지 않는다
- **동작에 영향 주는 문서 변경은 행위로 검증**: 에이전트 스펙(`agents/*.md`, `plugins/*/agents`)·스킬 지시문·`CLAUDE.md`/`rules` 규칙처럼 Claude·서브에이전트의 동작을 바꾸는 변경은 "문서라 검증 불필요"로 넘기지 않는다. 변경된 지시를 그대로 따르는 서브에이전트를 Agent tool로 띄우거나 해당 명령 경로를 직접 실행해, 의도한 동작 변화가 실제로 일어나는지 행위로 확인한다. 정적 읽기만으로 통과 판정 금지.
- **외과적 변경 원칙 적용**: 플러그인 수정 시에도
  - 인접 훅·설정 파일의 포맷·주석 변경 금지
  - 오래된 미사용 훅은 언급만 (삭제는 명시 요청 시에만)
  - 자신이 추가한 새 코드의 미사용 부분은 즉시 제거

## 플러그인 버전 관리 (필수)

버전은 자동으로 올리지 않는다. **사용자가 명시적으로 버전 번프를 요청할 때만** 올린다.
요청을 받으면 변경 내용(diff)을 보고 semver 등급을 판단해 **근거와 함께 제안**하고, 확정 후 올린다.
(과거 "변경할 때마다 무조건 번프" 규칙은 버전 세분화를 유발해 폐지했다.)

번프할 때는 해당 플러그인의 `plugin.json` 버전과 `.claude-plugin/marketplace.json` description 버전을 함께 동기화한다:

- `plugins/project-init/` 변경 → `plugins/project-init/.claude-plugin/plugin.json` + `.claude-plugin/marketplace.json` description
- `plugins/harness-check/` 변경 → `plugins/harness-check/.claude-plugin/plugin.json` + `.claude-plugin/marketplace.json` description
- `plugins/prompt-log/` 변경 → `plugins/prompt-log/.claude-plugin/plugin.json` + `.claude-plugin/marketplace.json` description
- 버전 형식: semver — 변경 규모로 판단:
  - `patch` (x.y.Z): 버그픽스, 오탈자, 메시지 수정 등 작은 수정
  - `minor` (x.Y.0): 기능 추가, 훅 개선, 새 파일 추가 등 하위 호환 변경
  - `major` (X.0.0): 하네스 구조 변경, 기존 동작 파괴적 변경

> 참고: 버전이 같으면 사용자 캐시가 갱신되지 않으므로, 사용자에게 실제 배포가 필요한 변경이면 번프를 함께 제안한다.

## 커밋 / PR

```
type(scope?): English title
- 한국어 변경 내용 / 변경 이유
```

type: `feat` `fix` `refactor` `docs` `chore`

## 릴리스 (태그 필수)

버전 번프를 동반한 변경을 푸시할 때는 그 버전의 SemVer 태그를 함께 단다.
**번프된 릴리스를 태그 없이 푸시하면 마켓플레이스가 정식 릴리스로 인식하지 못한다.**
(번프가 없는 일반 커밋은 태그를 달지 않는다. git 태그는 `plugin.json` 버전과 1:1로 유지한다.)

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
