# CLAUDE.md — {{PROJECT_NAME}}

> 코드로 파악 불가능한 것만 담는다. 상세 코드 규칙: @docs/code_rules.md
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
<!-- 없으면 이 섹션 삭제 -->

## 서브에이전트 전략

- 조사·탐색·병렬 분석은 서브에이전트에게 위임 (메인 컨텍스트 보호)
- 서브에이전트 하나당 작업 하나만 할당
- 복잡한 문제는 서브에이전트로 컴퓨팅 분산

## 개발 워크플로우

- 코드 수정 전 `docs/technical_doc.md` 및 연관 모듈 먼저 확인 (충돌 방지)
- 3단계 이상 작업: `tasks/todo.md`에 계획 작성 → 사용자 승인 → 실행
- 진행이 막히면 즉시 중단 → 계획 재수립 → 사용자 확인 (밀어붙이지 않음)
- 소단위(함수·단일 책임 기능 단위) 완료마다 `@verifier` 호출 (예외 없음)
- 연관 기능 묶음 완료 후 `/compact` 실행 — 소단위마다 하지 않음
- 버그 보고 받으면 묻지 않고 바로 수정 — CI 실패도 능동적으로 처리
- 사용자 교정 발생 시 `tasks/lessons.md` 업데이트 (세션 시작 시 복습)

## 커밋

```
type: English title
- 한국어 변경 내용 / 변경 이유
```

type: `feat` `fix` `refactor` `docs` `style` `test` `chore`

## 주의사항

- 디버깅 이슈 발생 시: `docs/debug/YYYYMMDD_이슈명.md` 작성 (시각 KST 기준)
