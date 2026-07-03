# 훅 작성 컨벤션 (상세)

> `CLAUDE.md`의 "훅 컨벤션" 체크리스트에 대한 **근거·코드 예시·사고 이력**.
> 훅을 새로 작성하거나 기존 훅의 입출력·매처를 건드리기 전에 이 문서를 읽는다.
> 체크리스트(요약)는 CLAUDE.md에, 여기엔 *왜 그렇게 해야 하는지*가 있다.

## 기본 규칙

- shebang `#!/usr/bin/env python3` + 한국어 docstring (역할 + 동작 단계 + 출력 채널 명시)
- `from __future__ import annotations` + 타입 힌트 (`dict[str, Any]`, `Path | None`)
- 표준 라이브러리만 — 외부 의존 추가 시 install.sh 갱신
- f-string, Python 3.10+ (PEP 604/585)
- snake_case 파일명
- stdin: `{"tool_name", "tool_input", "session_id", ...}` JSON
- 모든 메시지는 한국어
- stdin 파싱 실패는 silent exit 0 (훅이 흐름을 막지 않는다)
- 루트 settings.json 호출: `python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/<name>.py`
- 플러그인 hooks.json 호출: `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/<name>.py`

## stdio UTF-8 고정 필수 (smoke_test `[17]` 강제)

import 직후 다음을 넣는다:

```python
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
```

Windows cp949(한국어) 콘솔에서 이모지·em-dash(—) 입출력 시 UnicodeError 로 훅이 죽는다
(PyYAML 미설치 `— 검증 스킵` 안내문까지 깨져 'pyyaml 에러'로 오인됨). `errors="replace"` 는
stdin 으로 들어온 cp949/깨진 바이트의 디코드 크래시까지 막는다.

## 파일 텍스트 I/O 는 encoding 명시 필수 (smoke_test `[37]` 강제)

`write_text`/`read_text`/`open()`(텍스트 모드)은 항상 `encoding="utf-8"` 를 단다 — 미지정 시
Windows locale(cp949) 기본으로 동작해 비-ASCII 파일쓰기가 UnicodeEncodeError 로 죽는다.
POSIX 는 기본이 utf-8 이라 명시해도 no-op(회귀 0).

**읽기는 `encoding="utf-8", errors="ignore"`** — 구버전이 cp949 로 쓴 기존 파일을 utf-8 strict
로 읽다 죽는 마이그레이션 크래시를 피한다(쓰기엔 errors 금지 — 데이터 손상 은폐). stdio 가드와
별개 사각(파일 I/O 는 reconfigure 가 못 덮음)이라 smoke_test `[37]` 가 AST 로 누락을 강제한다.

## 서브에이전트 matcher

matcher 는 `Agent|Task` (v2.1.63에서 Task→Agent 개명, 구버전 호환).
matcher 의 letter-only 토큰은 tool_name **정확일치** — 죽은 토큰(MultiEdit 등)은
무해하지만, 개명된 툴을 옛 이름 단독으로 매치하면 훅이 조용히 죽는다.

## 출력 채널 (의도별 단일 선택 — 혼용 금지)

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
    주입한다. (참조 구현: 활성 훅 `plan_gate_stop_alert`, 그리고 템플릿 측 `skills/.../templates/.claude/hooks/cleanup_suggest.py` — 후자는 활성 hooks 디렉토리엔 없으니 grep 시 주의.)
- **권한 승격 (ask/allow)** (실행 자체를 사용자 확인창에 맡기거나 자동 승인): `exit 0 + stdout` 으로 `hookSpecificOutput.permissionDecision` JSON 출력
  - `ask` = 차단과 환기의 중간 — 정당한 사용자 경로까지 죽이지 않으면서 Claude 자율 실행엔 확인을 요구한다.
    예: `dangerous_bash_check` 의 plan-gate 자가전이 승격·workspace-guard(워크스페이스 밖 파괴 명령)
  - `allow` = PermissionRequest 이벤트에서 자동 승인. 예: `project_init_permission` (초기화 중 파일 생성, 프로젝트 스코프 신호 + TTL 로 남용 방지)
  - PreToolUse 에서 `ask` 는 반드시 exit 0 과 함께 — exit 2 를 섞으면 차단이 우선돼 확인창이 뜨지 않는다.
- **사용자 터미널 전용** (Claude 행동 영향 없음, 사용자 정보 알림): `exit 0 + stderr`
  - 사용자 터미널에만 보임 — Claude 는 못 봄
  - 예: `plan_gate_gc` (SessionEnd 시점 정보 — Claude 주입 불가 이벤트)
  - decision control 없는 이벤트 주의: `PostCompact`/`SessionEnd` 는 side-effect 용도로 분류돼 있어 환기 채널을 기대하지 않는다 — compact 후 재주입은 `SessionStart(matcher: compact)` 가 공식 권장 경로

**금지 패턴**: `exit 0 + plain stderr` 또는 `exit 0 + plain stdout` 으로 **Claude 환기 메시지**를 출력하지 않는다 — 사용자 터미널만 보이고 Claude context 진입 안 됨, 환기가 무효가 된다. Claude 가 봐야 하는 메시지는 반드시 `hookSpecificOutput.additionalContext` JSON 으로 감싼다.

참고: https://code.claude.com/docs/en/hooks.md (PreToolUse hookSpecificOutput 스펙)

## 코드 품질 (PostToolUse 자동)

`.claude/hooks/ruff_check.py`가 Edit/Write/MultiEdit 직후 자동 실행:
1. `ruff check --fix <file>` 자동 수정 (import 정렬 등). `ruff format`은 Surgical Changes 원칙에 따라 비활성화 — 인접 코드 재포맷 금지
2. 잔존 위반은 stderr + exit 2 → Claude가 다음 턴에 수정

ruff 미설치 시 graceful skip(세션당 1회 안내). 설정은 `pyproject.toml [tool.ruff]`.
룰셋은 보수적 — `E/W/F/I/B`만. 새 룰 추가 전 기존 훅에 폭격 가는지 측정.
