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

### 위임 시 필수 전달 정보 (표준 블록)

위임 프롬프트는 `TASK / USER_DECISIONS / CONSTRAINTS / GATE` 4블록을 사용한다.
**모든 블록 내용은 tasks/todo.md 5섹션에서 그대로 발췌한다 — todo.md 가 단일 진실 원천.**
사용자 결정 영역(USER_DECISIONS)을 평문에 섞지 않는 게 핵심 — 서브에이전트가 임의 재해석할 여지를 없앤다.

- TASK: 구현할 기능, 담당 파일 범위(건드리면 안 되는 파일 포함), 완료 기준
- USER_DECISIONS: 사용자 명시 결정. 자유도 0. **누락 금지** (비면 "없음"으로 명시)
- CONSTRAINTS: 일반 제약. 막히면 즉시 메인에 보고
- GATE: plan-gate 상태(`approved`)

### 서브에이전트 보고 신뢰성 (변경 증거 강제)

도메인 에이전트(@frontend/@backend/@deeplearning) 보고를 받으면:

- 보고에 "변경 증거" 섹션이 있는지 확인 (위임 직전 기준점 + git status + diff 원문)
- 기준점 = **위임 직전 working tree 상태** (베이스 커밋 아님):
  clean git → 시작 SHA `git diff <시작SHA>..HEAD`;
  dirty/비-git → `git stash create` 트리 SHA 또는 `cp file file.preagent` 대비 diff.
  ⚠️ 베이스 커밋 대비 diff 금지 — 기존 패치를 에이전트 신규로 오인해 폐기 사고
- 없으면 ⚠️ 신뢰성 격하 → 메인이 직접 위 기준점으로 diff 실행 후 비교
- 자연어 파일 목록이 diff 출력과 어긋나면 에이전트에 재확인 요청
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
    → 사용자: /approve-plan  ← 선승인. 이후 반복 편집(thrash) 임계값까지 무중단 작업 가능
```

### plan-gate가 차단했을 때
```
계획 작성 후 계속 진행     → /approve-plan
계획을 새로 짜야 함        → /replan → tasks/todo.md 수정 → /approve-plan
지금까지 작업 전체 버림    → /rollback
```

### 반복 편집(thrash) 차단됐을 때 (승인 후 같은 파일을 수렴 없이 반복)
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

### 스코프 강제 (선택, 기본 off)
tasks/todo.md 에 이번 작업이 건드릴 파일 패턴을 선언하면 스코프 밖 편집을 막을 수 있다:
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
/plan-gate-scope-shadow   → 위반 감지·기록만 (롤백 X, 먼저 관찰 권장)
/plan-gate-scope-enforce  → 스코프 밖 Edit 거부(layer-1) + Bash 변경 롤백(layer-2)
/plan-gate-scope-off      → 강제 끄기 (매니페스트는 기록만)
```
- plan-gate 운영 파일(tasks/todo.md·.claude/**·docs/.verifier_result.json)은 무조건 허용
- layer-2(Bash 변경 롤백)는 git 저장소에서만 동작 — 비-git 은 감지·경고만

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
| `/rollback` | 전체 되돌리기 (체크포인트 필수) | 프라이빗 ref 스냅샷에서 복원 (존재 파일 복구·신규 삭제) |
| `/plan-gate-scope-shadow` | 스코프 강제 관찰 | 위반 감지·기록만 (롤백 X) |
| `/plan-gate-scope-enforce` | 스코프 강제 켜기 | 스코프 밖 Edit 거부 + Bash 변경 롤백 |
| `/plan-gate-scope-off` | 스코프 강제 끄기(기본) | 매니페스트 기록만 |

## lessons.md 관리

- `.claude/memory/lessons.md` 는 50줄 이하로 유지
- 50줄 초과 시 오래된 교훈부터 `docs/decisions.md` 로 이관 후 삭제
- 세션 시작 시 반드시 읽는다 (CLAUDE.md 헤더 지시)

## 메인 = 오케스트레이터 (위임 vs 직접)

메인 Claude 는 오케스트레이터다. 직접 구현은 최소화하고 전문 에이전트에 위임한다.
**직접 처리(위임 불필요)**: 단일 파일 10줄 이하 수정 · 설정값 변경·import 추가·상수 정의 · 임시 디버깅·scaffold/boilerplate · 문서 업데이트.
그 외 구현은 도메인 에이전트 위임 (위 "서브에이전트 위임 규칙" 참조).

## 구현 전 확인 (Think Before Coding)

- **가정 명시**: 전제한 게 있으면 "Assumption:" 으로 명시. 틀렸을 때 되돌리기 어려우면 먼저 확인 요청
- **혼란 표면화**: 요청이 불명확하면 추측 말고 무엇이 불명확한지 짚어 묻는다 ("일단 해봤습니다" 금지)
- **해석 분기 표면화**: 2가지 이상 해석 가능하면 위험도 무관하게 나열 후 선택 요청 (결과가 사실상 동일할 때만 바로 진행)
- **단순한 방법 제안**: 더 단순한 접근이 보이면 구현 전 언급
- **절충안 명시**: 트레이드오프가 있으면 조용히 고르지 말고 제시 후 결정 요청
- **단정 전 영향 범위 확인**: ✅/❌ 단정 전, 관여 코드 경로(진입점 + 호출 체인 + 설정·미들웨어·콜백·후처리)를 먼저 나열. 한 곳으로 안 좁혀지면 "확인한 범위 / 확인 안 한 가능성(추가 확인 필요)" 두 줄로 분리

## verifier 결과 안전 규칙

- 코드 수정 후 사용자 실행 전 반드시 `@verifier` 호출 (예외 없음)
- 테스트 명령(`TEST_COMMAND`)이 `# TBD`이거나 테스트 인프라가 없으면 대안 기준 검증: ① 에러 없이 실행·예상 출력 ② 비정상 입력에서 에러/기본값 처리 ③ 기존 동작 시나리오 유지. 사용한 실행 명령·출력을 보고서에 첨부
- 결과 처리: ✅ → `/done` 요청 / ❌(1회) → `/retry` 재구현·재검증 / ❌(2회 연속 같은 원인) → 즉시 중단·원인 분석 보고 → 사용자에게 `/skip`·`/rollback` 위임
- 파괴적 작업(스키마 변경·삭제·외부 전송) 수반 시 1회차도 사용자 확인 필수
- **자동 롤백(git reset)은 사용자 명시 지시 없이 절대 실행하지 않는다**
- 진행이 막히면 즉시 중단 → 계획 재수립 → 사용자 확인 (밀어붙이지 않음)

## 버그 보고 대응

버그 보고를 받으면 즉시 수정 전 진단부터 보고한다: **추정 원인 / 수정 범위 / 불확실한 점**. 세 가지가 명확할 때만 수정 진행.
- 재현 조건·기대 동작이 불명확하면 Assumption 으로 명시하고 확인 요청
- 2개 이상 레이어에 걸치거나 되돌리기 어려운 변경이면 반드시 먼저 확인
- 예외(즉시 수정): 오탈자·import 누락처럼 원인·범위가 자명한 것
- (이 절차는 글로벌 훅 `detect_bug_report` 가 매 prompt 환기한다)

## 외부 SDK·툴체인 작업

- 외부 SDK·컴파일러·변환 툴체인 작업 전: 공식 워크플로우 전체 단계를 먼저 나열·확인 (중간 변환·서명·검증 단계 묵음 생략 방지)
- wrapper 스크립트가 있으면 그것만 사용 (인라인 명령 금지)
- 모르는 단계는 추측 말고 사용자에 확인

## 격리 실행 / 호스트 오염 방지

- Claude가 **호스트 네이티브**로 실행 중이면(컨테이너 밖), 영구 설치로 호스트를 오염시키지 않는다.
- 도구·의존성 설치가 필요하면 우선순위: ① 프로젝트 venv(`uv`/`.venv`) ② docker(`docker run --rm`/compose, 데이터만 마운트) ③ 임시 실행(`uvx`/`pipx run`).
- 합의 없이 지양: 시스템 패키지매니저(apt/dnf/brew) 설치, 전역 `pip install`, `npm -g`, `curl … | sh`, `~/.local/bin` 영구 설치.
- **예외**: 하네스가 요구하는 툴체인 부트스트랩(uv·ruff·python 등)은 사용자와 합의된 정상 설치다.
- **컨테이너 내부 세션이면 이 규칙 미적용** — 설치가 일회용 컨테이너에 갇히므로 직접 설치 무방.

## 장시간 작업 모니터링

- 학습·배치 등 장시간 작업을 시작시키면 `/monitor [간격]` 사용을 **먼저 제안**한다 — 사용자가 "살아있니?"로 침묵을 깨기 전에 자동 간격 보고를 거는 게 기본

## 운영 정보 기록 (대화에 두지 않는다)

세션 중 알게 된 비밀값(계정·비밀번호·토큰)은 `.env`(git 미추적 — .gitignore + pre-commit 이중 차단, 훅이 열람도 차단해 값 확인은 사용자 몫). 비밀 아닌 환경 정보(엔드포인트·포트·경로·설정값)는 `docs/deployment_guide.md`, docs 엔 비밀의 **키 이름만**. compact 후에도 파일은 남아 "아까 알려준 계정" 재설명이 사라진다.

## 완료 기준 작성 (tasks/todo.md)

완료 기준은 기계적으로 판별 가능하게 쓴다.
- 나쁜 예 "기능이 잘 동작함" → 좋은 예 "pytest 0 failures"
- 나쁜 예 "버그 수정" → 좋은 예 "재현 스크립트 에러 없이 종료"
- 테스트 없는 프로젝트: "대상 함수가 [입력]→[출력] 반환하며 bash 실행 성공"

## 문서 동기화

- 코드 수정 전 `docs/technical_doc.md` 및 연관 모듈 먼저 확인 (충돌 방지)
- 새 명령어 확정 → CLAUDE.md 명령어 섹션 즉시 추가 (일회성·디버깅용 제외). 명령어 코드가 변경·삭제되면 섹션도 즉시 수정·삭제
- 기능 완료 → README.md 영향 확인 (새 기능·API·사용법 변경 시 업데이트)
- 용어 변경·도입 → `docs/glossary.yaml` 갱신

## 커밋

```
type: English title
- 한국어 변경 내용 / 변경 이유
```

type: `feat` `fix` `refactor` `docs` `style` `test` `chore`
디버깅 이슈 발생 시: `docs/debug/YYYYMMDD_이슈명.md` 작성 (시각 KST 기준).
