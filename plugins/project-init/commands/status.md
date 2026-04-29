---
description: 현재 plan-gate 상태를 조회한다. gate id, state, edit 횟수, 체크포인트 정보를 출력한다.
allowed-tools: Bash
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" status
```

plan-gate가 활성화된 프로젝트에서 현재 gate 상태(created/approved/verified/done)와 edit 카운터, 체크포인트 tag/stash 정보를 출력한다.
