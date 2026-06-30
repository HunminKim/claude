---
description: verifier 판정 전, 검증 없이 gate를 마감한다 (⏭️ 기록. 판정 후엔 사용 불가).
allowed-tools: Bash
disable-model-invocation: true
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" skip-verify
```

사용자 전용 커맨드 — Claude가 자율 호출할 수 없다 (disable-model-invocation). 슬래시 없이 `skip-verify` 평문 입력도 동일하게 동작한다.

> **`/skip` 과 차이**: `/skip-verify` 는 verifier 판정 *전*(approved/verified) 검증 자체를 생략하고 마감 — ✅/❌ 판정이 난 뒤엔 거부된다. 판정 ❌ 를 인지하고 보존 마감하려면 `/skip`.
