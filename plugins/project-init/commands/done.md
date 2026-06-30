---
description: 작업을 완료로 마감한다. 체크포인트를 정리하고 gate를 종료한다.
allowed-tools: Bash
disable-model-invocation: true
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" done
```

사용자 전용 커맨드 — Claude가 자율 호출할 수 없다 (disable-model-invocation). 슬래시 없이 `done` 평문 입력도 동일하게 동작한다.

> **마감 4종 구분**: `/done` 정상 완료 — `created`(검증 불필요)는 무조건 마감. `approved`/`verified`는 verifier ✅ 판정(또는 `docs/.verifier_result.json` 복구 가능한 판정)이 있어야 마감, 없으면 "verifier 미검증 — 완료 불가" 로 거부 · `/skip`(=`/keep`) verifier ❌ 를 인지한 채 변경 보존 마감 · `/skip-verify` verifier 판정 *전* 검증 자체를 생략하고 마감.
