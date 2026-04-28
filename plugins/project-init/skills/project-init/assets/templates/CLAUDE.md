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

## 개발 워크플로우

- 코드 수정 전 `docs/technical_doc.md` 및 연관 모듈 먼저 확인 (충돌 방지)
- 여러 파일 수정 시: 계획 → 사용자 승인 → 순차 실행
- 소단위(함수·단일 책임 기능 단위) 완료마다 `@verifier` 호출 (예외 없음)
- 연관 기능 묶음 완료 후 `/compact` 실행 — 소단위마다 하지 않음
- 사용자 완료 사인 후 `docs/technical_doc.md` 업데이트

## 커밋

```
type: English title
- 한국어 변경 내용 / 변경 이유
```

type: `feat` `fix` `refactor` `docs` `style` `test` `chore`

## 주의사항

- 디버깅 이슈 발생 시: `docs/debug/YYYYMMDD_이슈명.md` 작성 (시각 KST 기준)
