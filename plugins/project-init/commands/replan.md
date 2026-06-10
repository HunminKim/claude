---
description: 계획을 재작성한다. 편집 카운터를 리셋하고 체크포인트는 유지한다.
allowed-tools: Bash
disable-model-invocation: true
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" replan
```

사용자 전용 커맨드 — Claude가 자율 호출할 수 없다 (disable-model-invocation). 슬래시 없이 `replan` 평문 입력도 동일하게 동작한다.
