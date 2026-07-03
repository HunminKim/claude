---
name: plan-gate-help
description: plan-gate 상황별로 어떤 슬래시 커맨드를 써야 하는지 안내한다. verifier ✅/❌ 후 처리, plan-gate 차단·thrash 차단 시 대응, 계획 승인 흐름, 공식 Plan Mode 연동, 스코프 강제(shadow/enforce/off), 켜기/끄기, 전체 커맨드 요약표를 담는다. "plan-gate 커맨드 뭐 써?", "verifier ❌면 뭐 해야 해", "게이트 차단됐는데", "스코프 강제 어떻게", "approve/done/retry/skip/rollback 언제 쓰나", "커맨드 요약" 등 plan-gate 커맨드 선택이 필요할 때 사용한다.
---

# plan-gate 커맨드 가이드

plan-gate는 자동 차단 장치다. 아래 상황별로 어떤 커맨드를 써야 하는지 Claude가 사용자에게 안내한다.

> 계획을 새로 세울지(plan-worthy) 그냥 진행할지(trivial)의 **판단 기준**은 이 스킬이 아니라
> `.claude/memory/workflow.md` 의 "새 요청 진입 — 계획 필요 여부 자가 판단"에 있다.

## 작업 시작 전 (계획이 이미 있을 때)
```
tasks/todo.md 작성
    → Claude가 계획 요약 후 /approve-plan 요청  ← 자동 유도
    → 사용자: /approve-plan  ← 선승인. 이후 반복 편집(thrash) 임계값까지 무중단 작업 가능
```

## plan-gate가 차단했을 때
```
계획 작성 후 계속 진행     → /approve-plan
계획을 새로 짜야 함        → /replan → tasks/todo.md 수정 → /approve-plan
지금까지 작업 전체 버림    → /rollback
```

## 반복 편집(thrash) 차단됐을 때 (승인 후 같은 파일을 수렴 없이 반복)
```
현재까지 작업으로 완료     → /done
계획 갱신 후 계속 진행     → /replan → tasks/todo.md 수정 → /approve-plan
전체 되돌리기              → /rollback
```

## @verifier 검증 후

> **먼저 live 게이트 상태를 확인한다** (`plan_gate_cli.py status` 또는 `.claude/state/plan_gate.json` 의 `current_gate_id`).
> 재개 배너의 상태는 스냅샷(stale)일 수 있으니 안내 직전 재조회한다.
> - `state == created` **또는** `current_gate_id == null` → **`/done` 안내 금지.** 미승인 소편집은 다음 편집 시 자동 롤오버로 닫히므로 별도 조치 불필요(명시 `/done` 도 "무조건 마감"으로 동작하나 필수 아님).
> - `state == approved / verified` → 아래 표대로 처리.

```
verifier ✅  →  /done          (체크포인트 정리 + gate 완료)
verifier ❌  →  /retry         (같은 체크포인트에서 재구현)
             →  /skip          (현재 변경 보존, 문제 인지 후 다음 주기에서 처리)
             →  /rollback      (이번 시도 전체 폐기 — 체크포인트 있을 때만 가능)
```

## 공식 Plan Mode 사용 시
```
Plan Mode로 계획 작성 → tasks/todo.md 작성 → 사용자 Accept
    → 첫 Edit 시 plan-gate가 계획 감지 → /approve-plan 유도 (gate: created 유지)
    → 사용자: /approve-plan  ← 명시 승인해야 구현 게이트가 열림
```
> 자동 승인 안 함: 통제 체크포인트는 todo.md 존재가 아니라 사람의 명시 승인으로만 열린다.
> (한 번도 검토 안 한 계획이 무인 승인되는 우회 방지 — 명시 /approve-plan 필수)

## plan-gate 켜기/끄기
```
/plan-gate-on   → .claude/plan_gate_enabled 생성, 활성화
/plan-gate-off  → .claude/plan_gate_enabled 삭제, 비활성화
```

## 스코프 강제 (기본 shadow — 환기만)
tasks/todo.md 에 이번 작업이 건드릴 파일 패턴을 선언하면 스코프 밖 편집을 관리할 수 있다.
**스코프를 선언하면 기본값이 shadow** 라 위반 시 차단 없이 환기된다(매니페스트 없으면 no-op):
```
<!-- plan-gate: scope BEGIN -->
src/auth/**          ← ** = 하위 전체, * = 한 경로 단계
src/models/user.py
<!-- plan-gate: scope END -->
<!-- plan-gate: do-not-touch BEGIN -->
src/payment/**       ← scope 보다 우선하는 금지 목록
<!-- plan-gate: do-not-touch END -->
```
```
/plan-gate-scope-shadow   → 위반 감지·기록만 (기본값, 차단·롤백 없음)
/plan-gate-scope-enforce  → 스코프 밖 Edit 거부(layer-1) + Bash 변경 롤백(layer-2)
/plan-gate-scope-off      → 강제 완전 끄기 (환기조차 없음, 매니페스트는 기록만)
```
- plan-gate 운영 파일(tasks/todo.md·.claude/**·docs/.verifier_result.json)은 무조건 허용
- layer-2(Bash 변경 롤백)는 git 저장소에서만 동작 — 비-git 은 layer-2 no-op. 단 layer-1(스코프 밖 Edit 거부)은 git 무관 항상 작동
- **enforce 는 게이트가 닫히면(`/done`·`/skip`·`/rollback`) 자동으로 shadow 로 복귀**한다 (한 작업용 enforce 가 다음 작업에서 신규 파일을 삭제하는 stale 사고 방지). 계속 강제하려면 다음 사이클에서 다시 `/plan-gate-scope-enforce`. 자동으로 enforce 를 *켜는* 규칙은 없다 — 파괴적 강제는 명시 opt-in 만.

## 커맨드 요약표
| 커맨드 | 사용 시점 | 효과 |
|--------|-----------|------|
| `/plan-gate-on` | plan-gate 활성화 | `.claude/plan_gate_enabled` 생성 |
| `/plan-gate-off` | plan-gate 비활성화 | `.claude/plan_gate_enabled` 삭제 |
| `/approve-plan` | 계획 확정 후 (시작 전 or 차단 후) | gate → approved, 작업 재개 |
| `/replan` | 계획 재작성 필요 시 | 카운터 리셋, 체크포인트 유지 |
| `/done` | 작업 완료 시 | 체크포인트 삭제, gate 종료 |
| `/skip` | verifier ❌ 후 현재 변경 보존 | 문제 인지 채로 gate 마감 (`/keep` 도 동일) |
| `/skip-verify` | verifier 판정 전, 검증 없이 마감할 때 | 검증 생략 마감 (⏭️ 기록. 판정 ✅/❌ 후엔 사용 불가) |
| `/retry` | verifier ❌ 후 재구현 | approved 상태 복귀, 카운터 누적 유지 |
| `/rollback` | 전체 되돌리기 (체크포인트 필수) | 프라이빗 ref 스냅샷에서 복원 (존재 파일 복구·신규 삭제) |
| `/plan-gate-scope-shadow` | 스코프 강제 관찰 | 위반 감지·기록만 (롤백 X) |
| `/plan-gate-scope-enforce` | 스코프 강제 켜기 | 스코프 밖 Edit 거부 + Bash 변경 롤백 |
| `/plan-gate-scope-off` | 스코프 강제 완전 끄기 | 매니페스트 기록만 (환기도 없음) |
