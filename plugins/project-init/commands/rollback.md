---
description: 체크포인트로 전체를 되돌린다 (git reset → clean tag, stash 복원 안내).
allowed-tools: Bash
disable-model-invocation: true
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" rollback
```

사용자 전용 커맨드 — Claude가 자율 호출할 수 없다 (disable-model-invocation). 슬래시 없이 `rollback` 평문 입력도 동일하게 동작한다.
