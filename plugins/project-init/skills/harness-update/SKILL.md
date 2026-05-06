---
name: harness-update
description: 기존 project-init 하네스를 최신 버전으로 업데이트한다. 에이전트·훅·워크플로우 규칙을 갱신하면서 사용자 데이터(CLAUDE.md, lessons.md, docs/)는 보존한다. "/harness-update", "하네스 업데이트", "에이전트 업데이트", "훅 최신화", "verifier 업데이트", "하네스 최신 버전으로" 등의 요청에 이 스킬을 사용한다.
---

# Harness Update Skill

기존 project-init 하네스를 최신 템플릿으로 업데이트한다.
**사용자 데이터는 절대 건드리지 않는다.**

## 실행 순서

### 1단계: 사전 확인

현재 디렉토리가 project-init으로 초기화된 프로젝트인지 확인한다.

```
필수 확인:
- .claude/agents/verifier.md 존재 여부  → 없으면 "project-init 먼저 실행하세요" 안내 후 중단
- .claude/plan_gate_enabled 존재 여부   → 있으면 plan-gate 활성 상태 (주의 메시지 출력)
```

plan-gate가 활성 상태이면:
```
⚠️  plan-gate가 활성화된 상태입니다.
    하네스 업데이트는 여러 파일을 수정하므로 plan-gate가 차단할 수 있습니다.
    계속 진행하려면 /plan-gate-off 후 업데이트 완료 후 /plan-gate-on 하거나,
    tasks/todo.md에 "하네스 업데이트" 계획을 작성하고 /approve-plan 하세요.
    
    계속 진행할까요? (plan-gate 상태 유지 또는 일시 비활성화 선택)
```

사용자 확인 후 진행 또는 중단.

### 2단계: 현재 상태 스캔

다음 파일들의 현재 존재 여부와 내용을 읽어 템플릿과 비교한다.

**템플릿 경로**: 이 스킬이 실행되는 플러그인 루트의 `skills/project-init/assets/templates/` 아래.
실제 경로는 `CLAUDE_PLUGIN_ROOT` 환경변수 또는 이 SKILL.md의 위치에서 추론한다.

#### 비교 대상 파일 목록

| 프로젝트 내 경로 | 템플릿 경로 | 처리 방식 |
|---------------|-----------|---------|
| `.claude/agents/verifier.md` | `agents/verifier.md` | 항상 업데이트 |
| `.claude/agents/frontend.md` | `agents/frontend.md` | 항상 업데이트 (없으면 신규 생성) |
| `.claude/agents/backend.md` | `agents/backend.md` | 항상 업데이트 (없으면 신규 생성) |
| `.claude/agents/deeplearning.md` | `agents/deeplearning.md` | 항상 업데이트 (없으면 신규 생성) |
| `.claude/memory/workflow.md` | `.claude/memory/workflow.md` | 항상 업데이트 (읽기 전용 규칙) |
| `.claude/hooks/design-precheck.py` | `.claude/hooks/design-precheck.py` | 항상 업데이트 |
| `.claude/hooks/post-compact.py` | `.claude/hooks/post-compact.py` | 항상 업데이트 |
| `.claude/hooks/cleanup_suggest.py` | `.claude/hooks/cleanup_suggest.py` | 항상 업데이트 |
| `.githooks/pre-commit` | `.githooks/pre-commit` | 항상 업데이트 |
| `.githooks/pre-push` | `.githooks/pre-push` | 항상 업데이트 |
| `.githooks/post-checkout` | `.githooks/post-checkout` | 항상 업데이트 |
| `scripts/validate_arch.py` | `scripts/validate_arch.py` | 항상 업데이트 |
| `.claude/settings.json` | `.claude/settings.json` | diff 후 사용자 결정 |
| `.claude/rules/code-style.md` | `.claude/rules/code-style.md` | diff 후 사용자 결정 |

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

```
위 내용으로 업데이트를 진행할까요?
- '사용자 결정 필요' 항목은 각각 diff를 보여드리고 개별 확인합니다.
- 전체 중단을 원하시면 '아니오'를 입력하세요.
```

### 5단계: 업데이트 실행

#### 5-1. "항상 업데이트" 파일
템플릿 내용을 그대로 덮어쓴다. 단, `{{PROJECT_NAME}}` 등 자리표시자가
있는 파일은 덮어쓰지 않는다 (agents/*, hooks/*, scripts/* 는 자리표시자 없음).

#### 5-2. "사용자 결정 필요" 파일 (settings.json, code-style.md)
파일별로 현재 내용과 템플릿의 diff를 보여주고:
```
이 파일을 업데이트할까요?
  y — 템플릿으로 덮어쓰기
  n — 건너뛰기
  d — 상세 diff 보기
```

#### 5-3. `.githooks/` 업데이트 후
실행 권한이 있는 파일은 `chmod +x`를 실행한다:
```bash
chmod +x .githooks/pre-commit .githooks/pre-push .githooks/post-checkout
```

#### 5-4. 신규 에이전트 파일
없던 에이전트 파일은 확인 없이 생성한다 (새 파일이므로 기존 내용 없음).

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

plan-gate가 업데이트 중에 비활성화됐다면 재활성화를 안내한다:
```
plan-gate를 다시 활성화하려면: /plan-gate-on
```

## 주의사항

- `workflow.md`는 읽기 전용 규칙 파일이므로 템플릿으로 항상 덮어쓴다.
  사용자 커스텀 규칙은 `CLAUDE.md` 또는 `.claude/rules/` 에 넣는 것이 올바른 위치다.
- 에이전트 파일 업데이트 시 기존 에이전트 파일의 커스텀 내용은 사라진다.
  에이전트를 커스터마이징했다면 업데이트 전에 백업하거나 병합을 수동으로 진행한다.
- 이 스킬은 project-init이 설치한 하네스 파일만 업데이트한다.
  프로젝트가 직접 추가한 훅·에이전트는 건드리지 않는다.
