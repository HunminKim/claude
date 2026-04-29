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

- Bash 연속 실패 2회 → `detect_failure_loop.py`가 경고 출력
- 경고 발생 시: 즉시 중단 → 설계 재검토 → 사용자 보고
- 같은 패치 재시도 금지 — 방향을 바꿔라

## 서브에이전트 위임 규칙

- 구현 완료 후 반드시 `@verifier` 호출 (예외 없음)
- 조사·탐색·병렬 분석은 서브에이전트에게 위임 (메인 컨텍스트 보호)
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
