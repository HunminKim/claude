# prompt-log

**제거 가능한 (removable)** Claude Code 플러그인. 사용자 prompt와 도구 호출 통계를 **사용자가 동의한 프로젝트에서만** jsonl로 누적한다.

이 플러그인이 추가하는 모든 코드/파일은 `[prompt-log]` 식별 마커를 가지고 있어 `grep` 으로 한 번에 찾을 수 있고, `uninstall.sh` 한 줄로 제거할 수 있다.

---

## 목적

- plan-gate 휴리스틱 튜닝 (추후)
- 사용자 워크플로우 패턴 분석 (어떤 prompt → 어떤 작업)
- 다른 플러그인이 read-only 로 활용 가능

## 동의 메커니즘 (default deny)

수집은 다음 **두 조건이 모두 만족**해야 시작된다:

1. **글로벌 whitelist 등록**: `~/.claude/prompt-log/projects-allowed.json` 에 프로젝트 절대경로 추가
2. **프로젝트별 marker 파일**: `<project>/.claude/prompt-log-consent` 존재

`/project-init` 실행 시 4단계에서 동의 요청이 표시되며, `y` 응답 시 둘 다 자동 생성된다. `n` 또는 `/project-init` 미실행이면 둘 다 없으므로 수집 안 함.

## 저장 위치

```
~/.claude/prompt-log/
├── prompts-YYYY-MM.jsonl        # 월별 분할 (한 줄 = 한 prompt record)
└── projects-allowed.json         # 글로벌 동의 whitelist

<project>/.claude/
├── prompt-log-consent            # 프로젝트별 동의 marker
└── state/
    └── prompt-log-active.json    # 진행 중 prompt (다음 prompt 도착 시 flush)
```

## Sanitize (PII 마스킹)

수집 직전 prompt 본문에 다음 정규식을 적용해 `[REDACTED:...]` 로 치환:

- API keys: `sk-...`, `sk-ant-...`, `ghp_...`, `ghs_...`, `xoxb-...`
- AWS: `AKIA...`
- JWT: `eyJ...eyJ...`
- URL credentials: `https://user:pass@...`
- Email
- 한국 PII: 주민등록번호(`YYMMDD-NNNNNNN`), 사업자등록번호(`NNN-NN-NNNNN`), 전화번호(`010-XXXX-XXXX`)

### 사용자 정의 패턴 추가

`~/.claude/prompt-log/sanitize_rules.yaml` 파일을 생성해 커스텀 마스킹 패턴을 추가할 수 있다:

```yaml
- pattern: "내부-[A-Z]+-\d+"   # 내부 티켓 ID
  replacement: "[REDACTED:ticket_id]"
- pattern: "Bearer [A-Za-z0-9._-]+"
  replacement: "[REDACTED:bearer_token]"
```

파일이 없거나 `pyyaml` 미설치 시 graceful skip (빌트인 패턴만 사용).

## Record 스키마 (V1)

```json
{
  "prompt_id": "pl_<ts_ms>_<hex6>",
  "session_id": "...",
  "ts_start": "ISO8601",
  "ts_end": "ISO8601",
  "project": {"abs_path": "...", "name": "...", "hash": "sha256[:12]"},
  "prompt": {
    "text": "sanitized 원문",
    "len": 142,
    "is_token": false,
    "token_value": null
  },
  "tools": {
    "edit": 5, "write": 2, "multi_edit": 0,
    "bash": 3, "task": 1, "other": 0, "total": 11
  },
  "files": {"unique_count": 4, "sample": ["src/a.py", "..."]},
  "user_tokens_during": [{"token": "/approve-plan", "ts": "..."}],
  "plan_gate": {                  // plan-gate 적용 프로젝트만, 없으면 null
    "gate_id": "plan-gate_...",
    "state": "approved",
    "verifier_status": "✅",
    "edit_count": 5,
    "unique_files_count": 3
  },
  "outcome": {"ended_by": "next_prompt|session_end", "duration_sec": 245}
}
```

## 슬래시 커맨드

| 커맨드 | 설명 |
|---|---|
| `/prompt-log-status` | 현재 프로젝트 동의 여부 + 글로벌 수집 통계(레코드 수, 파일 크기) 출력 |

## 훅 구성

| 훅 이벤트 | 스크립트 | 역할 |
|---|---|---|
| UserPromptSubmit | `prompt_logger.py` | 새 prompt 도착 → 이전 active flush + 새 active 시작 |
| PreToolUse (Edit\|Write\|MultiEdit\|Bash\|Task) | `tool_counter.py` | tools 카운터 누적, 영향 파일 추가 |
| SessionEnd | `session_finalize.py` | 마지막 active flush |

## 동의 후 활성화

### 자동 (권장)
```
/project-init   # 4단계에서 y 응답
```

### 수동
```bash
# whitelist 추가
mkdir -p ~/.claude/prompt-log
python3 -c "
import json, os, sys
from datetime import datetime, timezone
p = os.path.expanduser('~/.claude/prompt-log/projects-allowed.json')
allowed = json.load(open(p)) if os.path.exists(p) else []
abs_path = os.path.realpath('.')
if not any(e.get('abs_path') == abs_path for e in allowed):
    allowed.append({
        'abs_path': abs_path,
        'project_name': os.path.basename(abs_path),
        'consent_at': datetime.now(timezone.utc).isoformat(),
    })
    json.dump(allowed, open(p, 'w'), indent=2)
"

# marker 생성
mkdir -p .claude
date -u +"%Y-%m-%dT%H:%M:%S+00:00" > .claude/prompt-log-consent
```

## 동의 철회

```bash
# 1. marker 삭제
rm .claude/prompt-log-consent

# 2. (선택) whitelist에서 제거 — 수동 편집
$EDITOR ~/.claude/prompt-log/projects-allowed.json

# 3. (선택) 이미 저장된 데이터 삭제
rm ~/.claude/prompt-log/prompts-*.jsonl   # 모든 프로젝트 데이터 (주의!)
# 또는 jq로 특정 프로젝트만 필터링
```

V1에선 자동 grace period / 자동 삭제 없음. V2 예정 (V2_TODO.md 참고).

## 제거 (1줄)

```bash
bash plugins/prompt-log/uninstall.sh
```

내부 동작:
1. `~/.claude/prompt-log/` 삭제
2. 현재 디렉토리 하위 모든 `prompt-log-consent` / `prompt-log-active.json` 삭제
3. 외부 통합 흔적 (`>>> [prompt-log]` 마커 부분) 검색 안내

플러그인 자체 제거:
```bash
claude plugins uninstall prompt-log
```

외부 통합 (project-init / marketplace.json / install.sh / 루트 README) 의 prompt-log 추가 부분도 마커로 감싸져 있어 안전하게 제거 가능:
```bash
grep -rn '\[prompt-log\]' ~/.claude-config/   # 모든 흔적 위치
```

## plan-gate 와의 관계

prompt-log 는 plan-gate state(`<project>/.claude/state/plan_gate.json`)를 **read-only** 로 참조해 record에 메타 첨부한다. plan-gate가 없거나 미사용이면 `plan_gate: null`. 단방향 — plan-gate는 prompt-log 를 모르므로 제거해도 plan-gate에 영향 없음.

## 식별 마커 컨벤션 (제거 용이성)

추가된 모든 파일/식별자는 다음 마커로 검색 가능:

| 위치 | 마커 형태 |
|---|---|
| Python 파일 헤더 | `# [prompt-log] removable plugin — see plugins/prompt-log/README.md` |
| 함수 prefix | `pl_` (`pl_is_consented`, `pl_sanitize` 등) |
| 외부 통합 (md/sh) | `>>> [prompt-log] integration begin` ~ `<<< [prompt-log] integration end` |
| 디렉토리 | `plugins/prompt-log/`, `~/.claude/prompt-log/`, `.claude/prompt-log-consent` |

검색:
```bash
grep -rn '\[prompt-log\]' ~/.claude-config/
```

## V2 예정 (V2_TODO.md)

- 90일 grace period + 자동 압축/삭제
- `/prompt-log-cleanup` / `/prompt-log-delete-now` / `/prompt-log-export`
- 시뮬레이션 replay 분석 도구 (휴리스틱 임계값 정량 검증)
- detect_user_correction.py / detect_failure_loop.py 신호 통합
