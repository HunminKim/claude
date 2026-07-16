# CLAUDE.md — claude 마켓플레이스 저장소

> HunminKim의 Claude Code 플러그인 마켓플레이스(project-init / harness-check /
> prompt-log)를 배포하는 저장소. 이 파일은 *마켓플레이스 자체* 운영용이다 —
> `plugins/project-init/skills/.../templates/CLAUDE.md` (생성될 프로젝트용
> placeholder)와 혼동 금지.
>
> **이 파일은 지도다** — 매 턴 지켜야 하는 불변식만 인라인으로 두고, 상세 근거·절차는
> `docs/` 로 뺐다. 훅을 만들거나 릴리스를 낼 때는 아래 포인터 문서를 반드시 편다.

## 응답 언어

이 저장소에서 작업할 때 사용자에게 보내는 모든 응답은 **한국어**로 작성한다.
(코드·식별자·로그 문자열 등 기술적으로 영어가 필요한 부분은 예외.)

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
docs/                        상세 문서 — HOOK_CONVENTIONS.md · RELEASE.md · MANUAL.md 등
install.sh                   마켓플레이스 + 플러그인 일괄 설치
```

## 훅 컨벤션 (요약 — 상세·근거는 `docs/HOOK_CONVENTIONS.md`)

훅을 새로 만들거나 입출력·매처를 건드리기 전에 **`docs/HOOK_CONVENTIONS.md` 를 편다.**
아래는 위반 시 회귀를 부르는 핵심 불변식 체크리스트다:

- shebang `#!/usr/bin/env python3` + 한국어 docstring (첫 줄에 `출력 채널:` 명시)
- `from __future__ import annotations` + 타입 힌트 · 표준 라이브러리만 · snake_case · Python 3.10+
- stdin 파싱 실패는 silent `exit 0` (훅이 흐름을 막지 않는다)
- **stdio UTF-8 고정** (import 직후 `reconfigure(encoding="utf-8", errors="replace")`) — smoke_test `[17]` 강제
- **파일 텍스트 I/O 는 `encoding="utf-8"` 명시** (읽기는 `errors="ignore"`, 쓰기는 errors 금지) — smoke_test `[37]` 강제
- 서브에이전트 matcher 는 `Agent|Task` (개명 툴을 옛 이름 단독 매치하면 훅이 조용히 죽는다)
- 호출: 루트 `"$CLAUDE_PROJECT_DIR"/.claude/hooks/…` · 플러그인 `${CLAUDE_PLUGIN_ROOT}/hooks/…`

### 출력 채널 (의도별 단일 선택 — 혼용 금지)

| 의도 | 채널 |
|------|------|
| **차단/강제** (편집 거부·위반 차단) | `exit 2` + stderr → Claude blocking error |
| **비차단 환기** (advisory·정보 주입) | `exit 0` + stdout `hookSpecificOutput.additionalContext` JSON |
| **권한 승격** (사용자 확인창 ask / 자동 승인 allow) | `exit 0` + stdout `hookSpecificOutput.permissionDecision` JSON |
| **사용자 터미널 전용** (Claude 영향 없음) | `exit 0` + stderr |

- **금지**: `exit 0 + plain stderr/stdout` 로 Claude 환기 메시지 출력 금지 — context 진입 안 됨. 반드시 `additionalContext` JSON 으로 감싼다.
- **Stop 훅 예외**: Stop 이벤트의 `additionalContext`/`decision:block` 은 대화를 **강제로 잇는다**(최대 8회). 반드시 `stop_hook_active == true` 면 JSON 없이 `exit 0` 으로 억제.

> 각 채널의 실제 훅 예시·사고 이력·Stop 훅 하드캡 근거는 `docs/HOOK_CONVENTIONS.md` 참조.

## 작업 의례 (Subtraction-First)

- 새 훅·기능 추가 전 기존 `plan_gate_lib`, `prompt_log_lib`에서 흡수 가능한지 검토
- 커밋 전 `git diff` 자체 검토 (특히 `plugins/*/hooks/*.json` 매처 변경)
- **커밋 전 `python3 tests/smoke_test.py` 실행 필수** — 훅은 조용히 실패하므로
  행위 검증 없이는 회귀를 알 수 없다 (Edit 카운터 dead 회귀가 실제 사고 사례)
- ruff 잔존 오류는 같은 PR 안에서 fix — 다음 PR로 미루지 않는다
- prompt-log 관련 코드는 `[prompt-log]` 마커로 `grep -rn` 검색 가능해야 함
- **Simplicity First 적용**: 하네스 코드(`hooks/`, `plugins/`)도 단순성 우선 — 새 훅 추가 전
  기존 훅 확장으로 해결 불가능한지 먼저 확인, 미사용 매개변수·헬퍼를 "나중을 위해" 추가 금지,
  "이 훅이 없으면 실제로 발생하는 문제가 있나?" 아니라면 추가하지 않는다
- **동작에 영향 주는 문서 변경은 행위로 검증**: 에이전트 스펙(`agents/*.md`)·스킬 지시문·`CLAUDE.md`/`rules`
  규칙처럼 Claude·서브에이전트 동작을 바꾸는 변경은 "문서라 검증 불필요"로 넘기지 않는다. 변경된 지시를
  따르는 서브에이전트를 Agent tool로 띄우거나 명령 경로를 직접 실행해 의도한 동작 변화를 확인한다.
  정적 읽기만으로 통과 판정 금지.
- **외과적 변경 원칙**: 인접 훅·설정 파일의 포맷·주석 변경 금지 · 오래된 미사용 훅은 언급만(삭제는 명시
  요청 시에만) · 자신이 추가한 새 코드의 미사용 부분은 즉시 제거
- **케이스가 아니라 클래스에 대응 (일반화 우선)**: 리포트·사용자가 짚은 특정 사례(그 버그·그 파일·그
  키워드)만 하드코딩해 막지 않는다. 그 사례가 속한 일반 클래스를 파악해 원칙으로 대처한다 — 다음 변종은
  목록에 없는 형태로 온다. 구체 사례는 규칙 본문이 아니라 괄호 예시로 강등하고, 규칙은 라이브러리·도구·
  케이스 무관하게 쓴다. (예: "dropna·leading-zero·merge 금지" ❌ → "데이터 변환은 형상 불변량을 실측으로
  대조" ✅). 단, 실제 사고 이력을 남길 땐 재발 방지용 구체 예시는 유지한다 — 일반화가 사례를 지우라는 뜻은 아니다

## 요청 유형 구분 (필독)

| 사용자 표현 | 의미 | 수정 대상 |
|-----------|------|---------|
| "하네스 고쳐줘" | 플러그인·스킬 코드 수정 | `plugins/*/hooks/*.py`, `hooks.json` → 버전 번프 → 커밋·태그·푸시 |
| "로컬 파일 수정해" | 현재 컨텍스트의 로컬 파일 수정 | `CLAUDE.md`, `~/.claude/settings.json` 등 저장소 외부 파일 |

**"하네스 고쳐줘"** 를 받으면 `~/.claude/settings.json` 등 로컬 설정에 절대 손대지 않는다.
반드시 플러그인 파일 → `plugin.json` 버전 번프 → `git push origin main --tags` 순서로 처리한다.

## 버전 · 커밋 · 릴리스 → `docs/RELEASE.md`

버전 번프·커밋 포맷·태그·푸시 절차는 **`docs/RELEASE.md` 를 따른다.** 핵심만:

- 버전은 **사용자가 명시 요청할 때만** 번프 (자동 금지). 번프 시 `plugin.json` + `marketplace.json` description 동기화.
- 번프된 릴리스는 **태그 필수** — `git push origin main --tags` (`--tags` 빠지면 마켓플레이스가 릴리스로 인식 못 함).
- 태그 네임스페이스: 무접두 `v*` = project-init · 그 외는 `<plugin>-vX.Y.Z` 접두.

## 주의

- `.verifier_result.json`은 verifier 임시 파일 — 커밋 금지(.gitignore). 훅이 처리 후 자동 삭제
- `plugins/*/skills/*/assets/templates/` 하위는 placeholder 코드 — ruff 대상 외
- 마켓플레이스 메타(`marketplace.json`, 각 `plugin.json`) 변경 시 README.md 동기화
