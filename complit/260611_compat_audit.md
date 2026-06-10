# 260611 현행 Claude Code 호환성 감사 + prompt-log 데이터 기반 개선 (v1.30.0)

> 발단: prompt-log 3개월 실데이터(1,101 prompts) 분석에서 `multi_edit=0`·`task=0`·
> `is_token=0/98` 발견 → "하네스가 현행 Claude Code(v2.1.170)와 어긋난 곳 전수 검토"로 확대.
> claude-code-guide 에이전트와 공식 문서(hooks/tools-reference/sub-agents/permissions) 대조.

## 확정된 플랫폼 변화 (공식 문서 기준)

| 변화 | 근거 |
|---|---|
| `Task` 툴 → `Agent` 개명 (v2.1.63) | sub-agents.md 명시. `Task(...)` 별칭은 권한 규칙·에이전트 정의용 — **hooks matcher는 별도 경로로 tool_name 정확일치** |
| `MultiEdit` 툴 소멸 | tools-reference.md 툴 표에 부재. 대체: Edit 다회 / `replace_all` |
| PermissionRequest 출력 스키마 | `hookSpecificOutput.decision.behavior` (top-level `permissionDecision` 은 PreToolUse 전용) |
| 에이전트 frontmatter `model` 별칭 | `sonnet`/`opus`/`haiku` 별칭 권장 (특정 마이너 고정 지양) |
| 수동 멘션 형태 | `@agent-<name>` (bare `@name` 은 인터랙티브 피커 경유만) |
| `/agents` 로 만든 에이전트는 즉시 적용 | 파일 직접 생성만 재시작 필요 |

## BROKEN 수정 (조용히 죽어 있던 것)

1. **위임 가드 사망**: `delegation_prompt_check` matcher `"Task"` → 현행 tool_name 은 `Agent` 라 발화 0.
   prompt-log 실데이터의 `task=0` 이 그 증거(위임이 없던 게 아니라 관측이 불가했음).
   → matcher `"Agent|Task"` + 내부 판정 양쪽 이름 (구버전 호환). 행위 검증: Agent 차단/허용/Plan 통과.
2. **PermissionRequest 스키마**: top-level `permissionDecision` 출력 → 무효.
   v1.26.3 에서 top-level 로 갔던 결정은 당시 기준 — 현행 스펙은 nested `decision.behavior`.
   → `hookSpecificOutput.decision.behavior: allow` 로 교정. ※ 실제 권한 다이얼로그 경유는
   라이브 스캐폴드에서 행위 재확인 권장 (이 환경에선 stdin 모의만 가능했음).

## prompt-log 1.1.0 (실데이터가 증명한 수집 사각지대)

- **토큰 정규화**: 구 `PL_TOKEN_SET`(슬래시 정확일치 5종)은 실데이터 98건 중 **0건 인식**.
  → `pl_normalize_token()` — 평문/슬래시/`project-init:` 네임스페이스 3형태 흡수,
  `PL_TOKEN_VALUES` 를 `plan_approval._ACTION_TOKENS` 와 동기 (smoke 가 집합 일치 강제).
- **agent 버킷 신설**: matcher 에 `Agent` 추가. task 버킷과 분리해 신구 데이터 구분.
- **/monitor 제안 룰**: 실데이터의 "살아있니?" 핑 10건 → 템플릿 CLAUDE.md 에
  장시간 작업 시작 시 /monitor 선제안 룰 추가.

## stale 정리

- MultiEdit prose 전부 교체 (plan_gate 힌트·code-style·템플릿 CLAUDE.md·SKILL.md·lessons.md)
  — **matcher 의 `MultiEdit` 토큰은 의도적 유지** (정확일치라 무해 + 구버전 CLI 사용자 호환)
- `Task(subagent_type=...)` 표기 → `Agent` 툴 표기 (템플릿 CLAUDE.md/workflow.md/lessons.md)
- 에이전트 frontmatter: `tools` 에서 MultiEdit 제거, `model: claude-sonnet-4-6` → `sonnet`
- `Read(**)` → `Read` (관용형), 재시작 안내에 `/agents` 즉시 적용 예외 + `@agent-verifier` 표기

## 재발 방지

- smoke `[11] 플랫폼 호환성` 13건: matcher Agent 포함, 위임 가드 행위, 토큰 집합 동기,
  정규화 3형태, 버킷 매핑, **죽은 툴 prose grep** (이 검사가 작성 직후 lessons.md 잔존 1건을 실제로 잡음)
- CLAUDE.md 훅 컨벤션에 matcher 정확일치 함정 명문화

## 미해결 / 관찰

- PermissionRequest 라이브 행위 검증 1회 필요 (project-init 실행으로)
- 교정성 prompt 비율 (5월 1.2% → 6월 4.7%) — 작업 강도 교란변수, 다음 분석에서 재평가
- created 상태로 죽는 게이트 35% — 토큰 데이터 쌓인 뒤 임계 재검토

- [x] CLAUDE.md 반영 완료 (matcher 컨벤션)
- [x] tests/smoke_test.py 반영 완료 ([11] 플랫폼 호환성)
