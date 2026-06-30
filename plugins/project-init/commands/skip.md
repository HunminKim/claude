---
description: verifier ❌ 후 현재 변경을 보존하며 gate를 마감한다 (/keep 동일).
allowed-tools: Bash
disable-model-invocation: true
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" skip
```

사용자 전용 커맨드 — Claude가 자율 호출할 수 없다 (disable-model-invocation). 슬래시 없이 `skip` 또는 `keep` 평문 입력도 동일하게 동작한다.

> **`/done`·`/skip-verify` 와 차이**: `/skip` 은 verifier ❌ 상태에서만 — 실패를 인지한 채 변경을 보존하고 다음 주기로 넘긴다. 정상 완료는 `/done`, 판정 *전* 검증 자체를 건너뛰려면 `/skip-verify`.
