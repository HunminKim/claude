# 개발 워크플로우 규칙

- **프로젝트명**: {{PROJECT_NAME}}

> 3계층 과정 규칙. CLAUDE.md가 @참조로 세션 시작 시 로드한다.
> /compact 후 post-compact.py hook이 CLAUDE.md 핵심 섹션을 재주입한다.

---

## TDD 순서

1. 테스트 먼저 작성 → 실패 확인 → 구현 → 통과 확인
2. 테스트 없이 구현 완료 판정 금지 (CLAUDE.md 명령어가 `# TBD`인 경우만 예외)
3. @verifier가 단위 테스트 실행 포함 — CLAUDE.md 명령어 섹션 기준

## Phase Gate

- Phase 전환 전 `docs/checklist.md` 해당 Phase 전체 ✅ 확인
- 미완료 행이 있으면 다음 Phase 진입 금지
- 예외 필요 시 `docs/decisions.md`에 D-번호로 기록 후 진행

## 실패 루프 규칙

- Bash 연속 실패 2회 → Claude Code 플러그인 훅이 경고 출력 (프로젝트 하네스와 별개)
- 경고 발생 시: 즉시 중단 → 설계 재검토 → 사용자 보고
- 같은 패치 재시도 금지 — 방향을 바꿔라

## 서브에이전트 위임 규칙

### 구현 위임 (패턴 A — 선승인 후 위임)
```
메인 Claude: tasks/todo.md 작성 → /approve-plan → @domain_agent 위임
서브에이전트: gate 열린 상태에서 담당 파일 범위 내 구현 → 완료 보고
메인 Claude: 보고 수신 → @verifier 호출
```

| 에이전트 | 호출 시점 | 담당 범위 |
|---------|---------|---------|
| `@frontend` | UI/컴포넌트 구현 | 컴포넌트, 스타일, 프론트 유틸 |
| `@backend` | API/DB 구현 | 라우터, 서비스, 스키마, 마이그레이션 |
| `@deeplearning` | 모델/학습 구현 | 모델 정의, 학습 스크립트, 데이터 파이프라인 |
| `@verifier` | 구현 완료 후 항상 | 기능 동작 검증 (통과/실패 판정) |

### 위임 시 필수 전달 정보
- 구현할 기능 설명
- 담당 파일 범위
- 완료 기준 (어떻게 되면 성공인가)
- 건드리면 안 되는 파일/범위

### 제약
- `/approve-plan` 없이 서브에이전트에게 구현 위임 금지
- 서브에이전트가 plan-gate limit 초과 시: 멈추고 메인에 보고 (자체 해결 불가)
- verifier는 발견만 한다 — 수정은 메인 에이전트의 몫

## /compact 타이밍

- 연관 기능 묶음 완료 후 실행
- 소단위마다 실행 금지
- compact 후 이 파일을 참조해 워크플로우 복습

## 설계 결정 기록

- 새 설계 결정·기술 선택·패턴 변경 시 `docs/decisions.md`에 D-번호로 기록
- D-번호는 순서대로 부여 (D-001, D-002...)
- 기존 항목 수정 금지 (append-only)
- 결정이 뒤집히면 새 D-번호로 "기각: D-XXX" 표기

## plan-gate 커맨드 가이드

plan-gate는 자동 차단 장치다. 아래 상황별로 어떤 커맨드를 써야 하는지 Claude가 사용자에게 안내한다.

### 작업 시작 전 (계획이 이미 있을 때)
```
tasks/todo.md 작성 완료
    → /approve-plan  ← 선승인. 이후 scope creep 임계값까지 무중단 작업 가능
```

### plan-gate가 차단했을 때
```
계획 작성 후 계속 진행     → /approve-plan
계획을 새로 짜야 함        → /replan → tasks/todo.md 수정 → /approve-plan
지금까지 작업 전체 버림    → /rollback
```

### scope creep 차단됐을 때 (승인 후 편집 횟수 초과)
```
현재까지 작업으로 완료     → /done
계획 갱신 후 계속 진행     → /replan → tasks/todo.md 수정 → /approve-plan
전체 되돌리기              → /rollback
```

### @verifier 검증 후
```
verifier ✅  →  /done          (체크포인트 정리 + gate 완료)
verifier ❌  →  /retry         (같은 체크포인트에서 재구현)
             →  /rollback      (이번 시도 전체 폐기)
```

### 공식 Plan Mode 사용 시
```
Plan Mode로 계획 작성 → tasks/todo.md 작성 → 사용자 Accept
    → 첫 Edit 시 plan-gate가 tasks/todo.md 감지 → 자동 승인
    (/approve-plan 불필요)
```

### plan-gate 켜기/끄기
```
/plan-gate-on   → .claude/plan_gate_enabled 생성, 활성화
/plan-gate-off  → .claude/plan_gate_enabled 삭제, 비활성화
```

### 커맨드 요약표
| 커맨드 | 사용 시점 | 효과 |
|--------|-----------|------|
| `/plan-gate-on` | plan-gate 활성화 | `.claude/plan_gate_enabled` 생성 |
| `/plan-gate-off` | plan-gate 비활성화 | `.claude/plan_gate_enabled` 삭제 |
| `/approve-plan` | 계획 확정 후 (시작 전 or 차단 후) | gate → approved, 작업 재개 |
| `/replan` | 계획 재작성 필요 시 | 카운터 리셋, 체크포인트 유지 |
| `/done` | 작업 완료 시 | 체크포인트 삭제, gate 종료 |
| `/retry` | verifier ❌ 후 재구현 | approved 상태 복귀, 카운터 누적 유지 |
| `/rollback` | 전체 되돌리기 | git reset → checkpoint tag, stash 복원 안내 |

## lessons.md 관리

- `.claude/memory/lessons.md` 는 50줄 이하로 유지
- 50줄 초과 시 오래된 교훈부터 `docs/decisions.md` 로 이관 후 삭제
- 세션 시작 시 반드시 읽는다 (CLAUDE.md 헤더 지시)
