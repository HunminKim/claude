---
description: verifier ❌ 후 현재 변경사항을 그대로 보존하며 gate를 마감한다. /done과 동일하게 동작한다. /keep도 같은 효과다.
allowed-tools: Bash
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" skip
```

`gate.state == "verified"` 이고 `verifier_status == "❌"` 일 때 사용한다. 검증 실패를 인정하고 현재 변경사항을 유지한 채 gate를 닫는다. 체크포인트(tag, stash)는 정리된다.

`/done` 과 동일하다 — 검증 실패 상태에서의 완료 처리임을 명시적으로 표현할 때 사용한다.
