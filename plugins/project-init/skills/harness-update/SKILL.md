---
name: harness-update
description: 기존 project-init 하네스를 최신 버전으로 업데이트한다. 에이전트·훅·워크플로우 규칙을 갱신하면서 사용자 데이터(CLAUDE.md, lessons.md, docs/)는 보존한다. "/harness-update", "하네스 업데이트", "에이전트 업데이트", "훅 최신화", "verifier 업데이트", "하네스 최신 버전으로" 등의 요청에 이 스킬을 사용한다.
---

# Harness Update Skill

기존 project-init 하네스를 최신 템플릿으로 업데이트한다.
**사용자 데이터는 절대 건드리지 않는다.**

## 사전 조건 (실행 전 필독)

> **플러그인 업데이트 직후라면 Claude Code 세션을 재시작해야 한다.**
>
> Claude Code는 세션 시작 시점의 플러그인을 메모리에 로드한다.
> 플러그인을 업데이트해도 현재 세션은 이전 버전 템플릿을 기준으로 비교하므로
> 신규 파일·변경 훅이 감지되지 않는다.
>
> **올바른 순서**: 플러그인 업데이트 → Claude Code 재시작 → `/harness-update`

## 실행 순서

### 1단계: 사전 확인

현재 디렉토리가 project-init으로 초기화된 프로젝트인지 확인한다.

```
필수 확인:
- .claude/agents/verifier.md 존재 여부  → 없으면 "project-init 먼저 실행하세요" 안내 후 중단
- .claude/plan_gate_enabled 존재 여부   → 있으면 plan-gate 활성 → 자동 비활성화 후 진행
```

plan-gate가 활성 상태이면 **사용자 확인 없이 즉시 비활성화**한다:
```bash
rm .claude/plan_gate_enabled
```
그리고 사용자에게 알린다:
```
ℹ️  plan-gate를 일시 비활성화했습니다 (하네스 업데이트는 다수 파일을 수정하는 정상 관리 작업).
    업데이트 완료 후 자동으로 재활성화합니다.
```

> **이유**: 하네스 업데이트는 사용자가 명시적으로 요청한 관리 작업이다.
> plan-gate의 목적(계획 없는 ad-hoc 대량 편집 방지)과 충돌하지 않으므로
> 묻지 않고 비활성화하고 완료 후 복원한다.

### 2단계: 현재 상태 스캔

다음 파일들의 현재 존재 여부와 내용을 읽어 템플릿과 비교한다.

**템플릿 경로**: 이 스킬이 실행되는 플러그인 루트의 `skills/project-init/assets/templates/` 아래.
실제 경로는 `CLAUDE_PLUGIN_ROOT` 환경변수 또는 이 SKILL.md의 위치에서 추론한다.

#### 비교 대상 파일 목록

| 프로젝트 내 경로 | 템플릿 경로 | 처리 방식 |
|---------------|-----------|---------|
| `.claude/agents/verifier.md` | `agents/verifier.md` | diff 확인 후 업데이트 |
| `.claude/agents/frontend.md` | `agents/frontend.md` | diff 확인 후 업데이트 (없으면 신규 생성) |
| `.claude/agents/backend.md` | `agents/backend.md` | diff 확인 후 업데이트 (없으면 신규 생성) |
| `.claude/agents/deeplearning.md` | `agents/deeplearning.md` | diff 확인 후 업데이트 (없으면 신규 생성) |
| `.claude/agents/infra.md` | `agents/infra.md` | diff 확인 후 업데이트 (없으면 신규 생성) |
| `.claude/memory/workflow.md` | `.claude/memory/workflow.md` | 항상 업데이트 (읽기 전용 규칙 — 사용자 편집 불가) |
| `.claude/hooks/time_context.py` | `.claude/hooks/time_context.py` | diff 확인 후 업데이트 |
| `.claude/hooks/design-precheck.py` | `.claude/hooks/design-precheck.py` | diff 확인 후 업데이트 |
| `.claude/hooks/post-compact.py` | `.claude/hooks/post-compact.py` | diff 확인 후 업데이트 |
| `.claude/hooks/cleanup_suggest.py` | `.claude/hooks/cleanup_suggest.py` | diff 확인 후 업데이트 |
| `.claude/hooks/git_hooks_setup.py` | `.claude/hooks/git_hooks_setup.py` | diff 확인 후 업데이트 (없으면 신규 생성) |
| `.claude/hooks/verifier_sandbox.py` | `.claude/hooks/verifier_sandbox.py` | diff 확인 후 업데이트 (없으면 신규 생성) |
| `.plan-gateignore` | `.plan-gateignore` | 없으면 신규 생성 (있으면 보존 — 사용자 편집 파일) |

> **잔존물 정리**: 프로젝트에 `.claude/commands/skip.md` 가 있으면 **삭제를 권고**한다
> (구버전 스캐폴드 산물). v1.29.0부터 /done·/skip 등 전이 커맨드는 플러그인이 제공하며,
> 프로젝트 로컬 동명 커맨드는 플러그인 커맨드를 가려(shadow) 무력화할 수 있다.
| `.githooks/pre-commit` | `.githooks/pre-commit` | diff 확인 후 업데이트 |
| `.githooks/pre-push` | `.githooks/pre-push` | diff 확인 후 업데이트 |
| `.githooks/post-checkout` | `.githooks/post-checkout` | diff 확인 후 업데이트 |
| `scripts/validate_arch.py` | `scripts/validate_arch.py` | diff 확인 후 업데이트 |
| `.claude/settings.json` | `.claude/settings.json` | diff 확인 후 업데이트 |
| `.claude/rules/code-style.md` | `.claude/rules/code-style.md` | diff 확인 후 업데이트 |

> **처리 방식 설명**
> - **diff 확인 후 업데이트**: 현재 파일과 새 템플릿을 비교한다. 내용이 같으면 "(변경 없음)" 으로 스킵한다. 다르면 3단계 보고에서 "사용자 결정 필요" 로 분류한다.
> - **항상 업데이트**: 사용자 커스텀이 불가능한 읽기 전용 파일만 해당한다. 현재는 `workflow.md` 하나뿐이다.

#### 절대 건드리지 않는 파일

```
CLAUDE.md                    ← 프로젝트 특화 내용
.claude/memory/lessons.md    ← 누적된 사용자 교정 패턴
docs/                        ← 프로젝트 문서 전체
tasks/                       ← 현재 작업 계획
.claude/state/               ← plan-gate 런타임 상태
```

### 3단계: 변경 사항 보고

아래 형식으로 사용자에게 보고한다.

```
## 하네스 업데이트 미리보기

### 신규 추가 (없던 파일)
- .claude/agents/frontend.md   — 프론트엔드 구현 전문 에이전트
- .claude/agents/backend.md    — 백엔드 구현 전문 에이전트
- .claude/agents/deeplearning.md — AI/딥러닝 구현 전문 에이전트

### 업데이트 (내용 변경됨)
- .claude/agents/verifier.md   — [변경 요약: 예) code_smells 필드 추가]
- .claude/memory/workflow.md   — [변경 요약: 예) 도메인 에이전트 위임 규칙 추가]
- .claude/hooks/design-precheck.py — [변경 요약]
  ...

### 변경 없음 (스킵)
- .claude/hooks/post-compact.py
- scripts/validate_arch.py
  ...

### 사용자 결정 필요 (diff 확인)
- .claude/settings.json        — [변경 내용 간략 요약]
- .claude/rules/code-style.md  — [변경 내용 간략 요약]

보존 (업데이트 제외)
- CLAUDE.md ✅
- .claude/memory/lessons.md ✅
- docs/ ✅
```

변경 없는 파일이 많으면 "(스킵)" 목록은 접어서 개수만 표시한다.

### 4단계: 사용자 확인

**AskUserQuestion 툴**로 확인한다:
- 질문: "위 내용으로 업데이트를 진행할까요? ('사용자 결정 필요' 항목은 파일별로 diff를 보여드리고 개별 확인합니다.)"
- 옵션: `["진행합니다 (Recommended)", "중단합니다"]`

### 5단계: 업데이트 실행

#### 5-1. "diff 확인 후 업데이트" 파일

파일별로 현재 내용과 새 템플릿을 비교한다.

- **현재 파일 == 새 템플릿**: 변경 없음, 조용히 스킵 (사용자 확인 불필요)
- **현재 파일 ≠ 새 템플릿**: diff를 보여주고 **AskUserQuestion 툴**로 결정을 요청:
  - 질문: "이 파일을 업데이트할까요? (<파일 경로>)"
  - 옵션: `["템플릿으로 덮어쓰기 (Recommended)", "건너뛰기", "상세 diff 보기"]`

  "건너뛰기"를 선택하면 해당 파일은 보존한다. "상세 diff 보기" 선택 시 diff를 출력하고 다시 AskUserQuestion으로 덮어쓰기/건너뛰기를 묻는다.

`workflow.md`는 예외로 사용자 확인 없이 항상 덮어쓴다 (읽기 전용 규칙 파일).

#### 5-2. 신규 파일 (없던 에이전트·훅)
존재하지 않으면 비교 없이 바로 생성한다 (기존 내용이 없으므로 확인 불필요).

#### 5-3. `.githooks/` 업데이트 후
실행 권한이 있는 파일은 `chmod +x`를 실행한다:
```bash
chmod +x .githooks/pre-commit .githooks/pre-push .githooks/post-checkout
```

#### 5-4. chmod 처리
`.githooks/` 업데이트 후 실행 권한 부여:
```bash
chmod +x .githooks/pre-commit .githooks/pre-push .githooks/post-checkout
```

### 6단계: 완료 보고

```
## 하네스 업데이트 완료

업데이트됨 (N개):
  ✅ .claude/agents/verifier.md
  ✅ .claude/agents/frontend.md  (신규)
  ✅ .claude/agents/backend.md   (신규)
  ✅ .claude/agents/deeplearning.md (신규)
  ✅ .claude/memory/workflow.md
  ...

스킵됨:
  — .claude/hooks/post-compact.py  (변경 없음)
  — .claude/settings.json          (사용자 선택)
  ...

보존됨:
  🔒 CLAUDE.md
  🔒 .claude/memory/lessons.md
  🔒 docs/

새로 추가된 에이전트 사용법:
  @frontend    — UI/컴포넌트 구현 위임 (tasks/todo.md + /approve-plan 선행 필요)
  @backend     — API/DB 구현 위임
  @deeplearning — 모델/학습 파이프라인 구현 위임
```

**plan-gate 재활성화 (필수)**: 1단계에서 plan-gate를 비활성화했다면 반드시 재활성화한다:
```bash
touch .claude/plan_gate_enabled
```
그리고 사용자에게 명시적으로 알린다:
```
✅ plan-gate 재활성화 완료.
```

## 주의사항

- `workflow.md`는 읽기 전용 규칙 파일이므로 사용자 확인 없이 항상 덮어쓴다.
  사용자 커스텀 규칙은 `CLAUDE.md` 또는 `.claude/rules/` 에 넣는 것이 올바른 위치다.
- 에이전트·훅·스크립트 파일은 모두 diff 확인 후 업데이트한다. 현재 파일과 새 템플릿이
  다를 경우 사용자에게 덮어쓰기 여부를 확인한 뒤 진행한다.
  커스텀 내용이 있으면 `n` 을 선택해 보존하고 수동으로 병합한다.
- 이 스킬은 project-init이 설치한 하네스 파일만 업데이트한다.
  프로젝트가 직접 추가한 훅·에이전트는 건드리지 않는다.
