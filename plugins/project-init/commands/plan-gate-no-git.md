---
description: plan-gate 체크포인트를 git(프라이빗 ref 스냅샷) 대신 cp 파일 스냅샷으로 전환한다. .claude/plan_gate_no_git 파일을 생성한다.
allowed-tools: Bash
disable-model-invocation: true
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" no-git
```

git repo 이지만 plan-gate 가 git 에 스냅샷(프라이빗 ref)을 만들지 않기를 원할 때 사용한다 (별도 VCS 워크플로우와 충돌 회피, git 추적 비선호 등). 전환 후 체크포인트는 편집 직전 파일 원본을 `.claude/state/checkpoints/<gate_id>/` 에 복사하는 방식으로 만들어지고, `/rollback` 은 그 스냅샷에서 원본 복원·신규 파일 삭제로 동작한다. git 체크포인트로 되돌리려면 `/plan-gate-use-git` 을 실행한다.
