# Claude Code 플러그인 마켓플레이스

HunminKim의 개인 Claude Code 플러그인 저장소.

## 새 환경에서 사용하기

> **요구 사항: Python 3.10+** — 모든 훅이 `python3` 로 실행된다.
> 3.6 이하에서는 훅이 SyntaxError 로 전부 무력화된다 (install.sh 가 설치 전에 검사).

```bash
git clone https://github.com/HunminKim/claude.git ~/claude-config
cd ~/claude-config
bash install.sh
```

`install.sh` 가 하는 일:
1. `hunminkim` 마켓플레이스 등록
2. 공식 플러그인 설치 (code-review, code-simplifier, skill-creator, hookify)
3. 개인 플러그인 설치 (project-init, harness-check, prompt-log)

설치 후 Claude Code 재시작 (현재 상태는 `/plugin` 으로 확인)

---

## 플러그인 목록

### project-init

프로젝트 시작 시 개발 환경을 한 번에 초기화한다.

**호출:** `/project-init` 또는 "프로젝트 초기화해줘"

#### 생성되는 파일

| 파일 | 시점 | 작성 주체 |
|------|------|----------|
| `CLAUDE.md` | 초기화 시 | /project-init |
| `docs/plan.md` | 초기화 시 | 사용자 작성 |
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
Claude: @verifier 호출 (.claude/memory/workflow.md에 명시, 예외 없음)
    ↓
verifier: 검증 후 docs/.verifier_result.json 저장
    ↓
PostToolUse 훅 자동 감지 (컨텍스트 무관)
    ↓
checklist / completion_report / technical_doc 자동 업데이트
    ↓
.verifier_result.json 자동 삭제
```

- 검증은 **작업 유형별 프로파일**(`task_type` 8종 — bugfix=재현→소멸, refactor=행위 불변, docs/config=경량, security=부정 테스트, llm-prompt=eval 필수)로 강도가 정해진다. docs/config 의 경량 인정은 훅이 **diff 교차 검증**(변경이 정말 문서/설정뿐인지)으로 기계 확인한다.
- ❌ 판정에는 **실패 분류**(`failure_category`: 구현 결함 / 테스트 부족 / 검증 자산 한계 / 환경 제약)가 붙고, 훅 안내가 분류별로 갈린다(구현 결함→`/retry`, 환경 제약→`/skip` 권장). 상세: [docs/MANUAL.md §6](docs/MANUAL.md).

> 기능 그룹이 완전히 끝나면 `/compact` 실행 (소단위마다 하지 않음)

#### plan-gate (자동 강제 + 체크포인트 자동 관리)

복잡한·계획 외 코드 수정을 자동 감지해 사용자 검토를 강제하고, 게이트가 열릴 때
체크포인트를 자동으로 만들어 롤백 가능하게 한다. 사용자는 메시지 토큰만으로 모든
단계를 제어한다 (코드/파일시스템 직접 접근 불필요).

**두 가지 가드**:
- **thrash(반복 편집) 가드** (기본 켜짐): 같은 코드 파일을 *수렴 없이* ≥ 5회 반복 편집하면 차단. Bash 성공(green) 시 카운터 리셋되어 정상 반복은 통과하고 막힌 flailing 만 잡는다 (문서 파일 제외).
- **스코프 강제** (기본 shadow — 매니페스트 선언 시 위반 감지·환기만): `tasks/todo.md` 에 `<!-- plan-gate: scope BEGIN/END -->` 매니페스트로 건드릴 파일 패턴을 선언하면 스코프 밖 편집이 기본적으로 환기된다. `/plan-gate-scope-enforce` 로 올리면 스코프 밖 편집을 거부(layer-1)하고 Bash 가 만든 스코프 밖 변경을 롤백(layer-2)한다. `*`=한 경로 단계, `**`=하위 전체.

**워크플로우**:

```
Claude: 첫 Edit → 게이트 열림 + git 프라이빗 ref 스냅샷(체크포인트) 자동 생성
    ↓ tasks/todo.md 계획 감지 시 /approve-plan 유도 (자동 승인 안 함 — 명시 승인 필수)
Claude: (필요 시) tasks/todo.md 작성 → 사용자에게 /approve-plan 요청
사용자: /approve-plan   ← todo.md SHA-256 검증 (gate.state = approved)
    ↓
Claude: 구현 (thrash 임계 / 스코프 강제 적용. 예상 밖 인접 파일은 /subplan 로 audit 확장)
    ↓
@verifier(opus) 호출 → docs/.verifier_result.json → gate.state = verified
    ↓
사용자 결정:
  ✅ → /done      (체크포인트 정리)   |  /rollback (스냅샷으로 복원)
  ❌ → /retry     (같은 체크포인트 재구현)  |  /skip (현 변경 보존)  |  /rollback
계획 재작성 → /replan (카운터·스코프 리셋, 체크포인트 유지)
```

**진단 코드**: 모든 차단/위반 메시지 헤더에 `[PG-TRIGGER]`·`[PG-D1]`·`[PG-THRASH]`·`[PG-SCOPE-*]`·`[PG-DNT]` 코드가 붙는다 (실패루프 가드는 `[FL-LOOP]` — plan-gate 와 별개 시스템). 코드→원인→대응 표: [docs/MANUAL.md §11](docs/MANUAL.md#차단환기-코드-일람).
**상태 파일**: `.claude/state/plan_gate.json` · **audit**: `.claude/state/plan_gate_audit.log`
**체크포인트**: git 프라이빗 ref `refs/plan-gate/<id>/checkpoint` (게이트 열림 시 working tree 1회 스냅샷, 사용자 인덱스·stash·브랜치 무간섭)
　└ **비-git / `/plan-gate-no-git` opt-out**: 편집 직전 원본을 `.claude/state/checkpoints/<id>/` 에 cp 복사 → `/rollback` 이 원본 복원·신규 삭제. (v1 git tag/stash 백엔드는 refname 위반·stash drop 유실로 폐기)
**스코프 강제 모드**: `.claude/plan_gate_scope` = `shadow`(기본 — 부재·미지값 포함, 감지·기록만)·`off`(명시 기록 시만)·`enforce`(차단·롤백, 게이트 닫히면 shadow 자동 복귀). layer-2 롤백은 git 저장소에서만 동작(스냅샷 없으면 shadow 강등). 운영 파일(tasks/todo.md·.claude/state·플래그·verifier 결과)은 강제 면제.
**GC**: SessionEnd 훅이 30일 이상된 프라이빗 ref·gate 기록 정리

**활성화 스위치**: `.claude/plan_gate_enabled` 파일 존재 여부로 판정한다 (`/plan-gate-on` · `/plan-gate-off` 로 토글). `verifier.md` 존재 여부와는 독립이다.

> **런타임 상태 파일**: 실패 루프 가드 상태는 `.claude/state/failure_log.json` 에 기록된다 (plan-gate 상태와 동일하게 `.claude/state/` 하위 — `.gitignore` 자동 커버, 커밋 대상 아님).

#### 안전 가드 (plan-gate 와 독립 — 항상 동작)

- **위험 명령 차단**: `rm -rf /`·`~`(래퍼·쿼팅 우회 포함), fork bomb, 디스크 덮어쓰기, 프로젝트 루트 통째 삭제, 핵심 운영 파일 삭제 → 차단(exit 2).
- **비밀 노출 차단**: `.env`·키·자격증명 파일의 읽기/복사/업로드와 명령 안 인라인 시크릿 → 차단. `.env.example` 등 템플릿은 허용.
- **workspace-guard (ask 승격)**: 파괴적 명령(`rm`/`mv`/`find -delete`/`>` truncate 등)의 타겟이 **워크스페이스 밖**(상위·형제 디렉토리)이면 차단 대신 **사용자 확인창**을 띄운다. 변수·명령치환 타겟은 해석 불가 → fail-closed 로 확인. `/tmp` 등 시스템 임시 트리는 예외(워크스페이스가 그 트리 밖일 때만).

#### 그 밖의 명령

- **`/harness-update`** — 이미 초기화된 프로젝트의 하네스(훅·에이전트·docs 자동화)를 최신 플러그인 버전으로 갱신. 사용자가 채운 placeholder 값은 재주입하지 않는다.
- **`/harness-uninstall`** — 이 프로젝트에서 하네스(훅·에이전트·plan-gate 런타임·git hooks)만 제거. CLAUDE.md·docs 등 문서와 사용자 추가분은 보존한다 (목록 확인 후 진행).
- **`/monitor [시간]`** — 학습·배치 등 장시간 작업을 지정 간격으로 자동 점검하며 진행 상황을 보고한다.

### harness-check

개발 중간에 에이전트 제어 인프라(훅·verifier·문서 자동화)가 실제로 동작하는지
**독립 서브에이전트**가 진단하고, 문제 발견 시 upstream 수정 레포트를 생성한다.

**호출:** `/harness-check` 또는 `/harness-check "상황 설명"`

- 점검 항목: 훅 배선, verifier 스펙(`checklist_phase/row`·grounding), 문서 자동화(update_docs), CLAUDE.md 필수 섹션
- 상황 설명을 주면 `.claude/memory/lessons.md` 의 하네스 패턴 표를 참고해 추가할 하네스를 추천한다

<!-- >>> [prompt-log] integration begin -->
### prompt-log

**제거 가능한** 사용자 prompt **원문 전체**(자동 마스킹 후) + 도구 호출 통계 수집 플러그인. 동의한 프로젝트에서만 작동한다.

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

record 스키마: prompt(sanitized) + tools 카운트(edit/write/bash/agent 등) + 영향 파일 + plan-gate 메타(read-only) + outcome. plan-gate 토큰은 평문(`done`)·슬래시(`/done`)·네임스페이스(`/project-init:done`) 모두 `is_token`으로 정규화 인식.

**Sanitize**: API key, JWT, AWS, 이메일 등 정규식 마스킹 (`[REDACTED:type]` 치환).

**Sanitize 한계**: API 키·이메일·한국 PII 등 패턴 기반 마스킹이라 일반 비밀번호·내부 정보는 마스킹되지 않는다 — 동의 시 이 점이 명시된다.

**데이터 정리 / 제거**
```bash
# 대화형: /del_prompt_log — 로그를 ./prompt_log/ 로 이동(원본 삭제) 후 동의 유지/철회 재확인
# 비대화형 일괄 삭제:
bash plugins/prompt-log/uninstall.sh
claude plugins uninstall prompt-log
```

**식별 마커 컨벤션**: 추가된 모든 코드는 `[prompt-log]` 식별 마커로 검색 가능 (`grep -rn '\[prompt-log\]' ~/claude-config/`). 외부 통합 부분은 `<!-- >>> [prompt-log] integration begin -->` ~ `<!-- <<< [prompt-log] integration end -->` 마커로 감싸져 있어 안전하게 제거 가능.

자세한 내용: `plugins/prompt-log/README.md`. 미뤄둔 항목: `plugins/prompt-log/V2_TODO.md`.
<!-- <<< [prompt-log] integration end -->

---

## 플러그인 업데이트

```bash
# 마켓플레이스 메타데이터 갱신 후 플러그인별 업데이트 (재시작 필요)
claude plugin marketplace update hunminkim
claude plugin update project-init@hunminkim
claude plugin update harness-check@hunminkim
claude plugin update prompt-log@hunminkim
```
