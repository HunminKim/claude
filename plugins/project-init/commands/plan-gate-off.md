---
description: plan-gate를 비활성화한다. .claude/plan_gate_enabled 파일을 삭제한다.
allowed-tools: Bash
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" off
```

비활성화 후 Edit/Write/MultiEdit 제한이 해제된다. 공식 Plan Mode만 사용하거나 plan-gate 없이 작업할 때 사용한다. 재활성화하려면 `/plan-gate-on`을 실행한다.
