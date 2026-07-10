---
description: tasks/todo.md 계획을 승인하고 plan-gate를 연다 (gate → approved).
allowed-tools: Bash
disable-model-invocation: true
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" approve
```

사용자 전용 커맨드 — Claude가 자율 호출할 수 없다 (disable-model-invocation). 슬래시 없이 `approve-plan` 평문 입력도 동일하게 동작한다 (UserPromptSubmit 훅 fallback).

출력이 `승인 완료` 또는 `선승인 완료` 이면, 다른 도구를 쓰기 전에 TodoWrite 를 한 번 호출해 승인된 `tasks/todo.md` 의 작업 항목을 그대로 옮긴다. 승인이 거부됐거나(종료코드 1) `이미 승인됨` 이면 호출하지 않는다 — 진행 중인 목록을 pending 으로 되돌리지 않기 위해서다.

`tasks/todo.md` 가 단일 진실 원천이고 TodoWrite 는 표시용 사본이다. 항목을 완료로 표시해도 gate 는 닫히지 않는다 — gate 는 verifier ✅ 이후 `/done` 으로만 닫힌다.
