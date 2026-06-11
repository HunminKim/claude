---
description: /skip 의 별칭. verifier ❌ 후 현재 변경을 보존하며 gate를 마감한다.
allowed-tools: Bash
disable-model-invocation: true
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" skip
```

사용자 전용 커맨드 — Claude가 자율 호출할 수 없다 (disable-model-invocation). 슬래시 없이 `keep` 또는 `skip` 평문 입력도 동일하게 동작한다.
