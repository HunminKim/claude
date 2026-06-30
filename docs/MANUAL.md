# HunminKim Claude Code 마켓플레이스 — 사용 설명서

> 이 문서는 저장소의 **세 플러그인(project-init / harness-check / prompt-log)** 과
> 핵심 기능인 **plan-gate** 의 동작·명령·상태 흐름을 코드 기준으로 정리한 사용 설명서다.
> (작성 시점 버전: marketplace `hunminkim`, project-init **v2.14.0**, harness-check **v1.0.1**, prompt-log **v1.1.4**)
>
> ⚠️ 이 문서는 *마켓플레이스를 쓰는 사용자*용 매뉴얼이다. 저장소 자체를 개발할 때의 규칙은 루트 `CLAUDE.md` 를 본다.
>
> 🚀 **처음이거나 "언제 뭘 입력하나"만 빠르게 보고 싶으면** → [QUICKSTART.md](QUICKSTART.md) (사용자 관점 짧은 가이드). 이 문서는 내부 동작까지 담은 상세 레퍼런스다.

---

## 목차

1. [한눈에 보기](#1-한눈에-보기)
2. [설치](#2-설치)
3. [project-init — 프로젝트 초기화](#3-project-init--프로젝트-초기화)
4. [plan-gate — 동작 핵심 (가장 중요)](#4-plan-gate--동작-핵심-가장-중요)
   - [4.1 개념과 목적](#41-개념과-목적)
   - [4.2 상태 기계](#42-상태-기계-state-machine)
   - [4.3 전형적 작업 흐름 (예시)](#43-전형적-작업-흐름-한-사이클-예시)
   - [4.4 명령 레퍼런스 (전체)](#44-명령-레퍼런스-전체)
   - [4.5 스코프 강제 (off / shadow / enforce)](#45-스코프-강제-off--shadow--enforce)
   - [4.6 매니페스트 문법 (scope / do-not-touch)](#46-매니페스트-문법-tasks todomd)
   - [4.7 체크포인트 & 롤백](#47-체크포인트--롤백)
   - [4.8 반복편집(thrash) 가드](#48-반복편집-thrash-가드)
   - [4.9 실패루프 가드](#49-실패루프-가드)
5. [훅 전체 지도 (언제 무엇이 뜨나)](#5-훅-전체-지도-언제-무엇이-뜨나)
6. [서브에이전트 (verifier / 도메인)](#6-서브에이전트)
7. [harness-check — 하네스 점검](#7-harness-check--하네스-점검)
8. [prompt-log — 프롬프트 로깅 (옵트인)](#8-prompt-log--프롬프트-로깅-옵트인)
9. [보조 스킬 (monitor / harness-update)](#9-보조-스킬)
10. [자주 쓰는 레시피](#10-자주-쓰는-레시피)
11. [문제 해결 (FAQ)](#11-문제-해결-faq)
12. [레퍼런스: 파일·상태 위치](#12-레퍼런스-파일상태-위치)

---

## 1. 한눈에 보기

이 마켓플레이스는 **Claude Code 에이전트의 행동을 "제어"하는 하네스(harness)** 다.
핵심 가치는 LLM 이 *계획 없이 코드를 쏟아내거나, 검증 없이 완료를 선언하거나, 같은 실수를 조용히 반복하는 것* 을 훅으로 막는 데 있다.

### 기본 플로우는 딱 3스텝

복잡한 명령표를 다 외울 필요 없다. 평소엔 이 흐름만 기억하면 된다:

```
/approve-plan   →   @verifier   →   /done
  계획 승인          독립 검증         완료·정리
```

1. `tasks/todo.md` 에 계획을 쓰고 **`/approve-plan`** — 구현 게이트가 열린다
2. 구현이 끝나면 **`@verifier`** — 독립 에이전트가 실제 실행으로 검증(✅/❌)
3. ✅ 면 **`/done`** — 끝. (❌ 면 `/retry` 로 같은 자리에서 다시)

> 나머지 명령(`/rollback`·`/skip`·`/replan`·스코프 강제·`/subplan`·백엔드 전환)은 **막혔을 때나 특수 상황에서만** 쓰는 고급 기능이다. 필요해지는 순간 훅이 어떤 명령을 쓰라고 직접 안내하므로, 미리 외우지 않아도 된다. 전체 목록은 [4.4 명령 레퍼런스](#44-명령-레퍼런스-전체) 참고.

| 플러그인 | 버전 | 한 줄 요약 |
|---------|------|----------|
| **project-init** | v2.14.0 | 본체. 프로젝트 스캐폴딩 + plan-gate + verifier + 18개 훅 + 18개 명령 |
| **harness-check** | v1.0.1 | 진단. 독립 서브에이전트가 하네스 건강 상태를 점검·리포트 |
| **prompt-log** | v1.1.4 | 옵트인 프롬프트 통계 수집 (기본 비활성 = default deny) |

세 플러그인은 독립적이다. project-init 만 써도 되고, prompt-log 는 명시 동의 전엔 아무것도 수집하지 않는다.

---

## 2. 설치

저장소 루트의 `install.sh` 가 마켓플레이스 등록 + 플러그인 일괄 설치를 한다.

```bash
bash install.sh
```

- **사전 요구**: Python **3.10+** (훅이 `from __future__ import annotations` + PEP 604/585 문법 사용 → 3.9 이하는 SyntaxError). install.sh 가 선검사하고 3.6 등에서는 중단한다.
- 설치 항목: 공식 플러그인 4종(code-review, code-simplifier, skill-creator, hookify) + 개인 3종(project-init, harness-check, prompt-log).
- 로컬 디렉토리 우선 → 포크/미러는 GitHub fallback.

설치 후 `claude plugin list` 로 확인된다.

---

## 3. project-init — 프로젝트 초기화

### 무엇을 하나

새 프로젝트에서 `/project-init` 를 호출하면 다음을 한 번에 세팅한다:

1. **기술스택 감지** (`package.json`, `pyproject.toml`, `go.mod` 등) 후 맞춤 스캐폴딩
2. **기존 문서 분류** (CLAUDE.md/docs 가 있으면 keep / transform / discard 로 나눠 사용자 승인)
3. **4계층 규칙 구조** 생성 (아래)
4. **6개 도메인 서브에이전트** + 6개 로컬 훅 + `.githooks/` 생성
5. **plan-gate 활성화** (`.claude/plan_gate_enabled` 를 *마지막에* 생성 — 초기화 도중 plan-gate 가 켜지지 않도록)

### 생성되는 4계층 규칙 (토큰 효율 설계)

200줄짜리 비대한 CLAUDE.md 를 피하려고 규칙을 4곳에 분산한다:

| 위치 | 역할 | 로드 시점 |
|------|------|----------|
| `CLAUDE.md` (≤100줄) | **프로젝트 고유 정보만** (빌드/테스트 명령, 비자명 제약, 알려진 버그) | 항상 |
| `.claude/memory/workflow.md` | 일반 절차 규칙 (TDD, phase-gate, 위임) | SessionStart @참조 |
| `.claude/rules/code-style.md` | 파일 타입별 조건부 코드 규칙 | 코드 편집 시 |
| `docs/constraints.yaml` | 의존성·아키텍처·파이프라인 SSOT (pre-push·verifier 공유) | 도구가 읽음 |

### 생성 디렉토리 구조

```
project-root/
├── CLAUDE.md                 프로젝트 규칙 (100줄 한도)
├── README.md                 사용 가이드
├── .gitignore                .env·*.pem·.claude/state/ 등
├── .plan-gateignore          편집 카운트에서 제외할 문서/메타 파일
├── .githooks/                pre-commit, pre-push, post-checkout
├── .claude/
│   ├── settings.json         훅 등록 SSOT
│   ├── rules/code-style.md
│   ├── memory/{workflow,lessons}.md
│   ├── hooks/                6개 로컬 훅
│   ├── agents/               verifier + backend/frontend/infra/deeplearning/llm-agent
│   └── state/                plan-gate 런타임 상태
├── docs/                     plan·decisions·constraints·checklist·technical_doc 등
├── tasks/todo.md             세션 작업 계획 (plan-gate 매니페스트 위치)
└── scripts/validate_arch.py  아키텍처 제약 검사
```

> `docs/` 는 누적/불변 지식, `tasks/todo.md` 는 세션 단위 작업 계획(/done 후 폐기)이다.

---

## 4. plan-gate — 동작 핵심 (가장 중요)

### 4.1 개념과 목적

plan-gate 는 **"계획 → 승인 → 구현 → 검증 → 완료"** 를 강제하는 게이트다.
해결하려는 문제:

- Claude 가 계획 없이 곧장 대량 편집 → **첫 편집 시 스냅샷을 떠두고**, **같은 파일을 5회 반복 편집하면 차단**
- 검증 없이 "다 됐습니다" → **@verifier 호출 전엔 완료 환기**, 검증 ❌ 면 새 편집 잠금
- 스코프를 벗어난 파일까지 건드림 → **enforce 모드에서 스코프 밖 편집 거부 + Bash 부작용 롤백**

활성/비활성은 `.claude/plan_gate_enabled` 파일 존재 여부로 결정된다 (`/plan-gate-on` / `/plan-gate-off`).

### 4.2 상태 기계 (State Machine)

게이트는 5개 상태를 가진다. 상태는 `.claude/state/plan_gate.json` 에 원자적으로 저장된다.

```
        첫 Edit (스냅샷 캡처)
   ┌──────────────────────────┐
   ▼                          │
┌─────────┐  /approve-plan  ┌──────────┐  @verifier 호출  ┌──────────┐
│ created │ ──────────────► │ approved │ ───────────────► │ verified │
└─────────┘                 └──────────┘                  └──────────┘
   │                          │  ▲  │                       │  ✅      │  ❌
   │ /done (빈손 종료 허용)    │  │  │ /replan(카운터 리셋)   │ /done    │ /retry → approved
   │                          │  │  └───────────────────────┘          │ /skip·/keep (보존 종료)
   ▼                          │  └──── /retry (검증 ❌ 후 재구현) ──────┘ /rollback → 체크포인트 복원
┌──────┐                      │
│ done │ ◄────────────────────┘  /skip-verify (검증 생략, ⏭️ 표시)
└──────┘
   ▲
   │ /rollback
┌──────────────┐
│ rolled_back  │
└──────────────┘
```

상태 의미:

| 상태 | 의미 | 들어가는 법 |
|------|------|------------|
| `created` | 게이트 열림, 승인 대기 (todo.md 작성 단계) | 첫 Edit 또는 `/plan-gate-on` 후 편집 |
| `approved` | 승인됨, 구현 진행 중. 매니페스트(scope/do-not-touch) 로드됨 | `/approve-plan` |
| `verified` | @verifier 판정 받음 (✅ 또는 ❌) | @verifier 가 결과 기록 |
| `done` | 게이트 종료, 체크포인트 정리됨 | `/done`, `/skip`, `/skip-verify` |
| `rolled_back` | 체크포인트로 복원하고 종료 | `/rollback` |

### 4.3 전형적 작업 흐름 (한 사이클 예시)

```
1) (선택) /plan-gate-on            ← plan-gate 켜기 (project-init 후엔 이미 켜짐)

2) tasks/todo.md 에 계획 작성       ← 목표·영향 파일·스코프 매니페스트 기입
   첫 Edit 순간 → 게이트 created + 워킹트리 스냅샷 자동 캡처

3) /approve-plan                   ← created → approved, 매니페스트 로드
   (이때부터 edit_count_post_approval 카운트 시작)

4) 구현 (Claude 가 코드 작성)       ← 같은 파일 5회↑ 반복 → thrash 차단
   성공한 Bash(테스트 통과 등) → 반복 카운터 리셋

5) @verifier 호출                  ← 독립 검증 → docs/.verifier_result.json
   훅이 결과 처리 → 상태 verified (✅/❌)

6) 결과에 따라:
   ✅ → /done                      ← 체크포인트 정리, 게이트 종료
   ❌ → /retry (같은 체크포인트 재구현) 또는 /rollback (전체 복원)
       또는 /skip·/keep (변경 보존하고 종료)
```

> **핵심 타이밍**: 스냅샷은 *첫 편집* 또는 *`/approve-plan`(첫 편집 전 승인 시)* 에 캡처된다. 그래야 나중에 `/rollback` 이 복원할 기준점이 생긴다.

### 4.4 명령 레퍼런스 (전체)

슬래시 명령으로 호출한다. 일부는 평문 토큰(예: `done`, `approve`)으로도 인식된다(`plan_approval.py` fallback).

> **핵심 3개만 평소에 쓴다**: `/approve-plan`(승인) · `@verifier`(검증) · `/done`(완료).
> 아래 표의 나머지는 *막혔거나 되돌리거나 범위를 좁힐 때* 쓰는 고급 명령이며, 필요한 순간 훅이 직접 안내한다.

#### 승인·완료 흐름

| 명령 | 효과 | 언제 |
|------|------|------|
| `/approve-plan` (별칭 `/approve`) | created → approved, 매니페스트 로드 | todo.md 작성 후, 구현 시작 전 |
| `/done` | 체크포인트 정리, 게이트 종료 | 검증 ✅ 또는 사용자 판단 완료. created 상태에서도 빈손 종료 허용 |
| `/replan` | 편집 카운터 리셋, **체크포인트 유지** | 계획을 다시 짤 때 (같은 게이트 ID) |
| `/retry` | approved 로 복귀, **체크포인트·스코프·카운터 유지** | 검증 ❌ 후 같은 기준점에서 재구현 |
| `/skip` (별칭 `/keep`) | 현재 변경 **보존**하고 종료 | 검증 ❌지만 변경을 남기고 싶을 때 |
| `/skip-verify` | 검증 생략하고 종료 (⏭️ 표시) | 판정 *전* 검증을 건너뛸 때 (판정 후엔 불가) |
| `/rollback` | 체크포인트로 **전체 복원** 후 종료 | 변경을 통째로 되돌릴 때 |

#### plan-gate 켜기/끄기·상태

| 명령 | 효과 |
|------|------|
| `/plan-gate-on` | `.claude/plan_gate_enabled` 생성 (활성화) |
| `/plan-gate-off` | 위 파일 삭제 (비활성화) |
| `/status` | 현재 게이트 id·상태·편집 횟수·체크포인트 출력 |

#### 체크포인트 백엔드 전환

| 명령 | 효과 |
|------|------|
| `/plan-gate-no-git` | 체크포인트를 git(private ref) 대신 **cp 파일 스냅샷**으로. `.claude/plan_gate_no_git` 생성 |
| `/plan-gate-use-git` | git 백엔드로 복귀. 위 파일 삭제 |

> git 저장소면 자동으로 git 백엔드(private ref + touched manifest)를 쓴다. 비-git 프로젝트는 자동으로 cp 스냅샷.

#### 스코프 강제 (선택 기능)

| 명령 | 모드 | 효과 |
|------|------|------|
| `/plan-gate-scope-shadow` | shadow **(기본)** | 위반 **감지·기록만** (차단·롤백 없음) |
| `/plan-gate-scope-enforce` | enforce | 스코프 밖 Edit **거부** + Bash 부작용 **롤백** |
| `/plan-gate-scope-off` | off | 강제 완전 끄기 (매니페스트 기록만, 환기도 없음) |
| `/subplan <패턴> [패턴...]` | — | 승인된 스코프를 **확장** (audit 남김). do-not-touch 는 확장 불가, broad-glob(`**`,`*`) 거부 |

### 4.5 스코프 강제 (off / shadow / enforce)

모드는 `.claude/plan_gate_scope` 파일 내용(off/shadow/enforce)으로 표현된다. **부재 시 shadow(기본)** — 스코프를 선언했으면 위반을 기본적으로 환기한다(차단·삭제는 enforce 에서만). 매니페스트가 없으면 모드와 무관하게 no-op 이므로, 이 기본값은 *스코프를 선언한 프로젝트*에서만 효력이 생긴다. 환기조차 원치 않으면 `/plan-gate-scope-off` 로 명시 off 한다.

> **enforce 자동 복귀 (stale enforce 청소)**: enforce 모드는 프로젝트 단위로 영속해 게이트가 닫혀도 남는다. 한 작업을 위해 켠 enforce 가 무관한 다음 작업에서 신규 파일을 조용히 삭제·차단하는 사고를 막기 위해, **게이트가 닫히면(`/done`·`/skip`·`/skip-verify`·`/rollback`) enforce 는 자동으로 shadow 로 복귀**하고 그 사실을 환기한다. '파괴를 해제하는' 안전 방향이라 자동이며(opt-in 불필요), `off` 는 명시 선택이라 복귀시키지 않는다. 계속 강제하려면 다음 사이클에서 `/plan-gate-scope-enforce` 를 다시 입력한다.
>
> 반대로 **자동으로 enforce 를 켜는 규칙은 없다** — 파괴적 동작은 사용자 명시 opt-in 으로만 켜진다(자동 전환은 안전 방향으로만).

**2계층 강제:**

- **Layer-1 (PreToolUse, 편집 전)**: Edit/Write/NotebookEdit 직전에 deny-first 검사.
  - `enforce` → 스코프 밖 편집을 **차단**(exit 2)
  - `shadow` → 허용하되 경고 + audit 기록
  - **control-plane 은 항상 허용**: `tasks/todo.md`, `.claude/state/**`, `.claude/plan_gate_*`, `docs/.verifier_result.json`, `.plan-gateignore`
- **Layer-2 (PostToolUse Bash, 편집 후)**: Bash 실행 후 `git status` 로 실제 워킹트리 변경을 스윕.
  - 스코프 밖 **새** 파일(스냅샷에 없던) → 삭제(rm)
  - 스코프 밖 **기존** 파일(사용자가 손댄) → 경고만 (덮어쓰지 않음, 데이터 보호)
  - **스냅샷이 없으면 enforce → shadow 자동 강등** (백업 없는 삭제 금지)

> **do-not-touch** 는 스코프 허용보다 먼저 검사된다. subplan 확장으로도 뚫리지 않는다.

### 4.6 매니페스트 문법 (`tasks/todo.md`)

스코프와 보호 대상은 `tasks/todo.md` 안에 HTML 주석 짝 마커로 선언한다. **대소문자·문자열이 정확히 일치**해야 인식된다(기계 생성이 전제):

```markdown
<!-- plan-gate: scope BEGIN -->
src/auth/**
lib/helpers.js
<!-- plan-gate: scope END -->

<!-- plan-gate: do-not-touch BEGIN -->
.env*
secrets/**
<!-- plan-gate: do-not-touch END -->
```

**글롭 의미** (path-aware — `fnmatch` 와 다름):

| 패턴 | 의미 |
|------|------|
| `*` | 한 경로 컴포넌트 내 (슬래시 미포함). `src/*` = `src/a.js` O, `src/a/b.js` X |
| `**` | 0개 이상 컴포넌트 횡단. `src/**` = 서브트리 전체 |
| `**/x` | 임의 깊이의 `x`. `x`, `a/x`, `a/b/x` 모두 |
| `?` | 슬래시 아닌 한 글자 |

> 마커가 없거나 짝이 안 맞으면 **fail-open**(스코프 없음 = thrash 가드만 동작). 안전한 기본값이다.

### 4.7 체크포인트 & 롤백

| 백엔드 | 저장 방식 | 롤백 |
|--------|----------|------|
| **git** (기본, git 저장소) | private ref 에 워킹트리 스냅샷 + touched 매니페스트 | 스냅샷에서 존재 파일 복구·신규 삭제 (실패 시 reflog 안내) |
| **cp** (비-git 또는 `/plan-gate-no-git`) | `.claude/state/checkpoints/<gate>/` 에 파일 복사 | 복사본으로 복원 |

`/rollback` 동작:
- 스냅샷에 있던(=편집 전 존재한) 파일 → 원래 내용 복원
- 새로 생긴 파일 → 삭제
- 게이트와 무관한 파일 → 보존

`/done` 시 체크포인트는 정리(GC)된다. `plan_gate_gc.py`(SessionEnd)가 고아 ref·30일 지난 종료 게이트를 청소한다.

### 4.8 반복편집 (thrash) 가드

같은 코드 파일을 **5회**(`TRIGGER_REPEAT_RATIO=5`) 이상 반복 편집하면 차단된다 — "수렴 안 되는 헛돌이"를 멈추게 하는 장치.

- **green-bash 리셋**: 성공한 Bash(exit 0, 예: 테스트 통과) 직후 반복 카운터가 리셋된다. 정상적 반복(고치고-테스트-고치고)을 거짓 차단하지 않으려는 설계.
- 실패한 Bash 는 카운터를 유지한다.
- approved 상태에서도 thrash 가드는 동작한다.
- `.plan-gateignore` 에 등록된 문서/메타 파일은 카운트에서 제외된다.

### 4.9 실패루프 가드

연속 Bash 실패를 추적한다(`.claude/state/failure_log.json`).

- 1회 실패 → 부드러운 환기 (advisory)
- **2회 연속 실패 → 차단**(exit 2) + 멈추고 재검토하라는 경고
- 성공 / 30분 경과 / 작업 디렉토리 변경 시 리셋
- `interrupt`(사용자 중단)는 실패로 치지 않는다

#### 왜 "완화 모드"가 없나 (의도적 설계)

실패루프 가드는 일부러 풀 수 없게 만들었다 — refactor·exploration 같은 "완화 모드"를 두지 않은 건 누락이 아니라 **결정**이다.

> **연속 실패는 대개 에이전트가 사용자 의도와 다른 방향으로 버그를 자의적으로 해결하려 할 때 나온다.** 즉 가드가 막는 그 순간이 바로 *사용자가 개입해야 하는* 순간이다. 여기에 "실패를 더 봐주는" 완화 모드를 끼우면, 가드가 가장 값질 때 입을 다물게 되어 도로 그 사고를 연다. 그래서 이 가드는 임계(2회)를 낮게 두고, **완화 대신 "멈추고 사용자에게 돌아가기"** 를 기본값으로 고정했다.

같은 이유로 thrash 가드(같은 파일 5회)도 별도 완화 모드 없이 둔다 — 정상적 반복(고치고→테스트 통과→고치고)은 [green-bash 리셋](#48-반복편집-thrash-가드)이 이미 풀어주고, 그래도 막히면 `/replan` 한 번으로 풀 수 있는 escape 가 있다. 모드를 늘리는 대신 *기존 리셋 경로*로 오탐을 흡수하는 게 이 하네스의 방침이다.

> 이 가드는 한때 조용히 죽어 있었다(F-008). Bash 실패는 `PostToolUse` 가 아니라 `PostToolUseFailure` 이벤트로 와서 `exit_code` 키도 없었기 때문 — 두 이벤트를 모두 구독하고 멀티스키마로 분류하도록 고쳐졌다.

---

## 5. 훅 전체 지도 (언제 무엇이 뜨나)

project-init 의 18개 훅이 이벤트별로 묶여 있다(`detect_failure_loop` 는 PostToolUse·PostToolUseFailure 두 이벤트에 등록돼 표에선 2회 나타난다). **출력 채널**은 의도와 1:1:
`차단`=exit2+stderr, `환기`=additionalContext JSON(Claude 가 봄), `사용자전용`=stderr.

| 이벤트 | 매처 | 훅 | 역할 | 채널 |
|--------|------|----|------|------|
| **PreToolUse** | Bash | `dangerous_bash_check` | `rm -rf /`·비밀파일·인라인 토큰 등 차단 | 차단 |
| | Read\|Grep | `secret_read_guard` | `.env`·`*.pem`·`.ssh` 읽기 차단 (`.example` 허용) | 차단 |
| | Edit\|Write\|…Edit | `plan_gate` | Layer-1 스코프, thrash 가드, 첫편집 스냅샷 | 차단/환기 |
| | Agent\|Task | `delegation_prompt_check` | 도메인 에이전트 4블록 프롬프트 강제 | 차단/환기 |
| **PostToolUse** | Write | `update_docs` | verifier 결과 처리 → 문서 자동 갱신 | 환기 |
| | Write | `plan_summary_request` | todo.md 요약 주입 | 환기 |
| | Bash | `detect_failure_loop` | 연속 실패 추적 | 환기/차단 |
| | Bash | `plan_gate_bash` | Layer-2 스코프 스윕, green-bash 리셋 | 환기 |
| | Edit\|Write\|…Edit | `verifier_remind` | 승인 후 편집 누적 시 @verifier 환기 (mid-turn, 무료) | 환기 |
| **PostToolUseFailure** | Bash | `detect_failure_loop` | 실패 이벤트 포착 | 환기/차단 |
| **UserPromptSubmit** | * | `detect_bug_report` | 버그 키워드 → "고치기 전 생각" 체크리스트 | 환기 |
| | * | `detect_user_correction` | 한국어 교정 신호 → lessons.md 환기 | 환기 |
| | * | `plan_approval` | 평문 토큰(done/approve 등) → CLI fallback | — |
| | * | `detect_task_boundary` | 타임아웃 자동 done / replan vs done 선택 | 환기 |
| | * | `delegation_due_diligence` | 위임 시 5섹션 todo.md 가드 | 차단/환기 |
| **Stop** | * | `plan_gate_stop_alert` | @verifier 미호출 경고 + thrash 임박 환기 (backstop, turn 강제) | 환기 |
| **SessionStart** | * | `plan_gate_session_start` | 재진입 시 활성 게이트 상태 표시 | 환기 |
| **SessionEnd** | * | `plan_gate_gc` | 고아 체크포인트·오래된 게이트 청소 | 사용자전용 |
| **PermissionRequest** | * | `project_init_permission` | 초기화 중 파일 생성 자동 승인 | allow |

> `verifier_remind`(mid-turn, 비용 0)와 `plan_gate_stop_alert`(턴 종료, 연장 강제)는 **2단 방어**다 — 값싼 환기 → 비싼 최후 backstop.

---

## 6. 서브에이전트

project-init 이 `.claude/agents/` 에 생성하는 5개. `@이름` 으로 호출한다.

| 에이전트 | 모델 | 담당 | 금지 |
|---------|------|------|------|
| **verifier** | opus | 독립 검증·이슈 보고 (**수정 안 함**). 실제 동작 실행 검증 필수(전부 정적이면 fail) | 구현·수정 |
| **backend** | sonnet | API·DB·스키마·비즈니스 로직·인증 | 프론트·인프라 |
| **frontend** | sonnet | 컴포넌트·상태·스타일 (컴포넌트 300줄 한도) | 서버·DB·인프라 |
| **infra** | sonnet | Dockerfile·IaC·CI/CD·IAM. *컨테이너 정의* 담당 | 컨테이너 *안 코드*(=backend/frontend) |
| **deeplearning** | sonnet | 모델·학습루프·데이터 전처리·평가 (모델 *학습*) | 배포 인프라(GPU 프로비저닝 등)·LLM 호출/프롬프트(=llm-agent) |
| **llm-agent** | sonnet | 프롬프트·행동 명세·툴 스키마·오케스트레이션·RAG·LLM 호출/파싱·eval 하네스 (사전학습 모델 *호출*) | 앱 배선(API·DB·큐=backend)·모델 학습(=deeplearning) |

공통 규칙: 시작 시 `git rev-parse HEAD` 로 baseline 기록 → 자기 변경을 "원래 있던 것"으로 오귀속하지 않음. verifier 만 opus(판단 품질), 구현 에이전트는 sonnet(처리량).

### verifier 결과 스키마 (무엇을·어떻게 검증했나)

verifier 는 단순히 ✅/❌ 만 내지 않는다. 검증이 끝나면 `docs/.verifier_result.json` 에 **무엇을 어떻게 검증했는지**를 구조화해 기록하고, `update_docs.py` 훅이 이를 읽어 문서·plan-gate 상태에 반영한 뒤 파일을 삭제한다. 핵심 필드:

| 필드 | 의미 |
|------|------|
| `verdict` | 최종 판정 `✅`/`❌` |
| `test_items[]` | 검증 항목별 `{item, result, note, method}`. `method` = `static`(코드만 읽음) / `mocked` / `isolated_exec` / `production_exec` — **"어떻게" 검증했는지를 명시** |
| `evidence` | 실행 명령·출력 등 판정 근거 (사본) |
| `issues` | 발견된 문제 목록 |
| `side_effects` | 생성 경로·정리 상태·production 쓰기 여부 |
| `code_smells` | 동작엔 무관한 설계 냄새 (판정 영향 없음) |
| `critical_constraints` | 다음 세션에 영향 줄 제약 → `CLAUDE.md` 자동 반영 |

> **✅ 의 최소 조건 — 실행 grounding (v2.6.0 기계 강제)**: `✅` 는 `test_items` 중 **최소 1개가 실제 실행**(`mocked`/`isolated_exec`/`production_exec`)으로 입증돼야 한다. 전 항목이 `static`(코드만 읽음)인데 `✅` 면, `update_docs` 훅이 이를 **자동으로 `❌` 로 강등**하고 사유를 advisory 로 남긴다 — "읽기만 하고 통과시키기"를 막는다. 실행이 정말 불가능한 경우에만 예외이며, 이때는 `evidence` 에 `전 항목 실행 불가 — 사유` 를 명시해야 ✅ 가 유지된다.

---

## 7. harness-check — 하네스 점검

개발 중간에 하네스(훅·verifier·문서 자동화)가 정상인지 진단하는 스킬.

```
/harness-check               표준 점검
/harness-check <상황설명>     lessons.md 기반 추가 하네스 추천
```

- **독립 서브에이전트**(harness-inspector, sonnet)가 편향 없이 점검: verifier.md 존재, 훅 배선, checklist↔completion_report 정합, verifier 스키마, CLAUDE.md "알려진 버그" 섹션, 체크리스트 활용도.
- 판정: ✅ 정상 / ⚠️ 경고(계속 가능) / ❌ 중단(수정 필요).
- ⚠️·❌ 면 `docs/harness_fix_report.md` 에 **상류(template) 수정용** 리포트 생성.

---

## 8. prompt-log — 프롬프트 로깅 (옵트인)

> **기본은 수집 안 함(default deny).** 명시 동의 전엔 어떤 데이터도 기록되지 않는다.

### 무엇을·어디에

- 수집(동의 시): 프롬프트 텍스트(PII 마스킹됨), 툴 사용 횟수, 만진 파일 목록, plan-gate 메타, 전이 토큰, 세션 메타
- 저장: `~/.claude/prompt-log/prompts-YYYY-MM.jsonl` — **로컬 전용, 네트워크 전송 없음, 0600 권한**

### 동의 (2조건 AND — 둘 다 있어야 수집)

1. 프로젝트 마커 `.claude/prompt-log-consent` 존재
2. 글로벌 화이트리스트 `~/.claude/prompt-log/projects-allowed.json` 에 등록

하나라도 없으면 silent no-op. `/project-init` 4단계에서 동의(y) 시 둘 다 생성된다.

### PII 마스킹

API 키(sk-ant-, ghp_, AKIA), JWT, URL 자격증명, 이메일, 한국 주민번호·사업자번호·전화번호. 사용자 정의 규칙(`~/.claude/prompt-log/sanitize_rules.yaml`)도 가능.

### 명령·제거

- `/prompt-log-status` — 동의 여부 + 전체 레코드 수·용량
- 완전 제거: `plugins/prompt-log/uninstall.sh` (글로벌 데이터 + 모든 마커 삭제). 모든 코드는 `[prompt-log]` 마커로 grep 가능.
- ⚠️ V1 은 동의 철회 후 자동 삭제가 없다(수동 rm 필요, V2 예정).

---

## 9. 보조 스킬

### `/monitor` — 장시간 작업 모니터링

학습·배치 작업 진행을 자동 간격으로 표 보고.

```
/monitor 2h training     2시간, "training" 프리셋 (.claude/monitor_presets.yaml)
/monitor 30m             30분, 자동 감지
/monitor 90              90분 (숫자=분)
```

간격 = `max(60초, min(3600초, 총시간/10))`. 예: 2시간 → 12분 간격 10회.

### `/harness-update` — 하네스 최신화

기존 project-init 프로젝트를 최신 템플릿으로 무손실 업그레이드.

- 업데이트 중 plan-gate 자동 비활성화 → 끝나면 복원
- placeholder-aware diff (`{{...}}` 변수 무시, 재주입 방지)
- **절대 안 건드림**: `CLAUDE.md`, `.claude/memory/lessons.md`, `docs/`, `tasks/`, `.claude/state/`

---

## 10. 자주 쓰는 레시피

**A. 새 기능 한 사이클 (스코프 강제까지)**
```
/plan-gate-on
# tasks/todo.md 에 목표 + scope 매니페스트 작성
/plan-gate-scope-enforce       # 스코프 밖 편집 차단 원하면
/approve-plan
# 구현…
@verifier                      # 검증
/done                          # ✅ 면 완료
```

**B. 검증 실패 후 재시도**
```
@verifier → ❌
/retry                         # 같은 체크포인트에서 다시 (스코프·카운터 유지)
# 다시 구현…
@verifier → ✅
/done
```

**C. 통째로 되돌리기**
```
/rollback                      # 체크포인트로 전체 복원
```

**D. enforce 중 인접 파일이 추가로 필요할 때**
```
/subplan src/utils/**          # 전면 /replan 없이 스코프 확장 (audit 남음)
```

**E. 비-git 프로젝트**
```
/plan-gate-no-git              # cp 스냅샷 백엔드로 (자동 감지되지만 명시 가능)
```

---

## 11. 문제 해결 (FAQ)

**Q. 편집이 갑자기 차단됐다.**
- 같은 파일 5회 반복(thrash) → 멈추고 접근 재검토. 테스트 통과(green Bash)하면 카운터 리셋.
- enforce 모드 + 스코프 밖 → `/subplan` 으로 확장하거나 매니페스트 수정.
- 연속 Bash 2회 실패 → 실패루프 가드. 원인부터 파악.

**Q. @verifier 호출하라는 환기가 계속 뜬다.**
- 승인 후 코드 편집이 누적됐는데 검증 안 함. `@verifier` 호출하거나, 검증 불필요하면 `/skip-verify`(판정 전) / `/done`.

**Q. `/skip-verify` 가 안 먹는다.**
- 이미 @verifier 판정(verified)을 받은 후엔 사용 불가. `/done`·`/retry`·`/skip` 중 선택.

**Q. plan-gate 를 잠깐 끄고 싶다.**
- `/plan-gate-off` → 작업 → `/plan-gate-on`. (단 끈 동안 스냅샷·스코프 보호 없음.)

**Q. 스코프 매니페스트가 무시되는 것 같다.**
- 마커 문자열이 **정확히** `<!-- plan-gate: scope BEGIN -->` 인지(대소문자·공백) 확인. BEGIN/END 짝이 맞는지 확인. 안 맞으면 fail-open(스코프 없음)으로 떨어진다.

**Q. 비밀파일을 읽어야 하는데 차단된다.**
- 의도된 보호. `.env.example` 같은 예시 파일은 허용된다. 실제 비밀이 필요하면 환경변수 경로를 쓰도록 설계.

**Q. 롤백이 일부만 복원된 것 같다.**
- 롤백은 체크포인트 스냅샷 기준 touched 파일만 복구(존재 파일)·신규 파일 삭제한다(v1 의 tag/stash 백엔드는 데이터 유실로 폐기, 현재는 프라이빗 ref/cp 스냅샷). 스냅샷 이전 변경·추적 밖 파일은 대상이 아니다 — 더 되돌리려면 `git reflog` 로 직전 상태를 찾는다.

---

## 12. 레퍼런스: 파일·상태 위치

### 활성화/모드 플래그 (프로젝트 루트 기준)

| 파일 | 의미 |
|------|------|
| `.claude/plan_gate_enabled` | 존재 = plan-gate 활성 |
| `.claude/plan_gate_scope` | 내용 = `off`/`shadow`/`enforce` (부재 = shadow, 기본) |
| `.claude/plan_gate_no_git` | 존재 = cp 백엔드 (부재 = git 백엔드) |
| `.claude/prompt-log-consent` | 존재 = 이 프로젝트 prompt-log 동의 (+화이트리스트 필요) |
| `.plan-gateignore` | 편집 카운트 제외 패턴 |

### 런타임 상태

| 경로 | 내용 |
|------|------|
| `.claude/state/plan_gate.json` | 게이트 상태(id, state, edit_count, scope, do_not_touch, checkpoint …) |
| `.claude/state/failure_log.json` | 연속 Bash 실패 추적 |
| `.claude/state/checkpoints/<gate>/` | cp 백엔드 파일 스냅샷 |
| `docs/.verifier_result.json` | verifier 임시 결과 (훅이 처리 후 삭제) |
| `~/.claude/prompt-log/prompts-YYYY-MM.jsonl` | prompt-log 수집 데이터 (글로벌, 0600) |

### 주요 상수

| 상수 | 값 | 의미 |
|------|----|----|
| `TRIGGER_REPEAT_RATIO` | 5 | 같은 파일 반복편집 차단 임계 |
| `BOUNDARY_TIMEOUT_MINUTES` | 60 | 마지막 편집 후 자동 done 까지 |
| 실패루프 차단 | 2회 연속 | Bash 연속 실패 차단 임계 |
| 실패루프 리셋 | 30분 | 경과 시 카운터 리셋 |

### control-plane 항상-허용 (enforce 중에도 편집 가능)

`tasks/todo.md`, `.claude/state/**`, `.claude/plan_gate_*`, `docs/.verifier_result.json`, `.plan-gateignore`

---

*이 문서는 코드(plan_gate_lib.py·plan_gate_cli.py·hooks.json·commands/·SKILL.md) 기준으로 작성됨. 동작이 의심되면 해당 소스가 최종 권위다.*
