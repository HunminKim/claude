---
description: plan-gate 체크포인트를 git(tag/stash) 백엔드로 되돌린다. .claude/plan_gate_no_git 파일을 삭제한다.
allowed-tools: Bash
disable-model-invocation: true
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py" use-git
```

`/plan-gate-no-git` 으로 켰던 cp 스냅샷 모드를 해제하고 기본 git 체크포인트(tag/stash)로 복귀한다. 루트가 git repo 일 때만 git 백엔드가 실제로 동작하며, git repo 가 아니면 자동으로 cp 스냅샷이 계속 쓰인다.
