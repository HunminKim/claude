---
description: verifier ❌ 후 같은 체크포인트에서 재시도한다. 체크포인트는 유지된다.
allowed-tools: Bash
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" retry
```

`gate.state == "verified"` 이고 `verifier_status == "❌"` 일 때만 사용한다. `edit_count_post_approval` 카운터는 누적 유지되어 무한 retry로 인한 scope creep을 막는다.
