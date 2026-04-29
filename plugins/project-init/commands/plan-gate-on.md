---
description: plan-gate를 활성화한다. .claude/plan_gate_enabled 파일을 생성한다.
allowed-tools: Bash
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" on
```

plan-gate는 Edit/Write/MultiEdit이 임계값(3회 또는 3파일)을 초과하면 자동 차단하고 `tasks/todo.md` 계획 작성을 요구한다. 공식 Plan Mode와 함께 사용할 때는 `/approve-plan` 없이 `tasks/todo.md`가 존재하면 자동 승인된다.
