# Plan-Gate Untracked Stash Phantom Deletion

- **사건 ID**: PGSD-2026-05-11
- **발견 시각**: 2026-05-11 (KST)
- **영향 범위**: untracked 파일 103개 일시 사라짐 (`docker-compose.yml`, `docs/checklist.md`, `docs/completion_report.md`, `docs/technical_doc.md`, `.claude/agents/*`, `tasks/todo.md` 등)
- **데이터 손실**: 0건 (전량 `stash@{0}` 내부에 보존됨)
- **판정**: ❌ 하네스 수정 필요

---

## 1. 현상 (Symptom)

작업 세션 도중 사용자 의식 없이 다수의 untracked 파일이 working tree에서 **흔적 없이 사라진 것처럼** 보였다.

| 관찰 사실 | 값 |
|---------|------|
| 사용자가 의도적으로 실행한 삭제 명령 | 없음 |
| 메인 Claude의 `rm`/`mv`/`unlink` Bash 호출 | 0건 (transcript grep으로 확정) |
| 서브에이전트(sidechain)의 위험 Bash 호출 | 0건 |
| `Write` 도구로 빈 내용 덮어쓰기 | 0건 |
| 호스트(`~/project/hunmin/llm`)에서도 동시에 부재 | 동일 |
| 컨테이너(`/workspace`)에서도 동시에 부재 | 동일 |
| `git status` 결과 | 사라진 파일들이 untracked(`??`) 목록에서 사라짐 |
| `git stash list` | `stash@{0}: plan-gate_1778207468476_f543eb` 1건 존재 |
| stash 내부 파일 수 | 103개 (사라졌다고 보고된 파일 전원 포함) |

사용자 체감: "파일이 그냥 사라졌다." 실제: stash로 이동되었다.

---

## 2. 원인 (Root Cause)

### 2-A. 코드 위치

**파일**: `/root/.claude/plugins/marketplaces/hunminkim/plugins/project-init/hooks/plan_gate_lib.py`

```python
# Line 166~176 (대략)
def stash_dirty(root: Path, msg: str) -> str | None:
    ...
    # Line 172
    r = _git(root, "stash", "push", "-u", "-m", msg)
    ...
```

`/root/.claude/plugins/marketplaces/hunminkim/plugins/project-init/hooks/plan_gate.py:164-168` 의 trigger 로직이 임계값 초과 시 위 `stash_dirty()` 를 호출한다.

### 2-B. 메커니즘

`git stash push -u` 의 `-u` 플래그는 **untracked 파일까지 stash에 포함**시킨다. 이때 stash 동작의 정의상:

1. tracked-modified 파일은 **HEAD 상태로 복원** + stash 안에 변경분 보존
2. untracked 파일은 **working tree에서 제거** + stash 안에 보존

→ 사용자는 (2)의 절반(보존)은 못 보고 절반(working tree 제거)만 본다.

### 2-C. trigger 임계값

`plan_gate.json` 시점 데이터:
- `edit_count: 34` (3회 이상)
- `unique_files: 16` (6개 이상)
- `state: created`
- `last_edit_ts: 2026-05-11T00:26:25.931750+00:00`

stash commit time `2026-05-11 00:26:25 UTC` 와 0.93초 차이 → **동일 사건**으로 확정.

### 2-D. 하네스 보호 장치 부재

진단 결과 다음 보호 장치들이 모두 부재한다:

| 보호 장치 | 상태 | 영향 |
|---------|------|------|
| stash 부작용 명시적 사용자 알림 | ❌ | 사용자가 "파일 사라짐" 공포 체감 |
| 핵심 환경 파일 (`docker-compose.yml`, `.env`, `Dockerfile`) 보호 목록 | ❌ | 운영 핵심 파일도 무차별 stash |
| Bash PreToolUse 위험 명령 차단 | ❌ | 별개 경로의 `rm` 도 차단 안 됨 |
| file write/delete audit 로깅 | ❌ | 사후 추적 어려움 |
| `.env` `.gitignore` 제외 (또는 별도 백업) | ❌ | git history 백업 불가 — stash drop 시 영구 손실 |

---

## 3. 조치 방안 (Action Plan)

### 3-1. 즉시 조치 — 사용자 (5분)

```bash
cd ~/project/hunmin/llm
git stash list
git stash show stash@{0} --include-untracked --name-only | head
git stash pop stash@{0}
git status --short | head -20
```

**기대 결과**: 103개 untracked 파일 + `.env` modified 상태가 working tree로 복원됨.

### 3-2. 단기 조치 — 본 프로젝트 (CLAUDE.md / lessons.md)

이번 사건을 **반복 방지 지식**으로 박는다.

#### (a) `CLAUDE.md` 의 "알려진 버그 / 제약" 섹션에 추가
```markdown
- **plan-gate 자동 stash 부작용**: `git stash push -u` 로 untracked 파일이 working tree에서 제거된다(보존은 stash 안). 차단 시 사용자는 `git stash pop stash@{0}` 으로 즉시 복원 가능. 핵심 운영 파일(`docker-compose.yml`, `.env`, `Dockerfile`, `requirements.txt`)이 untracked 상태이면 미리 staging 또는 별도 백업 권장.
```

#### (b) `.claude/memory/lessons.md` "하네스 관련 패턴" 섹션에 추가
| 날짜 (KST) | 상황 유형 | 발생한 문제 | 추가한 하네스 | 효과 |
|-----------|----------|------------|--------------|------|
| 2026-05-11 | plan-gate 차단 시점 untracked 흡수 | docker-compose.yml 등 103개 파일 working tree에서 사라진 듯 보임 | (3-3 항목 적용 후 갱신) | (적용 후 측정) |

### 3-3. 중기 조치 — Upstream 하네스 패치 (claude_skills repo)

> 본 프로젝트 내 즉시 적용 가능한 변경은 (a), (b). 본 plugin 코드를 호스트에서 직접 패치하면 다른 사용자에게도 적용된다.

#### (a) `plan_gate.py` trigger 메시지에 stash 복구 가이드 추가 (심각도: 높음)

**파일**: `plugins/project-init/hooks/plan_gate.py` (line 164~180 부근, trigger 출력)

**현재**: stash 생성 사실만 알리고 끝
**수정 후**: 다음 텍스트를 trigger 출력에 포함

```
⚠️  plan-gate가 working tree를 stash로 자동 백업했습니다.
    untracked 파일 {N}개 + modified 파일 {M}개가 stash@{0} 에 보관됨.
    복원: git stash pop stash@{0}
    상세 확인: git stash show stash@{0} --include-untracked --name-only
```

**이유**: 사용자가 "사라졌다" 공포 체감의 90%는 해결됨.

#### (b) `plan_gate_lib.py` 에 핵심 파일 보호 목록 옵션 추가 (심각도: 중)

**파일**: `plugins/project-init/hooks/plan_gate_lib.py` (`stash_dirty()` 부근)

**현재**:
```python
def stash_dirty(root: Path, msg: str) -> str | None:
    ...
    r = _git(root, "stash", "push", "-u", "-m", msg)
```

**수정 후**:
```python
PROTECTED_PATTERNS = [
    "docker-compose*.yml", "Dockerfile*", ".env", ".env.*",
    "requirements.txt", "pyproject.toml", "Makefile",
]

def stash_dirty(root: Path, msg: str, protect_patterns: list[str] = None) -> str | None:
    """working tree를 stash 한다. PROTECTED_PATTERNS 매칭 파일은 stash 대상에서 제외."""
    patterns = protect_patterns or PROTECTED_PATTERNS
    # ① 보호 대상 파일을 임시로 .git/plan_gate_protected/ 로 이동 (또는 add intent-to-add)
    # ② git stash push -u 실행
    # ③ 보호 파일을 working tree로 복귀
    ...
```

**이유**: 운영 핵심 파일은 stash 대상에서 항상 제외되어야 한다. plan-gate가 모르는 동안 사라질 위험을 원천 차단.

**대안 (더 단순)**: 보호 파일이 untracked인 경우 plan-gate가 차단 직전에 `git add` 로 staging만 해두고 stash는 그대로 진행. staging된 파일은 stash 후에도 working tree에 남는다.

#### (c) Bash PreToolUse 위험 명령 차단 hook 추가 (심각도: 중)

**파일**: `plugins/project-init/hooks.json`

**현재**: PreToolUse matcher가 `Edit|Write|MultiEdit` 만. Bash는 PostToolUse `detect_failure_loop.py` 만 매칭.

**수정 후**:
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 $CLAUDE_PROJECT_DIR/.claude/hooks/dangerous_bash_check.py"
          }
        ]
      },
      ...
    ]
  }
}
```

**신규 스크립트** `plugins/project-init/hooks/dangerous_bash_check.py`:
- `rm -rf /`, `rm -rf /workspace`, `find / -delete` 등 패턴 차단
- 핵심 파일 직접 `rm`/`mv`/`unlink` 시 확인 요청
- 통과 시 exit 0, 차단 시 exit 2 + 사유 출력

#### (d) plan-gate audit log (심각도: 중)

**파일**: `plugins/project-init/hooks/plan_gate_lib.py`

**수정**: trigger / stash / pop 모든 액션을 `.claude/state/plan_gate_audit.log` 에 JSON Lines 로 추가 기록.

```python
def log_audit(root: Path, action: str, **kwargs):
    audit_path = root / ".claude/state/plan_gate_audit.log"
    entry = {"ts": now_iso(), "action": action, **kwargs}
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
```

**효과**: 향후 사후 추적 시 `cat .claude/state/plan_gate_audit.log | jq` 로 즉시 어떤 파일이 언제 stash됐는지 확인 가능.

### 3-4. 장기 조치 — 정책 변경 (검토 항목)

| 항목 | 현재 | 검토 방향 |
|------|------|----------|
| `.env` `.gitignore` 등재 | 등재 | `.env.example` 만 git 추적 + `.env` 는 별도 plan-gate 보호 목록 |
| plan-gate stash 정책 | 일괄 stash | tracked-modified 와 untracked 를 **분리 stash** (`stash@{0}` modified, `stash@{1}` untracked) — 복원 단위 세분화 |
| 보호 파일 정의 위치 | 부재 | `docs/constraints.yaml` 에 `protected_files` 키 신설, plan-gate 가 참조 |

---

## 4. 검증 방법

조치 적용 후 다음 시나리오로 회귀 테스트:

1. **사전**: 워크스페이스에 untracked 핵심 파일(`test-compose.yml`, `test.env`) 생성
2. **트리거**: 단일 파일 5회 편집으로 plan-gate 강제 차단
3. **확인 1**: trigger 메시지에 stash 복구 가이드가 명시되는가
4. **확인 2**: 보호 목록 파일은 working tree에 남는가
5. **확인 3**: `.claude/state/plan_gate_audit.log` 에 액션 기록되는가
6. **확인 4**: Bash 로 `rm test-compose.yml` 시도 시 차단되는가

---

## 5. 부록 — 증거 (Evidence)

### A. transcript 추적 결과 (현재 세션)
- `rm`/`unlink`/`mv` docker-compose 패턴: 0건
- `Write` docker-compose.yml 작성: 4/27 최초 생성 + Edit 1회 (둘 다 정상 생성/편집)
- 5/8 이후 docker-compose.yml 관련 작업: 모두 `Read` (정상)
- sidechain (서브에이전트) 위험 패턴: 0건

### B. plan_gate state
```json
{
  "gate_id": "plan-gate_1778207468476_f543eb",
  "state": "created",
  "edit_count": 34,
  "unique_files": 16,
  "checkpoint_dirty_stash_ref": "plan-gate_1778207468476_f543eb",
  "last_edit_ts": "2026-05-11T00:26:25.931750+00:00"
}
```

### C. stash 메타
- commit time: `2026-05-11 00:26:25 UTC`
- 파일 수: 103
- 메시지: `[plan-gate] plan-gate_1778207468476_f543eb`

### D. 사라진 파일 일부 (stash 안에 보존)
```
docker-compose.yml
.env                            (modified, stash@{0}^2)
docs/checklist.md
docs/completion_report.md
docs/technical_doc.md
docs/decisions.md
docs/glossary.yaml
docs/specs/tool_golden.jsonl
.claude/agents/verifier.md
.claude/agents/backend.md
.claude/agents/frontend.md
.claude/agents/deeplearning.md
.claude/settings.json
.claude/rules/code-style.md
.claude/memory/workflow.md
tasks/todo.md
app/src/eval/tool_metrics.py
app/src/eval/gen_tool_dataset.py
app/src/eval/run_tool_eval.py
...
```

---

## 6. 후속 작업

- [ ] 사용자: `git stash pop stash@{0}` 으로 복원
- [ ] 본 프로젝트: `CLAUDE.md` "알려진 버그/제약" 섹션에 1줄 추가 (3-2-a)
- [ ] 본 프로젝트: `.claude/memory/lessons.md` 에 패턴 행 추가 (3-2-b)
- [ ] Upstream (claude_skills repo): 3-3 의 (a)(b)(c)(d) 4건 PR 작성
- [ ] 회귀 테스트 (4번 항목)
- [ ] 추후 `docs/decisions.md` D-번호로 결정 기록

---

**작성자**: harness-check skill (harness-inspector 진단 기반)
**근거 파일**:
- `/workspace/.claude/state/plan_gate.json`
- `/root/.claude/plugins/marketplaces/hunminkim/plugins/project-init/hooks/plan_gate_lib.py` line 166~176
- `/root/.claude/plugins/marketplaces/hunminkim/plugins/project-init/hooks/plan_gate.py` line 164~168
- `git stash show stash@{0} --include-untracked --name-only` 출력
