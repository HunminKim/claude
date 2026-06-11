# 260611 하드코딩 전수 검사 (미처리 — 추후 일괄 수정 대기)

> 발단: "범용 하네스인데 특정 프로젝트/특정 사고 케이스에 하드코딩된 부분이 있는지
> 전수 검사" 요청. 4개 병렬 에이전트(hooks / prompt-log·루트 / skills·templates /
> install·meta·tests)로 134개 파일 스윕 후 높음 항목 전부 직접 검증.
> **이 문서는 발견 기록이며 아직 아무것도 수정하지 않았다.** 처리 시 항목별 체크.

## 🔴 높음 — 다른 환경에서 오동작/무동작 또는 데이터 유출

### H1. 런타임 수집 데이터가 저장소에 커밋됨 (유출) — ✅ 260611 처리

- [x] `prompt-log/prompts-2026-04.jsonl` (2건) / `prompts-2026-05.jsonl` (669건) /
  `prompts-2026-06.jsonl` (430건) — 실제 사용자 프롬프트 **총 1,101건**이 git 추적 중
- [x] `prompt-log/projects-allowed.json` — 특정 프로젝트명 `"DAIR-YOLO"` + consent 타임스탬프
- [x] `prompt-log/.records.lock` — 락 파일까지 커밋
- 본래 `~/.claude/prompt-log/` 런타임 산출물. 저장소에 있을 이유 없음.
- **처리 완료 (260611)**:
  - 실데이터 1,101건 + projects-allowed.json 을 정식 위치 `~/.claude/prompt-log/` 로
    이동 보존 (기존 home 사본은 smoke test 잔재 `/tmp/pl_test` 라 덮어씀)
  - `git rm -r prompt-log/` + `.gitignore` 에 `/prompt-log/` 추가
    (루트 고정 — `plugins/prompt-log/` 는 제외)
  - 데이터 유입 경로: v1.29.0 커밋(cd41072)에 분석용 사본이 휩쓸려 들어간 것
- [ ] **잔여**: git 히스토리 정리(cd41072~HEAD 7개 커밋 + v1.29.0 이후 태그에 데이터 잔존)
  — filter-repo + force push 여부 사용자 결정

### H2. install.sh 원격 저장소 하드코딩 — 로컬 클론이 무의미

- [ ] `install.sh:34` — `claude plugin marketplace add HunminKim/claude`
- 항상 GitHub 원격 main 을 받음. fork·사내 미러·오프라인·로컬 수정본에서 오동작.
  README 의 "로컬 클론 후 설치" 서사와 모순 (`README.md:8-9` `~/claude-config` 안내).
- **처리**: `add .` 또는 스크립트 위치(`BASH_SOURCE`) 기준 경로 등록으로 교체.

### H3. `/tmp/.claude_init_in_progress` — POSIX 고정 경로 양쪽 계약

- [ ] `plugins/project-init/hooks/project_init_permission.py:24` (`_SIGNAL_FILE`)
- [ ] `plugins/project-init/skills/project-init/SKILL.md:17,201` (touch / rm)
- 신호 파일 계약이 `/tmp` 리터럴 — 경로 어긋나면 자동승인이 **조용히 무동작**
  (PermissionRequest 그냥 통과). Windows 비호환.
- **처리**: `tempfile.gettempdir()` 또는 `CLAUDE_PROJECT_DIR` 하위로. 훅·SKILL 양쪽 동기 수정.

### H4. `rm -rf /workspace` 패턴 — 이 개발 컨테이너 전용 매직 경로

- [ ] `plugins/project-init/hooks/dangerous_bash_check.py:27`
- `/workspace` 는 이 저장소가 사는 환경의 경로일 뿐. 배포된 다른 머신에선 아무것도 못 막음.
- **처리**: `CLAUDE_PROJECT_DIR` 값과 동적 비교로 일반화 ("작업 디렉토리 전체 삭제" 의도 복원).

### H5. 템플릿에 남은 이전 프로젝트(DAIR-YOLO 계열 추정)의 실제 도메인 값

- [ ] `templates/agents/verifier.md:58` — `train → export_onnx → onnx2novaonnx → compile →
  validate` (특정 NPU 툴체인 단계명이 본문 지시문에)
- [ ] `templates/docs/constraints.yaml:31` — 동일 단어 `onnx2novaonnx` (주석 예시)
- [ ] `templates/.claude/rules/code-style.md:54-55` — `CnnOutput(IntEnum): BOTTLE=0, PHONE=2`,
  `ActionType.DRINKING` (행동인식 프로젝트 실제 클래스)
- **처리**: 도메인 중립 예시로 교체 (`step_a → step_b → step_c`, `Color(IntEnum)` 류).

## 🟡 중간 — 특정 케이스 과적합

- [ ] **M1. 위임 에이전트 이름 고정**: `delegation_due_diligence.py:25`
  (`@(backend|frontend|deeplearning|ai|infra)`), `delegation_prompt_check.py:33-35`
  (`DELEGATION_SUBAGENTS` frozenset) — 다른 에이전트 구성(`@data`, `@mobile`)에선 미발화.
  `.claude/agents/` 디렉토리 스캔으로 도출하는 게 범용.
- [ ] **M2. prompt-log → project-init 결합**: `prompt_log_lib.py:270-272` —
  `"project-init:"` 네임스페이스 리터럴. "독립·제거 가능 플러그인" 표방과 모순.
- [ ] **M3. KST 고정**: `time_context.py:43` (루트 + 템플릿 양쪽) — `"TZ": "Asia/Seoul"`.
  한국어 컨벤션과 별개로 타임존은 설정/시스템값 가능해야 함. ※ 의도된 설계일 수 있음 — 사용자 결정.
- [ ] **M4. smoke_test PATH 고정**: `tests/smoke_test.py:50` —
  `PATH="/usr/bin:/bin:/usr/local/bin"`. Apple Silicon Homebrew(`/opt/homebrew/bin`)·nix
  환경에서 훅 내부 git 호출 실패 가능.
- [ ] **M5. validate_arch 다언어 모순**: `templates/scripts/validate_arch.py:40` —
  `--include=*.py` 고정인데 SKILL.md 1단계는 다언어 스택 표방.
- [ ] **M6. harness-check 결합**: `harness-check/SKILL.md:66` 외 — upstream 리포명
  `claude_skills` 박힘. project-init 레이아웃 전제는 형제 플러그인 진단 도구라 일부 의도된 결합.

## ⚪ 낮음 (참고 — 수정 선택)

- `prompt_log_lib.py:263-268` — 사고 날짜(260610)·분석 결과("98건 중 0건") 주석.
  회귀 추적 메타데이터로 보면 정상.
- `harness-inspector.md:4` — `model: claude-sonnet-4-6` 버전 핀. 별칭 `sonnet` 권장
  (260611 compat 감사에서 "특정 마이너 고정 지양" 확인했던 항목과 동일 계열).
- todo.md 섹션 헤더 정확일치 의존(`## 영향 파일` 등, delegation 훅들) —
  project-init 템플릿과 한 몸 전제면 의도된 설계.
- plan-gate 의 `tasks/todo.md`·`docs/`·`.claude/` 레이아웃 가정 —
  `is_project_init_managed` 게이트로 비대상 프로젝트는 무해 통과하므로 수용 가능.

## 🔒 보안 노트 (저장소 외 — 로컬 파일)

- `.claude/settings.local.json:15` — GitHub PAT(`ghp_...`) 평문 존재.
  **git 에 커밋된 적 없음 확인** (`git log --all` 결과 없음). 단 allowlist 에 토큰이
  박힌 것 자체가 위험 → **해당 토큰 폐기·재발급 권장**.
- 같은 파일에 일회성 세션 경로 권한 규칙 누적
  (`/root/.claude/projects/.../toolu_*.txt` 등) — 정리 권장.

## 정상으로 판정 (수정 불필요)

- 한국어 메시지 전반 — 저장소 컨벤션.
- `CLAUDE_PROJECT_DIR`/`CLAUDE_PLUGIN_ROOT` 사용처 전부 — 올바른 패턴.
- marketplace.json/plugin.json 의 `HunminKim` author/owner — 메타데이터로서 정상.
- `{{PROJECT_NAME}}` 류 placeholder, `uninstall.sh` 의 `BASH_SOURCE` 기준 경로 산출,
  sanitize 정규식(일반 패턴), lessons.md 시드 교훈(도메인 중립) — 전부 정상.
- 이메일·`/Users/...`·`/home/...` 절대경로 하드코딩 — **전 파일에서 미발견**.

## 처리 시 주의

- H3/H4/H5/M1~M3 은 플러그인 파일 변경 → 해당 `plugin.json` 버전 번프 +
  marketplace.json 동기화 + `python3 tests/smoke_test.py` + 태그 푸시 의례 준수.
- H1 은 데이터 삭제라 버전 번프 무관하지만 히스토리 정리 결정이 선행돼야 함.
- M3(KST)·M6(harness-check 결합)은 "의도된 설계" 가능성 — 수정 전 사용자 확인.
