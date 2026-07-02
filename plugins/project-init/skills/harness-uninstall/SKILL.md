---
name: harness-uninstall
description: project-init 이 설치한 하네스(훅·에이전트·plan-gate 런타임·git hooks)를 프로젝트에서 제거한다. CLAUDE.md·docs 등 문서는 유지한다. "/harness-uninstall", "하네스 제거", "하네스 언인스톨", "훅 다 지워줘", "plan-gate 완전히 제거" 등의 요청에 이 스킬을 사용한다.
disable-model-invocation: true
---

# Harness Uninstall Skill

project-init 이 설치한 **하네스(실행 장치)만** 프로젝트에서 제거한다.
**문서는 남긴다** — CLAUDE.md, docs/, tasks/, memory/lessons 등 프로젝트 지식은 보존.

## 삭제 / 보존 기준

| 구분 | 대상 | 처리 |
|------|------|------|
| 🗑 하네스 훅 | `.claude/hooks/` 의 6종: `time_context.py` `design-precheck.py` `post-compact.py` `cleanup_suggest.py` `git_hooks_setup.py` `verifier_sandbox.py` | 삭제 |
| 🗑 하네스 에이전트 | `.claude/agents/` 의 6종: `verifier.md` `frontend.md` `backend.md` `deeplearning.md` `llm-agent.md` `infra.md` | 삭제 |
| 🗑 git hooks | `.githooks/` 의 3종: `pre-commit`·`pre-push`·`post-checkout` + `git config --unset core.hooksPath` | 삭제 |
| 🗑 하네스 스크립트 | `scripts/validate_arch.py` | 삭제 |
| 🗑 plan-gate 런타임 | `.claude/plan_gate_enabled`·`plan_gate_scope`·`plan_gate_no_git`, `.claude/state/` 하위 plan-gate 파일(`plan_gate.json`·`checkpoints/`·`plan_gate_audit.log`·`plan_gate_intro_seen.flag`), `.plan-gateignore`, git 프라이빗 ref `refs/plan-gate/*` | 삭제 |
| ✂️ 훅 배선 | `.claude/settings.json` 의 hooks 항목 중 **위에서 삭제한 파일을 가리키는 배선만** 제거 | 부분 수정 |
| 🔒 문서 | `CLAUDE.md`, `README.md`, `docs/`, `tasks/`, `.claude/memory/`(lessons·workflow), `.claude/rules/` | 보존 |
| 🔒 사용자 추가분 | 프로젝트가 직접 추가한 훅·에이전트·커맨드 (위 목록에 없는 파일) | 보존 |
| ➖ prompt-log | 동의 marker·수집 데이터 | 건드리지 않음 — `/del_prompt_log` 안내 |

## 실행 순서

### 1단계: 대상 확인

`.claude/agents/verifier.md` 또는 `.claude/plan_gate_enabled` 가 없으면
"project-init 하네스가 없는 프로젝트입니다" 안내 후 중단.

### 2단계: 삭제 목록 수집 (아직 지우지 않는다)

위 표의 🗑/✂️ 항목을 실제 존재 여부 기준으로 나열한다.
- `.claude/hooks/`·`.claude/agents/`·`.githooks/` 에서 **표의 이름과 정확히 일치하는
  파일만** 대상 — 그 외 파일은 사용자 추가분으로 보존 목록에 표기.
  하네스 파일 삭제 후 **비게 된 디렉토리**(`.githooks/`·`.claude/state/` 등)는 함께
  제거하고, 사용자 파일이 남아 있으면 디렉토리를 유지한다
- `.claude/settings.json` 은 삭제 대상 훅을 가리키는 hooks 배선만 골라낸다
  (env·permissions·사용자 훅 배선은 보존). 배선 제거 후 남는 내용이 없으면
  파일 삭제 여부를 3단계 질문에 포함
- git 저장소면 `git for-each-ref refs/plan-gate/` 로 잔존 체크포인트 ref 나열

### 3단계: 목록 보고 + 확인

삭제/수정/보존 목록을 보여주고 **AskUserQuestion 툴**로 확인한다:
- 질문: "위 하네스 구성요소를 제거할까요? (문서는 보존됩니다)"
- 옵션: `["제거 진행 (Recommended)", "취소"]`

⚠️ 함께 안내할 것:
- `.claude/memory/workflow.md` 는 보존되지만 내용이 제거된 하네스(plan-gate 토큰·
  @verifier 위임)를 지시하므로 **죽은 지시**가 된다 — 원하면 별도 요청으로 정리 가능
- plan-gate·위험명령 가드 등 **플러그인 전역 훅**은 이 프로젝트의
  `plan_gate_enabled` 삭제로 비활성화된다. 플러그인 자체를 없애려면:
  `claude plugins uninstall project-init` (모든 프로젝트에 영향)

### 4단계: 제거 실행

2단계 목록 순서대로 삭제·수정한다. `git config --unset core.hooksPath || true`
(키 부재 시 exit 5 — 재실행 멱등성 확보)와 `refs/plan-gate/*` ref 삭제
(`git update-ref -d`)도 수행한다.

### 5단계: 완료 보고

```
## 하네스 제거 완료

삭제됨 (N개): .claude/hooks/… , .claude/agents/… , .githooks/, …
수정됨: .claude/settings.json (하네스 배선 M건 제거)
보존됨: CLAUDE.md · docs/ · tasks/ · .claude/memory/ · .claude/rules/ · 사용자 추가 훅 K개

다시 설치하려면: /project-init (기존 문서는 선별 단계에서 보존됩니다)
```

## 주의사항

- 목록 확인(3단계) 없이 삭제하지 않는다 — "그냥 다 지워"라고 해도 목록은 보여준다
- 진행 중인 plan-gate 게이트(approved·verified)가 있으면 목록 단계에서 경고한다 —
  체크포인트가 함께 삭제되어 /rollback 이 불가능해진다
- 이 스킬은 **이 프로젝트만** 건드린다 — 전역 플러그인·다른 프로젝트는 영향 없음
