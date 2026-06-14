---
description: plan-gate 스코프 강제를 shadow 로 켠다. 스코프 위반을 감지·기록만 하고 차단·롤백은 하지 않는다(롤아웃 관찰용). .claude/plan_gate_scope 파일에 shadow 를 기록한다.
allowed-tools: Bash
disable-model-invocation: true
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" scope-shadow
```

enforce 로 바로 켜기 전에, 스코프 계약이 실제 작업과 얼마나 충돌하는지 먼저 관찰하는 모드다. 스코프 밖 Edit/Bash 변경을 감지해 `.claude/state/plan_gate_audit.log` 에 `scope_violation_shadow`·`scope_deny_shadow` 로 기록하고 Claude 에 환기만 한다 — 편집을 거부하거나 파일을 롤백하지 않는다. audit log 가 "데이터가 깨끗한가"를 보여주면 `/plan-gate-scope-enforce` 로 승격한다. 끄려면 `/plan-gate-scope-off`.
