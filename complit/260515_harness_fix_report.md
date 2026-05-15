# 하네스 수정 레포트

- **프로젝트**: Detection test (YOLOv8 baseline)
- **점검일**: 2026-05-15 (KST)
- **판정**: ❌ 하네스 수정 필요
- **촉발 사고**: 가중치 평가 파이프라인 `run_inference()` 메모리 누수 (10,000장을 단일 `model.predict()` 호출에 투입 → Results 객체 누적 → RAM 94GB 점유 → 동시 진행 GPU 학습 OOM kill 직전). 프로덕션 직전 메인이 직접 실행하다 우연히 발견.

---

## 발견된 문제 요약

| # | 문제 | 심각도 | 유형 |
|---|------|--------|------|
| 1 | `/done` 이 `verifier_status` 를 검사하지 않음 — verifier 미실행으로도 게이트 정상 종료 | ❌ | 템플릿 구조 |
| 2 | Stop 훅이 `approved` 상태 게이트의 verifier 방치를 감지 못함 | ⚠️ | 템플릿 구조 |
| 3 | 위임 에이전트 완료 보고 양식이 todo 단계별 입증을 강제하지 않음 | ❌ | 템플릿 구조 |
| 4 | `todo.md` 완료 기준 템플릿에 운영 규모 검증 항목이 없음 | ❌ | 템플릿 구조 |
| 5 | verifier 가 `todo.md` 의 `예상 단계` 완료 여부를 검증 대상으로 보지 않음 | ⚠️ | 템플릿 구조 |

**근본 원인 (Agent A·B 종합)**: verifier 호출이 메인 Claude 의 *수동·선택적 명령*인데, 어떤 훅·상태머신도 이를 강제하지 않는다. `plan_gate.json` 상 다수 게이트가 `verifier_status: null` 로 closed — 이번 cycle 만의 예외가 아니라 **검증이 구조적으로 optional**. 여기에 ① 위임 에이전트의 자기보고가 단계별 입증을 강제받지 않고 ② 완료 기준이 운영 규모를 요구하지 않아, 소규모 sanity check 통과만으로 완료 오판이 가능한 3중 허점.

---

## Upstream 수정 대상 (claude_skills 레포지토리)

> 아래는 모두 project-init plugin 템플릿/훅 구조 결함이다. claude_skills 레포에서 수정해야 신규 프로젝트에 반영된다.

### [문제 #1] `/done` 이 verifier 미검증을 통과시킴 — **단일 최대 원인**

**영향 파일**: `hooks/plan_gate_cli.py` — `cmd_done` (현재 약 126-165줄)

**현재 코드**
```python
    if gate["state"] not in ("approved", "verified"):
        _err(f"[plan-gate done] 현재 상태 '{gate['state']}'에서는 완료 불가.")
        return 1
    # → 곧바로 게이트 종료 로직
```

**수정 방향** (state 검사 직후 삽입)
```python
    if gate["state"] not in ("approved", "verified"):
        _err(f"[plan-gate done] 현재 상태 '{gate['state']}'에서는 완료 불가.")
        return 1
    if gate.get("verifier_status") not in ("✅", "❌"):
        _err(
            "[plan-gate done] verifier 미검증 — 완료 불가.\n"
            "  @verifier 호출 → docs/.verifier_result.json 생성 후 다시 /done.\n"
            "  의도적으로 건너뛰려면 /skip-verify 를 명시적으로 입력."
        )
        return 1
```

**수정 이유**: `verifier_status` 는 verifier 가 `.verifier_result.json` 을 Write 할 때만 설정되는데, `cmd_done` 이 이를 보지 않아 verifier 가 구조적으로 optional. 이 한 줄이 막혔으면 이번 사고의 갭 (a)는 발생 불가.

### [문제 #2] Stop 훅이 verifier 방치 게이트를 리마인드 안 함

**영향 파일**: `hooks/plan_gate_stop_alert.py` (현재 약 55줄 분기)

**현재 코드**: `gate["state"] != "approved"` 이면 즉시 return → verifier 없이 열린 채 방치된 게이트를 Stop 시점에 감지 못함.

**수정 방향**: `state == "approved"` + 편집 발생 이력 있음 + `verifier_status is None` 이면 stderr 로 "⚠️ @verifier 미호출 — /done 전 필수" 경고 출력. (차단은 #1 의 `cmd_done` 이 담당, Stop 훅은 리마인더 역할.)

**수정 이유**: #1 이 하드 차단이라면 #2 는 메인이 firefight 등으로 verifier 단계를 잊었을 때 turn 종료 시점에 능동적으로 환기 — 이번 사고의 "메인이 메모리 버그 대응 중 verifier 누락" 시나리오를 조기 차단.

### [문제 #3] 위임 에이전트 완료 보고가 단계별 입증을 강제 안 함

**영향 파일**: `skills/project-init/assets/templates/agents/deeplearning.md` (완료 보고 양식, 현재 약 79-116줄 / 행동 원칙 약 124줄)

**현재 코드**: "구현 항목" 표 + "Smoke test ✅/❌" 식 자기신고 체크박스만 존재.

**수정 방향**: 완료 보고에 **todo.md 단계 전수 표** 강제 —
```
### todo.md 단계별 완료 입증 (전 단계 필수 — 누락 시 ⚠️ 미완료 보고)
| 단계 | 상태 | 입증 (실행 명령 + 출력 근거) |
|------|------|------------------------------|
| 1. … | 완료/미완료 | … |
```
행동 원칙에 추가: "**할당된 todo 단계 중 하나라도 실제 실행·입증 못 하면 '⚠️ 미완료: 단계 N' 으로 보고. 다른 메커니즘(monitor 등)이 대신 할 것이라 추정 금지.**"

**수정 이유**: 이번 사고에서 위임 에이전트가 단계 4(정합성 검증)를 "monitor 가 보고할 것"이라며 건너뛰고 완료 보고 — 단계별 입증 강제가 있었으면 "단계 4 미완료"가 양식상 드러났다.

### [문제 #4] `todo.md` 완료 기준 템플릿에 운영 규모 항목 없음

**영향 파일**: `skills/project-init/assets/templates/tasks/todo.md` (현재 약 20줄)

**현재 코드**
```
**완료 기준**: 
```

**수정 방향**
```
**완료 기준** (기계적 판별 가능하게):
- [ ] 기능 정합성: …
- [ ] **운영 규모 검증**: 실제 입력 규모(데이터 건수/파일 크기/반복 횟수)에서
      메모리·시간·리소스 경로를 1회 이상 실측. 소규모 sanity check 만으로 완료 판정 금지.
- [ ] 에러/경계 입력 처리: …
```

**수정 이유**: 프로젝트 todo.md 가 "에러 없이 완료 / 표 채워짐" 같은 규모 무관 기준만 쓴 건 템플릿이 비어 있기 때문. 운영 규모 항목이 강제됐으면 10,000장 메모리 경로가 완료 기준에 포함돼 누수가 검증 단계에서 잡혔다.

### [문제 #5] verifier 가 todo 단계 완료를 검증 대상으로 안 봄

**영향 파일**: `skills/project-init/assets/templates/agents/verifier.md` (파이프라인 단계 검증, 현재 약 42-46줄)

**현재 코드**: 파이프라인 단계 검증이 `docs/constraints.yaml` 의 `pipeline_steps` 에만 의존 (대개 비어 있음). `tasks/todo.md` 의 `예상 단계` 는 검증 대상 아님.

**수정 방향**: 검증 대상에 `tasks/todo.md` 의 `예상 단계` 체크박스 추가 — 각 단계가 실제 완료·입증됐는지 확인, 미입증 단계는 ❌. 그 위에 "완료 기준에 운영 규모 경로가 있으면 실측 근거 없이는 ❌" 항목 추가.

**수정 이유**: #3 (에이전트 자기보고) 과 #4 (완료 기준) 를 verifier 가 독립적으로 교차 검증하는 안전망. 자기보고만 믿지 않게 함.

---

## 이 프로젝트 내 즉시 조치 사항

> 템플릿 수정은 신규 프로젝트에만 적용되므로, 이 프로젝트는 직접 패치 필요.

- [ ] **신규 plan-gate 게이트로 메모리 누수 수정 마무리** — `fc2aa6` 게이트는 이미 closed 라 되돌릴 수 없음. 메모리 누수 수정(`run_inference` 배치화는 적용됨) → `@verifier` 정식 호출 → `/done` 정상 절차로.
- [ ] **`tasks/todo.md` 완료 기준에 운영 규모 항목 추가** — "10,000장 전수 추론 시 RAM 피크 실측" 항목 명시 후 재검증.
- [ ] **`.claude/agents/verifier.md` 에 수정 #5 수동 반영** — todo 단계 검증 + 운영 규모 실측 근거 요구.
- [ ] **`.claude/memory/workflow.md` / `CLAUDE.md` 에 수정 #3·#4 취지 수동 반영** — 위임 시 단계별 입증 강제, 완료 기준에 운영 규모 필수.
- [ ] **`.claude/memory/lessons.md` 기록** — "verifier 가 구조적으로 optional (`/done` 이 `verifier_status` 미검사) → 검증 누락 가능. verifier 호출은 의식적으로." + "하네스 관련 패턴" 섹션에 이번 사고 등재.
- [ ] **`docs/checklist.md` 에 Phase 6 행 등재** — Phase Gate 규칙이 동작하도록.

---

## 핵심 한 줄

`hooks/plan_gate_cli.py` 의 `cmd_done` 이 `verifier_status` 를 검사하지 않는 것(수정 #1)이 사고의 단일 최대 원인 — 이것만 막아도 verifier 미호출은 구조적으로 차단된다.
