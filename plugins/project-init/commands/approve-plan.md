---
description: 활성화된 plan-gate를 승인하고 작업을 재개한다. tasks/todo.md의 SHA-256 검증 포함.
allowed-tools: Bash
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" approve
```

이 명령은 `gate.state == "created"` 상태에서만 동작한다. 승인 후 같은 gate에서 추가 Edit이 가능하지만, scope creep 방지를 위해 `max(initial_edit_count + 2, 5)`를 넘으면 다시 차단된다.
