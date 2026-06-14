---
description: 승인된 plan-gate 스코프에 파일 패턴을 audit 남기며 추가한다(확장 escape-hatch). enforce 중 예상 밖 인접 파일을 전면 /replan 없이 진행해야 할 때 Claude/사용자가 호출한다. do-not-touch 는 확장으로도 허용되지 않는다.
allowed-tools: Bash
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" subplan $ARGUMENTS
```

스코프 강제(enforce)가 켜진 상태에서 작업 중 **승인 스코프 밖의 인접 파일**을 건드려야 한다는 걸 발견했을 때 사용한다. 전면 `/replan`(카운터 리셋 + 사용자 재승인 필요)을 하지 않고도 그 파일 패턴을 현재 게이트 스코프에 추가해 진행할 수 있다. 추가된 확장은 `.claude/state/plan_gate_audit.log` 에 `subplan_expand` 로 기록되어 사용자가 최종 diff 와 함께 검토한다.

- 사용: `/subplan src/util/**` (여러 개: `/subplan a/** b.py`)
- `do-not-touch` 로 선언된 경로는 확장으로도 허용되지 않는다 (deny-first).
- 계획을 새로 짜면(`/replan`) 확장은 초기화된다.
- 인자 없이 호출하면 현재 확장 목록을 표시한다.

이 커맨드는 `disable-model-invocation` 을 두지 않는다 — Claude 가 위임/작업 중 자율적으로 확장을 선언할 수 있어야 escape-hatch 로 기능하기 때문이다. 남용 방지는 audit 기록 + 최종 diff·verifier·사람 검토가 담당한다.
