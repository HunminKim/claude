---
description: verifier ❌ 후 같은 체크포인트에서 재구현을 시작한다 (approved 복귀).
allowed-tools: Bash
disable-model-invocation: true
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" retry
```

사용자 전용 커맨드 — Claude가 자율 호출할 수 없다 (disable-model-invocation). 슬래시 없이 `retry` 평문 입력도 동일하게 동작한다.
