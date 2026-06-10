# 260610 하네스 전수 검사 + 수정 리포트 (v1.28.0)

> 2026-06-10 KST. 5개 영역 병렬 정밀 검사 → 발견 결함 18건 전부 수정.
> 모든 수정은 공식 hooks 문서 + 현재 CLI 실측으로 방향을 확정한 뒤 적용했고,
> 수정 후 행위 검증(스모크 테스트 71건)을 통과했다.

## 핵심 교훈

**문제가 많았던 게 아니라, 문제를 발견할 장치가 없었다.**
침묵-실패 훅 시스템(silent exit 0) + 테스트 0개 + 플랫폼 드리프트(CLI 플래그·훅 JSON 스펙 변경)의
조합으로 P0급 고장 6건이 무증상으로 누적돼 있었다. → `tests/smoke_test.py` 신설,
커밋 전 실행을 CLAUDE.md 작업 의례에 추가.

## P0 — 죽어 있던 기능 복구

| # | 결함 | 수정 | 검증 |
|---|------|------|------|
| 1 | install.sh가 존재하지 않는 `--name`/`--yes` 플래그 사용 + `2>/dev/null \|\| echo`로 에러 삼킴 → 7개 플러그인 설치 전부 무음 실패 | CLI 실측 기반 재작성, 에러 캡처, 설치 후 `claude plugin list` 자가 검증 | 실제 실행 — 7개 설치+검증 성공, 멱등성 확인 |
| 2 | plan_gate.py multi-edit 힌트 분기가 카운터 증가 앞에서 early-return → `Edit` 툴에서 반복편집 트리거 영구 사망 | return 제거, advisory 누적 후 흐름 계속 | Edit 5회째 차단 / 상이 파일 7개 무차단 / Write 5회째 차단 |
| 3 | 스캐폴드 누락: settings.json이 참조하는 git_hooks_setup·verifier_sandbox 미생성, @frontend/@backend/@deeplearning 에이전트 미생성 | SKILL.md 트리·생성순서·템플릿 섹션에 훅 6종/에이전트 5종 전부 배선 | 스모크 3중 정합 검사 |
| 4 | update_docs.py — 진행 로그(평문)와 verifier advisory(JSON)가 같은 stdout에 섞여 advisory 파싱 불가 → 환기 무효 | 진행 로그 전부 stderr, advisory JSON stdout 단독. 컨벤션 정비(future annotations·main 가드·exit 1 제거) | stdout 순수 JSON 파싱 + stderr 분리 실측 |
| 5 | 도메인 에이전트 4종의 status 명령이 존재하지 않는 `.claude/plugins/...` 경로 | 프로젝트 로컬 `.claude/state/plan_gate.json` 직접 읽기로 교체 | state 유무 양쪽 실측 |
| 6 | `/skip-verify`가 안내되지만 plan_approval `_ACTION_TOKENS`에 없어 무반응 | 토큰 등록 | approved→done+⏭️ 종단 실측 |

## P1 — 채널·오탐·안내 교정 (공식 문서로 확정 후 적용)

- **공식 스펙 확정 사실**: `systemMessage`=사용자 전용 / Stop 훅은 `hookSpecificOutput.additionalContext` 지원(턴 끝 주입, 대화 계속) / PostCompact는 주입 불가(side-effect 전용) / SessionStart JSON은 `hookSpecificOutput` 래퍼 필수 + matcher `compact` 존재
- plan_gate_stop_alert: systemMessage → additionalContext(Stop). v1.26.1에서 systemMessage로 갔던 것은 당시 판단 — 현행 스펙 재확인 후 환기 채널로 복귀
- plan_gate_session_start: top-level additionalContext(비표준) → hookSpecificOutput 래퍼
- 템플릿 cleanup_suggest: systemMessage → additionalContext(Stop)
- 템플릿 post-compact: **PostCompact → SessionStart(matcher: compact) 이벤트 이전** (PostCompact는 주입 불가가 스펙)
- dangerous_bash_check: `.env.example/.sample/.template/.dist` 허용 (secret_read_guard와 정책 통일, negative lookahead)
- intro_block 거짓 안내 수정: "verifier.md 삭제로 비활성화"(거짓) → `/plan-gate-off` + `.plan-gateignore` 안내
- harness-update 비교표: infra.md(기존 누락!)·git_hooks_setup·verifier_sandbox·skip.md·.plan-gateignore 추가
- 오펀 템플릿 배선: `.plan-gateignore`, `commands/skip.md`, `docs/debug/.gitkeep` 신설
- prompt-log uninstall.sh: `~/.claude-config` 하드코딩 → 스크립트 위치 기준 자동 산출

## P2 — 컨벤션·정밀도

- docstring `출력 채널:` 선언 보강: prompt-log 4종, plan_gate_cli/approval/gc/session_start, ruff_check
- ruff_check ast 경고: plain stderr(무효) → PostToolUse additionalContext JSON (차단 분기에선 생략 — 차단 우선)
- plan_gate_gc: 만료 기준 created_at → closed_at(fallback created_at), 정리 로그 stdout → stderr
- prompt_log_lib: tmp/append 파일을 0600으로 **생성** (기록 후 chmod의 노출 race 제거)
- last_archived_todo_sha: state 재로딩 제거 (호출자 state 전달)
- README: clone 경로 통일, plan-gate 활성화 스위치 사실 교정, 3개 플러그인 업데이트 안내
- CLAUDE.md: 채널 예시 목록 현행화 + PostCompact/SessionEnd 주입 불가 명시 + 스모크 테스트 의례 추가

## 의도적으로 수정하지 않은 것 (Simplicity First)

- detect_failure_loop 동시 세션 카운터 공유: 실사고 증거 없음, 세션 분리 시 정리 훅 필요 — 사고 발생 시 재검토
- tool_counter lost-update: 단일 세션 훅은 직렬 실행, 통계 1건 오차 — 수정 비용 > 효익
- 템플릿 kebab-case 파일명(design-precheck.py, post-compact.py): 참조 파급 대비 효익 없음 — 언급만

## 재발 방지 장치

1. `tests/smoke_test.py` — 훅 stdin 주입 행위 검증 71건 (트리거/채널/정합/버전)
2. install.sh 자가 검증 단계 — 무음 실패 원천 차단
3. CLAUDE.md 작업 의례에 커밋 전 스모크 실행 명문화

- [x] CLAUDE.md 반영 완료
- [x] tests/smoke_test.py 반영 완료
