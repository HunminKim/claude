---
description: /approve-plan 의 짧은 별칭. tasks/todo.md 계획을 승인하고 plan-gate를 연다.
allowed-tools: Bash
disable-model-invocation: true
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" approve
```

사용자 전용 커맨드 — Claude가 자율 호출할 수 없다 (disable-model-invocation). 슬래시 없이 `approve` 평문 입력도 동일하게 동작한다.
