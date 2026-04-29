---
description: 현재 plan-gate 작업을 완료로 마감하고 체크포인트(tag, stash)를 정리한다.
allowed-tools: Bash
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" done
```

`gate.state` 가 `approved` 또는 `verified` 일 때만 실행 가능하다. 실행 후 `.claude/gate/<gate_id>/clean` tag와 `[plan-gate]` stash 항목이 모두 삭제된다.
