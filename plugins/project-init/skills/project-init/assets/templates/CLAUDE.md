# CLAUDE.md — {{PROJECT_NAME}}

> 이 파일은 매 세션 **항상 로드**된다 — 코드로 알 수 없는 **프로젝트 고유 정보만** 담는다.
> 절차·워크플로우 규칙은 여기 적지 않는다 → `.claude/memory/workflow.md`(과정 규칙) · `.claude/rules/code-style.md`(코드 규칙).
> 세션 시작 시 @.claude/memory/lessons.md 와 @.claude/memory/workflow.md 를 읽어 복습한다.
> 각 줄 기준: "없으면 Claude가 실수할까?" — 아니면 삭제. **100줄 이내 유지.**

## 응답 언어

**항상 한국어로 응답한다.** compact·세션 재개 등으로 영어로 전환되더라도 한국어를 유지한다.

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

<!-- 코드로 알 수 없는 이 프로젝트만의 함정만 기록한다. 예: 특정 라이브러리 버그, GPU·경로 제약, API rate limit, 비직관적 환경 의존.
     일반 하네스 규칙(plan-gate·위임·verifier 등)은 여기 적지 않는다 → .claude/memory/workflow.md 가 단일 진실 원천. -->

## 규칙 분산 구조 (각 항목은 한 곳에만 — 중복 금지)

CLAUDE.md 는 위 "프로젝트 고유 정보"만 갖는다. 아래 내용은 절대 여기 중복 기재하지 않는다 (중복 시 drift·토큰 낭비):

| 레이어 | 파일 | 담는 것 | 로드 시점 |
|--------|------|---------|-----------|
| 과정 규칙 | `.claude/memory/workflow.md` | 서브에이전트 위임 · plan-gate · verifier 흐름 · /compact · 버그 보고 절차 · 커밋 형식 · TDD/Phase gate | 세션 시작 @참조 |
| 코드 규칙 | `.claude/rules/code-style.md` | 외과적 변경 · 단순성 우선 · 인덱스 매핑 · 구현 전 확인(코드) · ML 도메인 | 코드 파일 편집 시 |
| 교정 패턴 | `.claude/memory/lessons.md` | 반복 실수 방지 패턴 | 세션 시작 복습 |
| 제약 SSOT | `.claude/constraints.yaml` | 의존성·아키텍처 제약 · 임시파일 네이밍 | 도구/검증 시 |

> plan-gate · 위임 · verifier 사용법이 필요하면 `workflow.md` 를 본다. 이 파일에 옮겨 적지 않는다.
