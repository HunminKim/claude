---
description: plan-gate 스코프 강제를 완전히 끈다(환기조차 없음). 매니페스트는 기록만 하고 스코프 위반을 차단·롤백·환기하지 않는다. .claude/plan_gate_scope 파일에 'off' 를 기록한다(기본값은 shadow 이므로 명시 off).
allowed-tools: Bash
disable-model-invocation: true
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" scope-off
```

스코프 강제를 완전히 끈다(기본값 shadow 의 환기조차 끄는 명시 선택). tasks/todo.md 의 scope 매니페스트는 여전히 파싱·저장되지만(/status 로 확인 가능) 편집을 거부하거나 Bash 변경을 롤백하지 않고, 위반 환기도 하지 않는다. 같은 파일 반복 편집(thrash) 가드는 강제 모드와 무관하게 계속 동작한다. 다시 켜려면 `/plan-gate-scope-shadow`(기본·관찰) 또는 `/plan-gate-scope-enforce`(강제).
