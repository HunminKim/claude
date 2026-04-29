---
description: 현재 plan-gate 체크포인트로 working tree를 복원한다 (git reset --hard).
allowed-tools: Bash
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" rollback
```

체크포인트 tag로 `git reset --hard` 한다. 잃은 dirty 변경은 `[plan-gate]` stash에 보존되어 있어 `git stash list`로 확인 후 필요하면 `git stash pop`으로 복구할 수 있다.
