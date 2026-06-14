---
description: plan-gate 스코프 강제를 enforce 로 켠다. 스코프 밖 Edit 은 거부되고, Bash 가 만든 스코프 밖 변경은 체크포인트로 롤백된다. .claude/plan_gate_scope 파일에 enforce 를 기록한다.
allowed-tools: Bash
disable-model-invocation: true
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" scope-enforce
```

tasks/todo.md 의 매니페스트(`<!-- plan-gate: scope BEGIN/END -->`)로 선언한 scope 계약을 실제로 강제한다. layer-1(PreToolUse)은 스코프 밖 Edit/Write 호출을 거부하고, layer-2(PostToolUse Bash)는 git status 로 working tree 를 훑어 스코프 밖 변경을 체크포인트 상태로 되돌린다(존재했던 파일은 복원, 신규 파일은 삭제). plan-gate 운영 파일(tasks/todo.md·.claude/state/**·docs/.verifier_result.json·.plan-gateignore)은 무조건 허용된다. git 저장소에서만 layer-2 롤백이 동작하며, 비-git 은 감지·경고만 한다. 끄려면 `/plan-gate-scope-off`, 롤백 없이 관찰만 하려면 `/plan-gate-scope-shadow`.
