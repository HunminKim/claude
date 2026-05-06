# CLAUDE.md — {{PROJECT_NAME}}

> 코드로 파악 불가능한 것만 담는다. 코드 규칙 자동 로드: `.claude/rules/code-style.md`
> 세션 시작 시 @.claude/memory/lessons.md 와 @.claude/memory/workflow.md 읽어 복습.
> 각 줄 기준: "없으면 Claude가 실수할까?" — 아니라면 삭제.

## 프로젝트

- **목적**: {{PROJECT_DESCRIPTION}}
- **기술 스택**: {{TECH_STACK}}

## 주요 구조

```
{{PROJECT_STRUCTURE}}
```

## 명령어

```bash
{{BUILD_COMMAND}}
{{TEST_COMMAND}}
{{TEST_SINGLE_COMMAND}}
{{LINT_COMMAND}}
```

## 알려진 버그 / 제약

<!-- 코드로 알 수 없는 핵심 함정만 기록. 예: 특정 라이브러리 버그, 환경 제약, API 제한 -->

## 서브에이전트 전략

메인 Claude는 **오케스트레이터**다. 직접 구현은 최소화하고 전문 에이전트에게 위임한다.

### 메인이 직접 처리 (위임 불필요)
- 단일 파일, 10줄 이하의 간단한 수정
- 설정 파일 값 변경, import 추가, 상수 정의
- 임시 디버깅 코드, scaffold/boilerplate 생성
- 문서 업데이트 (README, 주석)

### 도메인 에이전트에게 위임 (기본 원칙)
```
메인: 요청 분석 → tasks/todo.md 작성 → /approve-plan → 에이전트 위임 → 결과 수신 → @verifier
```

| 작업 유형 | 위임 대상 |
|---------|---------|
| UI 컴포넌트, 상태 관리, 스타일링 | `@frontend` |
| API, DB 스키마, 비즈니스 로직, 보안 | `@backend` |
| 모델, 학습 파이프라인, 데이터 전처리 | `@deeplearning` |
| 구현 완료 후 검증 | `@verifier` |

### 위임 시 메인이 전달할 것
1. 구현할 기능 설명
2. 담당 파일 범위 (건드리면 안 되는 파일 포함)
3. 완료 기준
4. plan-gate가 approved 상태임을 명시

### 제약
- 서브에이전트 하나당 작업 하나만 할당
- `/approve-plan` 없이 구현 위임 금지
- 위임받은 에이전트가 막히면 메인에 보고 (자체 해결 시도 금지)

## 개발 워크플로우

- 코드 수정 전 `docs/technical_doc.md` 및 연관 모듈 먼저 확인 (충돌 방지)
- **plan-gate (자동 강제)**: 동일 파일 반복 편집 3회 이상 OR 영향 파일 6개 이상 OR MultiEdit 항목 5개 이상이면 PreToolUse 훅이 자동 차단한다. `tasks/todo.md` 에 계획 작성 후 사용자가 `/approve-plan` 입력해야 재개. 차단 시점에 `git tag` + `git stash`로 체크포인트 자동 생성. `.claude/plan_gate_enabled` 파일이 있을 때만 동작 (`/plan-gate-on` 으로 활성화, `/plan-gate-off` 로 비활성화).
- 진행이 막히면 즉시 중단 → 계획 재수립 → 사용자 확인 (밀어붙이지 않음)
- 코드 수정 후 사용자 실행 전 반드시 `@verifier` 호출 (예외 없음)
- verifier는 단위 테스트 실행 포함 — 테스트 없이 검증 완료 불가
- verifier 결과 후 사용자에게 결정 토큰 요청: `✅ → /done|/rollback`, `❌ → /retry|/rollback`. 자동 정리·롤백은 하지 않는다.
- 연관 기능 묶음 완료 후 `/compact` 실행 — 소단위마다 하지 않음
- 버그 보고 받으면 묻지 않고 바로 수정 — CI 실패도 능동적으로 처리
- 외부 SDK·컴파일러·변환 툴체인 작업 전: 공식 워크플로우 전체 단계를 먼저 나열·확인
  - 단계 누락 사고 방지 — 중간 변환·서명·검증 단계가 묵음 생략되기 쉬움
  - wrapper 스크립트가 있으면 그것만 사용 (인라인 명령 금지)
  - 모르는 단계는 추측하지 말고 사용자에게 확인
- 사용자 교정 발생 시 `.claude/memory/lessons.md` 업데이트 (세션 시작 시 복습)
- 새 설계 결정·기술 선택 시 `docs/decisions.md`에 D-번호로 기록 (append-only)
- 용어 변경·신규 도입 시 `docs/glossary.yaml` 업데이트
- 명령 해석이 2가지 이상 가능하면: 각 해석을 나열하고 선택 요청
  - 적용 기준: 되돌리기 어려운 작업(삭제·스키마 변경·외부 전송)에 한정
  - 저위험 작업은 가장 보수적인 해석으로 바로 실행
- 새 명령어가 확정되면 이 파일의 명령어 섹션에 즉시 추가 — 일회성·디버깅용은 제외
- 명령어의 코드가 변경·삭제되면 명령어 섹션도 즉시 수정·삭제
- 기능 완료 시 README.md 영향 여부 확인 → 새 기능·API 변경·사용법 변경 시 업데이트

## 커밋

```
type: English title
- 한국어 변경 내용 / 변경 이유
```

type: `feat` `fix` `refactor` `docs` `style` `test` `chore`

## 주의사항

- 디버깅 이슈 발생 시: `docs/debug/YYYYMMDD_이슈명.md` 작성 (시각 KST 기준)
