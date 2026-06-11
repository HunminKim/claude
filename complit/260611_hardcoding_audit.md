# 260611 하드코딩 전수 검사 (H1~H5·M1·M2·M4·M5 처리 완료 / M3·M6 결정 후 종결)

> 발단: "범용 하네스인데 특정 프로젝트/특정 사고 케이스에 하드코딩된 부분이 있는지
> 전수 검사" 요청. 4개 병렬 에이전트(hooks / prompt-log·루트 / skills·templates /
> install·meta·tests)로 134개 파일 스윕 후 높음 항목 전부 직접 검증.
>
> **처리 현황 (260611)**: H1(데이터 유출) 선처리 완료. 이후 나머지 일괄 처리 —
> H2~H5·M1·M2·M4·M5 수정 + smoke 161통과 + 행위 검증 완료. M3(KST)·M6(harness-check
> 결합)은 사용자 결정으로 "의도된 설계" 종결(무수정). 아래 항목별 체크 참조.
> 릴리스: project-init v1.33.0, prompt-log v1.1.1.

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
- [x] **히스토리 정리 완료 (260611)**: `git filter-repo --path prompt-log --invert-paths`
  → main + 태그 48개 force push. 원격 검증: `prompt-log/` 콘텐츠 404,
  경로 커밋 0건. 사전 백업: `/root/claude-repo-backup-260611.bundle`
  ※ GitHub 캐시에 고아 커밋이 한동안 남을 수 있음 — 완전 소거가 필요하면
  GitHub Support 에 reachability 정리 요청 (사실상 접근은 이미 차단됨)
  ※ settings.local.json 의 PAT 는 push 시도에서 **무효 확인됨** (이미 폐기 상태)
  — 죽은 allowlist 줄이므로 로컬 정리만 남음

### H2. install.sh 원격 저장소 하드코딩 — ✅ 260611 처리

- [x] `install.sh:34` — `claude plugin marketplace add HunminKim/claude`
- 항상 GitHub 원격 main 을 받음. fork·사내 미러·오프라인·로컬 수정본에서 오동작.
- **처리 완료**: `SCRIPT_DIR` 기준 로컬 우선 + 원격 fallback (사용자 결정).
  스크립트 옆에 `.claude-plugin/marketplace.json` 있으면 그 로컬 경로 등록, 없으면
  `HunminKim/claude` fallback. 공식 문서 확인: 로컬 경로 등록 → marketplace.json 의
  `name`(hunminkim)으로 식별 → `install @hunminkim` 정상. 행위 검증: bash 문법 OK +
  로컬 소스 선택 분기 동작 확인.

### H3. `/tmp/.claude_init_in_progress` — ✅ 260611 처리

- [x] `project_init_permission.py` — `_SIGNAL_FILE = Path(tempfile.gettempdir()) / ...`
- [x] `SKILL.md:17,201` — `touch/rm "${TMPDIR:-/tmp}/.claude_init_in_progress"`
- **처리 완료**: 플랫폼 임시 경로로 양쪽 동기화 (Python tempfile ↔ bash ${TMPDIR:-/tmp}).
  행위 검증: 신호파일 생성 시 allow JSON, 없으면 통과 확인.

### H4. `rm -rf /workspace` 패턴 — ✅ 260611 처리

- [x] `dangerous_bash_check.py` — 정적 `/workspace` 줄 제거 + `_deletes_project_root()` 추가
- **처리 완료**: `CLAUDE_PROJECT_DIR` 와 동적 비교로 일반화. smoke 신규 테스트
  "CLAUDE_PROJECT_DIR 전체 삭제 → 차단" 통과.

### H5. 템플릿에 남은 이전 프로젝트(DAIR-YOLO 계열) 도메인 값 — ✅ 260611 처리

- [x] `verifier.md:58` — `build → test → package → sign → deploy` 중 `sign` 스킵으로 교체
- [x] `constraints.yaml` pipeline_steps 주석 — build/test/package/sign/deploy 로 교체
- [x] `code-style.md` — `SensorId(IntEnum): TEMP=0, PRESSURE=2` / `SENSOR_TO_METRIC` 중립 예시
- **처리 완료**: 인덱스 매핑·파이프라인 예시의 교육적 의미 유지하며 도메인 색 제거.

## 🟡 중간 — 특정 케이스 과적합

- [x] **M1. 위임 에이전트 이름 고정** — ✅ 260611 처리. 두 훅 모두 고정 목록 제거,
  `_UTILITY_SUBAGENTS`(Plan/Explore/verifier/general-purpose/statusline-setup) 화이트리스트
  외 + `.claude/agents/<name>.md` 존재 시 도메인 위임으로 판정. 어떤 이름(@data 등)에도
  일반화. smoke: backend/@data 발화, Plan/미정의 통과 — 행위 검증 완료.
- [x] **M2. prompt-log → project-init 결합** — ✅ 260611 처리.
  `re.sub(r"^[\w.-]+:", "", t)` 로 임의 플러그인 네임스페이스 prefix 제거.
  smoke: `/any-plugin:skip → skip` 통과.
- [x] **M3. KST 고정** — ⏸️ 사용자 결정으로 **무수정 종결**. 한국어 컨벤션의 일부로
  KST 고정 유지(`time_context.py` 루트+템플릿). "의도된 설계"로 분류.
- [x] **M4. smoke_test PATH 고정** — ✅ 260611 처리.
  `env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project)}` 로 실제 PATH 상속.
- [x] **M5. validate_arch 다언어** — ✅ 260611 처리. `LANG_RULES` 로 Python/JS·TS/Go/Rust
  import 문법 검사. 행위 검증: 4개 언어 banned 탐지 + clean 통과.
- [x] **M6. harness-check 결합** — ⏸️ 사용자 결정으로 **무수정 종결**. `claude_skills`
  리포명·project-init 레이아웃 전제는 형제 플러그인 진단 도구의 의도된 결합으로 분류.

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

## 처리 결과 (260611 종결)

- **수정 완료**: H1(데이터 유출+히스토리), H2(install), H3(/tmp), H4(/workspace),
  H5(DAIR-YOLO 잔재), M1(위임 가드 일반화), M2(네임스페이스), M4(PATH), M5(다언어).
- **무수정 종결(의도된 설계)**: M3(KST 고정), M6(harness-check 결합) — 사용자 결정.
- **검증**: `python3 tests/smoke_test.py` 161통과 0실패 + H2/H3/M5 별도 행위 검증.
- **릴리스**: project-init v1.32.0 → v1.33.0, prompt-log v1.1.0 → v1.1.1
  (harness-check 무변경). marketplace.json description 동기화.

## 잔여 (별도 처리 — 저장소 외/로컬)

- 🔒 `.claude/settings.local.json` PAT(폐기 확인됨) + 일회성 세션 경로 권한 규칙 누적 →
  로컬 파일 정리. git 미추적이라 릴리스와 무관.
- ⚪ 낮음 항목들(`harness-inspector.md` model 버전 핀 등)은 수정 선택 — 미처리.
