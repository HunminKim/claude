---
description: tasks/todo.md를 갱신하고 같은 체크포인트로 재계획한다. 카운터는 리셋된다.
allowed-tools: Bash
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" replan
```

gate를 `created` 상태로 되돌리고 모든 카운터를 0으로 리셋한다. 체크포인트(tag, stash)는 유지된다. 사용자는 새 todo.md를 작성한 뒤 다시 `/approve-plan` 을 입력해 작업을 재개해야 한다.
