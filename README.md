# Claude Code 플러그인 마켓플레이스

HunminKim의 개인 Claude Code 플러그인 저장소.

## 새 환경에서 사용하기

```bash
git clone https://github.com/HunminKim/claude.git ~/claude-config
cd ~/claude-config
bash install.sh
```

`install.sh` 가 하는 일:
1. `hunminkim` 마켓플레이스 등록
2. 공식 플러그인 설치 (code-review, code-simplifier, skill-creator, hookify)
3. 개인 플러그인 설치 (project-init, harness-check, prompt-log)

설치 후 Claude Code 재시작 또는 `/reload-plugins`

---

## 플러그인 목록

### project-init

프로젝트 시작 시 개발 환경을 한 번에 초기화한다.

**호출:** `/project-init` 또는 "프로젝트 초기화해줘"

#### 생성되는 파일

| 파일 | 시점 | 작성 주체 |
|------|------|----------|
| `CLAUDE.md` | 초기화 시 | /project-init |
| `docs/development_plan.md` | 초기화 시 | 사용자 작성 |
| `docs/context_note.md` | 초기화 시 | 사용자 작성 |
| `docs/checklist.md` | 초기화 시 → 소단위마다 업데이트 | verifier (자동) |
| `docs/technical_doc.md` | 소단위 완료마다 누적 | verifier (자동) |
| `docs/completion_report.md` | 소단위 완료마다 누적 | verifier (자동) |
| `docs/deployment_guide.md` | 개발 중 누적 → 완료 후 정리 | verifier + 사용자 |
| `docs/retrospective.md` | 완료 사인 후 | Claude |
| `docs/debug/*.md` | 버그 발생 시 | Claude |
| `.claude/agents/verifier.md` | 초기화 시 | /project-init |

#### 개발 워크플로우 (verifier)

```
사용자: 기능 구현 요청
    ↓
Claude: 소단위로 구현
    ↓
Claude: @verifier 호출 (CLAUDE.md에 명시, 예외 없음)
    ↓
verifier: 검증 후 docs/.verifier_result.json 저장
    ↓
PostToolUse 훅 자동 감지 (컨텍스트 무관)
    ↓
checklist / completion_report / technical_doc 자동 업데이트
    ↓
.verifier_result.json 자동 삭제
```

> 기능 그룹이 완전히 끝나면 `/compact` 실행 (소단위마다 하지 않음)

#### plan-gate (자동 강제 + 체크포인트 자동 관리)

복잡한 코드 수정을 자동 감지해 사용자 계획 승인을 강제하고, 작업 시작 시점에
체크포인트를 자동으로 만들어 롤백 가능하게 한다. 사용자는 메시지 토큰만으로
모든 단계를 제어한다 (코드/파일시스템 직접 접근 불필요).

**트리거 조건** (PreToolUse 훅, OR):
- `Edit`/`Write`/`MultiEdit` 호출 ≥ 3회
- 영향 파일 ≥ 3개
- 단일 `MultiEdit` 항목 ≥ 5개

**워크플로우**:

```
Claude: Edit 시도 (1·2·3차)
    ↓ 3차에서 차단 (트리거 임계값 도달)
PreToolUse 훅: git stash + git tag (체크포인트 생성)
    ↓
Claude: tasks/todo.md 에 계획 작성 → 사용자에게 검토 요청
    ↓
사용자: /approve-plan
    ↓ todo.md SHA-256 검증
plan_approval 훅 + 슬래시 커맨드: gate.state = "approved"
    ↓
Claude: 구현 진행 (max(initial+2, 5) 초과 시 scope creep 차단)
    ↓
@verifier 호출 → docs/.verifier_result.json
    ↓
update_docs 훅: gate.state = "verified", verifier_status 기록
    ↓
사용자 결정:
  ✅ → /done       (체크포인트 정리)
       /rollback   (체크포인트로 복원, dirty stash 보존)
  ❌ → /retry      (같은 체크포인트에서 재시도)
       /rollback   (체크포인트로 복원)
계획 재작성 필요 → /replan (카운터 리셋, 체크포인트 유지)
```

**상태 파일**: `.claude/state/plan_gate.json`
**체크포인트**: `git tag .claude/gate/<id>/clean` + `[plan-gate] <id>` stash entry
**GC**: SessionEnd 훅이 30일 이상된 tag·stash·gate 기록 정리

**가드**: `.claude/agents/verifier.md` 가 없는 (project-init 미적용) 프로젝트에선 plan-gate가 자동 비활성화된다.

<!-- >>> [prompt-log] integration begin -->
### prompt-log

**제거 가능한** 사용자 prompt + 도구 호출 통계 수집 플러그인. 동의한 프로젝트에서만 작동한다.

**목적**
- plan-gate 휴리스틱 튜닝 (V2)
- 사용자 워크플로우 패턴 분석
- 다른 플러그인이 read-only로 활용

**동의 메커니즘 (default deny)**
다음 두 조건이 모두 만족해야 수집:
1. 글로벌 whitelist 등록 — `~/.claude/prompt-log/projects-allowed.json`
2. 프로젝트별 marker — `<project>/.claude/prompt-log-consent`

`/project-init` 실행 시 4단계에서 동의 요청이 자동 표시되며, `y` 응답 시 둘 다 자동 생성. `n` 또는 미실행이면 수집 안 함.

**저장**
```
~/.claude/prompt-log/
├── prompts-YYYY-MM.jsonl     # 월별 분할 (한 줄 = 한 prompt record)
└── projects-allowed.json     # 동의 whitelist
```

record 스키마: prompt(sanitized) + tools 카운트(edit/write/bash/task) + 영향 파일 + plan-gate 메타(read-only) + outcome.

**Sanitize**: API key, JWT, AWS, 이메일 등 정규식 마스킹 (`[REDACTED:type]` 치환).

**1줄 제거**
```bash
bash plugins/prompt-log/uninstall.sh
claude plugins uninstall prompt-log
```

**식별 마커 컨벤션**: 추가된 모든 코드는 `[prompt-log]` 식별 마커로 검색 가능 (`grep -rn '\[prompt-log\]' ~/.claude-config/`). 외부 통합 부분은 `<!-- >>> [prompt-log] integration begin -->` ~ `<!-- <<< [prompt-log] integration end -->` 마커로 감싸져 있어 안전하게 제거 가능.

자세한 내용: `plugins/prompt-log/README.md`. 미뤄둔 항목: `plugins/prompt-log/V2_TODO.md`.
<!-- <<< [prompt-log] integration end -->

---

## 플러그인 업데이트

```bash
claude plugins update project-init@hunminkim
```
