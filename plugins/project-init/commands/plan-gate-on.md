---
description: plan-gate를 활성화한다. .claude/plan_gate_enabled 파일을 생성한다.
allowed-tools: Bash
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" on
```

plan-gate는 같은 코드 파일을 Edit/Write/MultiEdit으로 5회 이상 반복 편집하면 자동 차단하고 `tasks/todo.md` 계획 작성을 요구한다. `tasks/todo.md` 계획이 감지되면 `/approve-plan` 명시 승인을 유도하며, 승인 전까지 구현 게이트는 열리지 않는다(자동 승인 안 함 — 사람의 명시 승인 필수).
