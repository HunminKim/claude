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

## 장시간 Bash 실행 규칙

- 30초 이상 걸릴 명령 실행 전: "~분 예상, 대기합니다" 먼저 출력 후 실행
- 백그라운드 모니터링 중: 5분 간격으로 현재 상태 보고 (진행률, 로그 요약)
- 명령 완료 즉시: 결과 요약 보고 — 대기시키지 않는다
- 예외 없음: 결과가 나왔으면 침묵하지 않는다

## 실패 루프 규칙

- Bash 연속 실패 2회 → Claude Code 플러그인 훅이 경고 출력 (프로젝트 하네스와 별개)
- 경고 발생 시: 즉시 중단 → 설계 재검토 → 사용자 보고
- 같은 패치 재시도 금지 — 방향을 바꿔라

## 서브에이전트 위임 규칙

### 구현 위임 (패턴 A — 선승인 후 위임)
```
메인 Claude: tasks/todo.md 5섹션 작성 → Plan subagent 외부 검증 → /approve-plan → @domain_agent 위임
서브에이전트: gate 열린 상태에서 담당 파일 범위 내 구현 → 완료 보고
메인 Claude: 변경 증거 확인 → @verifier 호출
```

| 에이전트 | 호출 시점 | 담당 범위 |
|---------|---------|---------|
| `@frontend` | UI/컴포넌트 구현 | 컴포넌트, 스타일, 프론트 유틸 |
| `@backend` | API/DB 구현 | 라우터, 서비스, 스키마, 마이그레이션, 인증·인가 *로직* |
| `@deeplearning` | 모델/학습 구현 | 모델 정의, 학습 스크립트, 데이터 파이프라인 |
| `@infra` | 인프라 구현 | IaC, 컨테이너 이미지·오케스트레이션, CI/CD, 클라우드 리소스·IAM·시크릿·모니터링 IaC |
| `@verifier` | 구현 완료 후 항상 | 기능 동작 검증 (통과/실패 판정) |

`@backend` vs `@infra` 경계: `agents/infra.md` 의 "도메인 경계" 표가 단일 진실 원천.
- Dockerfile, k8s manifest, CI workflow 파일, IAM 정책 = `@infra`
- 컨테이너 안에서 실행되는 애플리케이션 코드, DB 스키마 마이그레이션 파일, 권한 검증 미들웨어 = `@backend`

### 위임 전 due diligence 체크리스트 (Plan subagent 호출 의무)

메인 self-check만으로는 영향 파일 누락·기술 충돌·USER_DECISIONS 누설을 반복적으로 놓친다.
서브에이전트 위임 직전 메인은 Plan subagent를 호출해 외부 검증한다:

```
Agent(subagent_type="Plan", prompt="""
다음 tasks/todo.md 를 위임 직전 검증한다:
<todo.md 본문 발췌>

체크리스트:
1. "영향 파일" 섹션이 비어 있지 않은가
2. 동기/비동기·MCP 호출 방식·공유 타입 충돌 여부
3. fallback 분기가 HTML 주석 마커로 별도 게이트 분리됐는가
4. USER_DECISIONS 가 평문에 섞이지 않고 별도 섹션에 있는가
5. 직전 위임 사이클에서 효과 본 패턴이 재적용됐는가

⚠️ 항목이 있으면 명시. 통과면 "검증 통과" 한 줄로 종료.
""")
```

- Plan 보고에 ⚠️ 가 있으면 todo.md 보강 후 재검증 — 통과 시에만 도메인 에이전트 위임
- /approve-plan 은 plan-gate 절차 승인이지 todo.md 내용 품질 승인이 아니다 — 내용 품질은 Plan subagent 의 책임 영역
- Plan subagent 호출은 옵션이 아닌 위임 직전 의무 단계

### 위임 시 필수 전달 정보 (표준 블록 — CLAUDE.md 의 "위임 시 메인이 전달할 것" 참조)

위임 프롬프트는 `TASK / USER_DECISIONS / CONSTRAINTS / GATE` 4블록을 사용한다.
**모든 블록 내용은 tasks/todo.md 5섹션에서 그대로 발췌한다 — todo.md 가 단일 진실 원천.**
사용자 결정 영역(USER_DECISIONS)을 평문에 섞지 않는 게 핵심 — 서브에이전트가 임의 재해석할 여지를 없앤다.

- TASK: 구현할 기능, 담당 파일 범위(건드리면 안 되는 파일 포함), 완료 기준
- USER_DECISIONS: 사용자 명시 결정. 자유도 0. **누락 금지** (비면 "없음"으로 명시)
- CONSTRAINTS: 일반 제약. 막히면 즉시 메인에 보고
- GATE: plan-gate 상태(`approved`)

### 서브에이전트 보고 신뢰성 (변경 증거 강제)

도메인 에이전트(@frontend/@backend/@deeplearning) 보고를 받으면:

- 보고에 "변경 증거" 섹션이 있는지 확인 (시작 SHA + git status + git diff --stat 원문)
- 없으면 보고를 ⚠️ 신뢰성 격하 → 메인이 직접 `git diff --stat <시작SHA>..HEAD` 실행 후 비교
- 자연어 파일 목록이 git diff --stat 출력과 어긋나면 에이전트에 재확인 요청
- 메인의 직접 확인 결과가 우선 — 자연어 자기보고는 보조 자료다

자기 변경을 "이미 있었다 / 이전 세션 변경이다"라고 오인 보고하는 사고를 막기 위한 게이트다.
working tree는 메인과 공유되지만 context는 분리되어 있어, 시작 기준점 없이는 본인 변경을 식별할 수 없다.

### 제약
- `/approve-plan` 없이 서브에이전트에게 구현 위임 금지
- 서브에이전트가 plan-gate limit 초과 시: 멈추고 메인에 보고 (자체 해결 불가)
- USER_DECISIONS 충돌 보고를 받으면 메인이 임의 재해석 금지 — 사용자에게 결정 위임
- verifier는 발견만 한다 — 수정은 메인 에이전트의 몫
- verifier 는 production 경로(`runs/`, `outputs/`, `data/`, DB)에 부작용을 만들지 않는다. 부작용 흔적이 보이면 메인이 즉시 verifier 보고를 신뢰성 ⚠️ 로 격하하고 정리 후 재검증한다

### verifier fallback (@verifier "agent not found" 시)

`.claude/agents/verifier.md` 는 세션 시작 시에만 로드된다 (예외: `/agents` 인터페이스로 만든 에이전트는 즉시 적용). 사용자가 직접 타이핑할 때의 수동 멘션 형태는 `@agent-verifier` 다. project-init 직후 동일 세션에서 verifier 호출이 실패하면 아래 절차로 대체한다:

1. `Agent` tool로 `subagent_type="general-purpose"` 호출
2. 첫 메시지에 `.claude/agents/verifier.md` 전문을 컨텍스트로 첨부
3. `docs/.verifier_result.json` 을 표준 스키마로 직접 작성하도록 요청
   - 필수 필드: `feature_name`, `verdict`(✅/❌), `test_items`, `issues`, `evidence`, `implementation`
4. JSON 파일이 생성되면 `update_docs.py` 훅이 자동 처리 (PostToolUse Write 매칭)

근본 해결: Claude Code 재시작 → `claude --continue` 로 대화 유지하며 재시작하면 verifier 인식됨.

## verifier code_smells 처리 흐름

verifier 검증 보고서에서 `code_smells` 항목이 기록됐을 때:

- **판정 ✅ + code_smells 있음** → `/simplify` 호출 → 개선안 검토 후 `/done`
- **판정 ❌ + code_smells 있음** → 실패 원인 먼저 수정 → 재검증 ✅ 후 위 흐름 적용
- `/simplify` 결과가 불필요하거나 범위 초과라고 판단되면 거부 가능 — `lessons.md`에 이유 기록

## 임시방편 → 절차 승격 게이트 (verifier ✅ 후 /done 전)

한 번 효과 본 운영 패턴이 다음 위임에 자동 반영되지 않으면 같은 사고가 반복된다.
verifier 통과 후 `/done` 입력 전에 메인 Claude는 아래를 점검한다:

- 이번 사이클에서 새로 효과 본 위임/보고 패턴이 있는가?
  - 예: "위임 프롬프트에 git status 첨부를 강제했더니 자기보고 정확도가 올랐다"
  - 예: "USER_DECISIONS 블록을 명시했더니 임의 차선책 선택이 사라졌다"
- 있으면 `CLAUDE.md` 또는 `.claude/memory/workflow.md`에 반영 → 그 다음 `/done`
- 없으면 그대로 `/done`

승격 대상 후보:
- 서브에이전트에 새로 강제했더니 정확도가 오른 보고 형식
- 메인이 새로 점검하기 시작한 체크포인트
- 사용자가 명시 요청해서 추가된 절차 (한 번 효과 본 것은 다음에도 효과 본다)

승격하지 않은 임시방편은 그 사이클 종료와 함께 휘발되어 같은 사고가 재발한다.

### 운영 메모 체크박스 (절차 반영 강제)

debug 노트·세션 종료 정리·임시 메모 등 운영 메모 작성 시 메모 본문 끝에 아래 체크박스를 박는다:

```
- [ ] CLAUDE.md 반영 완료
- [ ] .claude/memory/workflow.md 반영 완료
```

`/done` 직전에 체크박스 상태 확인 — 미완료면 `/done` 차단하고 절차 반영 후 재진입.
"메모만 남기고 절차 미반영" 패턴은 다음 세션부터 휘발되어 같은 사고가 재발한다.
메모 1건 = 절차 반영 commit 1건 (1:1 매칭이 성공 기준).

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
tasks/todo.md 작성
    → Claude가 계획 요약 후 /approve-plan 요청  ← 자동 유도
    → 사용자: /approve-plan  ← 선승인. 이후 scope creep 임계값까지 무중단 작업 가능
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
             →  /skip          (현재 변경 보존, 문제 인지 후 다음 주기에서 처리)
             →  /rollback      (이번 시도 전체 폐기 — 체크포인트 있을 때만 가능)
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
| `/skip` | verifier ❌ 후 현재 변경 보존 | 문제 인지 채로 gate 마감 (`/keep` 도 동일) |
| `/skip-verify` | verifier 판정 전, 검증 없이 마감할 때 | 검증 생략 마감 (⏭️ 기록. 판정 ✅/❌ 후엔 사용 불가) |
| `/retry` | verifier ❌ 후 재구현 | approved 상태 복귀, 카운터 누적 유지 |
| `/rollback` | 전체 되돌리기 (체크포인트 필수) | git reset → checkpoint tag, stash 복원 안내 |

## lessons.md 관리

- `.claude/memory/lessons.md` 는 50줄 이하로 유지
- 50줄 초과 시 오래된 교훈부터 `docs/decisions.md` 로 이관 후 삭제
- 세션 시작 시 반드시 읽는다 (CLAUDE.md 헤더 지시)
