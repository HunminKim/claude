# 하네스 수정 레포트 — project-init 2.17.6 · 워크트리 병렬 세션 plan-gate 상태 공유 간섭 (2026-07-10)

> **적용: v2.20.0 (2026-07-10)** — (a)(b)(c) 전부 반영. 2차 검토에서 발견된 3건을 수정안에 추가 반영:
> ① (c) fallback 은 원본 gate 의 `file_edit_counts` 에 해당 워크트리 경로가 실제로 섞여 있을 때만
> 발동(교차 집계의 직접 증거) — 무증거 fallback 은 무관한 병렬 세션 gate 를 남의 판정으로 전이시키는
> 역방향 오염이라 억제. ② `plan_gate_gc.py` 는 stdin 을 폐기하고 있어 `data` 바인딩부터 추가.
> ③ `_worktree_gitdir` 는 git 2.48+ relative-paths 모드의 상대 gitdir 을 마커 위치 기준으로 절대화.
> 회귀 테스트: smoke_test `[55]` 10건.

- **프로젝트**: daesung (발견 현장) → **수정 대상: 이 레포 `plugins/project-init/`**
- **플러그인 버전**: project-init **2.17.6**
- **점검일**: 2026-07-10 (KST)
- **판정**: ⚠️ (하네스 구성 자체는 정상 — 워크트리 병렬 세션 조합에서 결정론적으로 재현되는 **템플릿/플러그인 구조 결함**)
- **진단 주체**: harness-inspector(1단계) + claude-code-guide(아키텍처 분석) + general-purpose(코드 정밀 분석) — 3개 독립 서브에이전트

---

## 발견된 문제 요약

| # | 문제 | 심각도 | 유형 |
|---|------|--------|------|
| 1 | 워크트리 세션의 편집이 원본 트리 게이트에 교차 집계 → 게이트 전역 max 기반 thrash 가 **무관한 세션의 Write 를 연좌 차단** | ❌ | 템플릿 구조 |
| 2 | 워크트리에서 실행된 verifier 의 ✅ 판정이 게이트에 미반영된 채 결과 파일이 **소비·삭제(판정 유실)** → `/done` 정상 마감 불가 | ❌ | 템플릿 구조 |
| 3 | `plan_gate_cli.py`(Bash)와 훅이 **서로 다른 루트**를 봐서 워크트리 cwd 에선 "활성 gate 없음" 오보고 | ⚠️ | 템플릿 구조 |

**재현 조건** (daesung 고유 요인 없음 — project-init 프로젝트 전부 해당):
1. Claude Code `EnterWorktree` 는 항상 `<프로젝트>/.claude/worktrees/<이름>/` 에 linked worktree 를 만든다 → 워크트리 파일 경로가 원본 루트의 `relative_to(project_root)` 필터(`plan_gate_lib.py:978-982`)를 통과해 원본 게이트에 합산된다.
2. 훅에는 `CLAUDE_PROJECT_DIR`(=세션 시작 루트)가 주입되지만 **Bash 툴 환경에는 주입되지 않는다** (daesung 실측: `env | grep -i claude_project` 공백). 공식 문서는 워크트리 세션에서의 값을 미정의 — 원본 루트에서 시작 후 EnterWorktree 한 세션은 원본 루트를, 워크트리에서 시작한 세션은 워크트리를 가리킨다(GitHub anthropics/claude-code#36360 실측과 일치).
3. project-init 이 하네스(`.claude/agents/verifier.md`, `.claude/plan_gate_enabled`)를 **커밋 대상**으로 만들므로 모든 워크트리가 하네스 사본을 갖는다 → `update_docs.py` 의 `is_project_init_managed()` 가 워크트리를 유효 루트로 오인한다.
4. 근본 원인: **루트 해석이 3개 소비자에서 3가지로 갈라진다** — 훅 `plan_gate.py`=env / CLI `plan_gate_cli.py:652`=cwd / `update_docs.py:232-242 _resolve_root`=결과파일 docs 부모. 셋의 일치를 강제하는 코드가 없고, 비워크트리 단일 세션에서만 우연히 일치해 잠복해 있었다.

**실측 증거 (daesung, 2026-07-10 16:22~16:31 KST)**:
- 게이트 `plan-gate_1783665127520_9ee549` 의 `file_edit_counts` 에 원본 트리 `scripts/shot_cluster.py`(CSV 세션, 6회)와 워크트리 `scripts/image/prepare_dataset.py`(이미지 세션, 2회) 혼재. audit 로그 `thrash_approved max_repeat=5,6,6` 3건 — shot_cluster.py 가 임계(5)를 넘기자 이미지 세션의 무관한 `docs/.verifier_result.json` Write 가 PG-THRASH 로 차단됨.
- 이미지 사이클 verifier ✅ 는 워크트리 `docs/completion_report.md` 에 반영됐으나 게이트 `verifier_status: null` — `update_docs.py:476-480` "게이트 없음" 분기가 판정 반영 없이 `delete_ok=True` 로 결과 파일을 삭제. `/done` 의 복구 경로(`plan_gate_cli.py:180 _recover_verifier_from_file`)가 찾을 파일도 이미 없음.
- 제3 세션(점검 에이전트)의 읽기 전용 Bash 조차 게이트 `bash_count_post_approval` 을 119→146 으로 올림 — 오염이 실시간 진행됨을 확인.

---

## Upstream 수정 대상 (이 레포 `plugins/project-init/hooks/` — 라인 번호는 2.17.6 배포본 기준)

> 설계 결정: 워크트리를 원본 루트로 병합하지 않고 **트리별 상태 분리**를 택한다. 병합하면 동시 세션이 한 게이트를 공유해 카운터 오염·연좌 차단이 구조적으로 남는다. 분리가 게이트의 의미("한 작업 단위")와 일치한다. 워크트리 감지는 git 실행 없이 `.git` **파일** 검사(linked worktree 는 `gitdir: <main>/.git/worktrees/<n>` 텍스트 파일, 서브모듈 `modules/` 경로와 구분됨)로 한다.
> **권장 적용 순서: (a) → (c) → (b).** (a) 없이 (c)만 적용해도 판정 유실은 즉시 멎는다. (b)는 독립 적용 가능.

### [수정 a] 루트 해석 단일화 — "가장 가까운 작업 트리 = 게이트 루트"

**a-1. `hooks/plan_gate_lib.py:74-83` — `find_project_root()` 교체**

현재:
```python
def find_project_root() -> Path | None:
    """CLAUDE_PROJECT_DIR 우선, 없으면 cwd 상위에서 .claude/를 찾는다."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".claude").exists():
            return parent
    return None
```

수정 후:
```python
def _worktree_gitdir(p: Path) -> Path | None:
    """p 가 linked git worktree 루트면 gitdir(Path) 반환, 아니면 None.

    linked worktree 는 `.git` 이 'gitdir: <main>/.git/worktrees/<name>' 텍스트
    파일이다. 서브모듈(.git/modules/)과 구분하기 위해 worktrees 경로를 요구한다.
    git 실행 없이 감지 — 의존성 없음.
    """
    marker = p / ".git"
    if not marker.is_file():
        return None
    try:
        text = marker.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    gitdir = Path(text[len("gitdir:"):].strip())
    if gitdir.parent.name != "worktrees" or gitdir.parent.parent.name != ".git":
        return None
    return gitdir


def worktree_main_root(root: Path) -> Path | None:
    """linked worktree 루트면 원본(main) 체크아웃 루트를 반환, 아니면 None."""
    gitdir = _worktree_gitdir(root)
    if gitdir is None:
        return None
    return gitdir.parent.parent.parent  # <main>/.git/worktrees/<n> → <main>


def find_project_root(start: str | os.PathLike | None = None) -> Path | None:
    """작업 트리 기준 프로젝트 루트 — 훅·CLI·update_docs 3소비자의 단일 출처.

    linked git worktree 루트를 CLAUDE_PROJECT_DIR 보다 우선한다. EnterWorktree 는
    <프로젝트>/.claude/worktrees/<n>/ 에 워크트리를 만들고 훅에는 원본 루트가
    CLAUDE_PROJECT_DIR 로 주입되지만 Bash(plan_gate_cli)에는 없다 — env 를 무조건
    우선하면 훅=원본 / CLI=워크트리로 상태가 갈라진다(2026-07-10 daesung 실측).
    훅은 훅 입력의 `cwd` 필드를 start 로 넘겨 결정론을 확보한다.
    """
    cwd = Path(start).resolve() if start else Path.cwd().resolve()
    # 1) cwd 상위에서 linked worktree 루트 탐색 (env 보다 우선 — 트리별 상태 분리)
    for parent in [cwd] + list(cwd.parents):
        if _worktree_gitdir(parent) is not None:
            return parent
    # 2) 기존 규칙 그대로: env 우선, 없으면 cwd 상위 .claude/ 탐색
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".claude").exists():
            return parent
    return None
```

**a-2. `hooks/plan_gate_lib.py:94-98` — `is_plan_gate_enabled()` 원본 플래그 fallback**

현재:
```python
def is_plan_gate_enabled(root: Path) -> bool:
    """.claude/plan_gate_enabled 파일 존재 시 plan-gate 활성.
    verifier.md와 독립적으로 on/off 가능.
    """
    return (root / PLAN_GATE_FLAG).exists()
```

수정 후:
```python
def is_plan_gate_enabled(root: Path) -> bool:
    """.claude/plan_gate_enabled 파일 존재 시 plan-gate 활성.
    verifier.md와 독립적으로 on/off 가능.
    워크트리에 플래그가 없으면(하네스 미커밋 브랜치) 원본 체크아웃의 플래그를
    따른다 — 설정(플래그)은 공유, 상태(plan_gate.json)는 트리별 분리.
    """
    if (root / PLAN_GATE_FLAG).exists():
        return True
    main = worktree_main_root(root)
    return main is not None and (main / PLAN_GATE_FLAG).exists()
```

**a-3. 훅 진입점이 훅 stdin JSON 의 `cwd` 를 start 로 전달** — 훅 프로세스 cwd 는 세션 cwd 와 다를 수 있어 stdin 계약 필드를 쓴다 (`update_docs.py:81-87` 이 이미 같은 이유로 `data["cwd"]` 사용). **전수 적용 필수** — 하나라도 빠지면 그 훅만 다른 루트를 보는 새 불일치가 생긴다:

| 파일:라인 | 변경 (`lib.find_project_root()` → `lib.find_project_root(data.get("cwd") or None)`) |
|---|---|
| `hooks/plan_gate.py:122` | ✎ |
| `hooks/plan_gate_bash.py:53` (`_active_gate(data)` 내부) | ✎ |
| `hooks/detect_task_boundary.py:69` | ✎ |
| `hooks/plan_gate_stop_alert.py:97` | ✎ |
| `hooks/plan_approval.py:184` | ✎ |
| `hooks/plan_summary_request.py:42` | ✎ |
| `hooks/verifier_remind.py:95` | ✎ |
| `hooks/plan_gate_session_start.py:76` | ✎ |
| `hooks/plan_gate_gc.py:109` | ✎ |
| `hooks/plan_gate_cli.py:652` | **수정 불요** — Bash cwd(=세션 cwd) 기준 `Path.cwd()` 폴백이 그대로 옳다 |

**a-4. `hooks/update_docs.py:242` — `_resolve_root()` 를 공통 함수에 위임**

현재: `    return lib.find_project_root()`
수정: `    return lib.find_project_root(docs_dir.parent)`
(239-241 의 `candidate` 우선 검사는 유지 — 트리별 분리 규칙에서는 결과 파일이 놓인 트리가 곧 올바른 루트이므로 이제 훅과 일치한다.)

**수정 이유**: 3개 소비자(훅/CLI/update_docs)가 같은 입력이면 같은 루트를 내도록 단일화 + 워크트리 상태를 트리별로 분리해 교차 오염을 근원 차단.

### [수정 b] thrash 판정을 "지금 편집하려는 파일 자체"의 반복 횟수로

**b-1. `hooks/plan_gate_lib.py:801-813`**

현재:
```python
def _max_code_repeat(gate: dict[str, Any]) -> int:
    """코드 파일 중 가장 많이 편집된 횟수. doc 파일은 제외."""
    counts = gate.get("file_edit_counts", {})
    return max((c for fp, c in counts.items() if not is_doc_path(fp)), default=0)
...
def trigger_threshold_exceeded(gate: dict[str, Any]) -> bool:
    return _max_code_repeat(gate) >= TRIGGER_REPEAT_RATIO
```

수정 후 (`_max_code_repeat` 는 status/advisory 표시 전용으로 유지, 판정만 target 기반으로 교체):
```python
def _code_repeat_for(gate: dict[str, Any], target: str | None) -> int:
    """지금 편집하려는 파일 자체의 반복 편집 횟수. target 없음·doc 파일은 0.

    게이트 전역 max 로 판정하면 한 파일의 thrash 가 무관한 파일의 편집까지
    연좌 차단한다(트리 공유 세션에서 특히 치명 — 2026-07-10 daesung 실측).
    """
    if not target or is_doc_path(target):
        return 0
    return int(gate.get("file_edit_counts", {}).get(target, 0))


def trigger_threshold_exceeded(gate: dict[str, Any], target: str | None) -> bool:
    return _code_repeat_for(gate, target) >= TRIGGER_REPEAT_RATIO
```
(`file_edit_counts` 키는 `plan_gate.py:268-269` 가 넣는 `target` 원문 그대로라 조회 키가 정확히 일치.)

**b-2. `hooks/plan_gate.py` 호출부** (`trigger_threshold_exceeded` 호출자는 이 파일 3곳이 전부 — 전수 grep 확인):

| 라인 | 현재 | 수정 |
|---|---|---|
| :289 | `_max_repeat = lib._max_code_repeat(gate)` | `_repeat = lib._code_repeat_for(gate, target)` |
| :292 | `and not lib.trigger_threshold_exceeded(gate)` | `and not lib.trigger_threshold_exceeded(gate, target)` |
| :293 | `and _max_repeat == lib.TRIGGER_REPEAT_RATIO - 1` | `and _repeat == lib.TRIGGER_REPEAT_RATIO - 1` |
| :301 | `… and lib.trigger_threshold_exceeded(gate):` | `… and lib.trigger_threshold_exceeded(gate, target):` |
| :330 | `… and lib.trigger_threshold_exceeded(gate):` | `… and lib.trigger_threshold_exceeded(gate, target):` |
| :332 | `max_repeat=lib._max_code_repeat(gate)` | `max_repeat=lib._code_repeat_for(gate, target)` (audit 정확성) |

메시지 빌더(`plan_gate_lib.py:1428, :1468, :1610`)와 정보성 표시(`plan_gate_cli.py:614`, `detect_task_boundary.py:135`, `plan_gate_stop_alert.py:132`, `plan_gate_session_start.py:122`)는 `_max_code_repeat` 유지 — 차단이 target 기준이 되면 차단 시점의 target 카운트가 곧 max 라 표시 불일치 없음.

**수정 이유**: 연좌 차단 제거 — thrash 는 "그 파일이 수렴 안 됨"의 신호이지 게이트 전체 정지 신호가 아니다.

### [수정 c] update_docs "게이트 없음" 분기 — verifier 판정 보존 + 원본 루트 1회 fallback

**`hooks/update_docs.py:468-480`**

현재:
```python
    delete_ok = True
    try:
        _root = _resolve_root(docs_dir)
        if not _root or not lib.is_project_init_managed(_root):
            _log("[update_docs] plan-gate 미관리 프로젝트 — gate 갱신 건너뜀")
        else:
            _state = lib.load_state(_root)
            _gate = lib.current_gate(_state)
            if not _gate or _gate["state"] not in ("approved", "verified"):
                _log(
                    "[update_docs] ⚠️ 판정을 반영할 gate 가 없습니다"
                    f"(state={_gate['state'] if _gate else None!r}) — gate 갱신 건너뜀"
                )
```

수정 후 (473-480 대체, 481 `elif` 이하 기존 유지):
```python
    delete_ok = True
    try:
        _root = _resolve_root(docs_dir)
        if not _root or not lib.is_project_init_managed(_root):
            _log("[update_docs] plan-gate 미관리 프로젝트 — gate 갱신 건너뜀")
        else:
            _state = lib.load_state(_root)
            _gate = lib.current_gate(_state)
            # 워크트리 루트에 소비 가능한 gate 가 없으면 원본 체크아웃으로 1회 fallback —
            # 루트 해석이 갈렸던 과거 상태(훅=원본에 집계, 결과 파일=워크트리)를 흡수한다.
            if not _gate or _gate["state"] not in ("approved", "verified"):
                _main = lib.worktree_main_root(_root)
                if _main and lib.is_project_init_managed(_main):
                    _m_state = lib.load_state(_main)
                    _m_gate = lib.current_gate(_m_state)
                    if _m_gate and _m_gate["state"] in ("approved", "verified"):
                        _root, _state, _gate = _main, _m_state, _m_gate
            if not _gate or _gate["state"] not in ("approved", "verified"):
                delete_ok = False  # 판정 보존 — 삭제하면 verifier ✅/❌ 가 유실된다
                _log(
                    "[update_docs] ⚠️ 판정을 반영할 gate 가 없습니다"
                    f"(state={_gate['state'] if _gate else None!r}) — gate 갱신 건너뜀.\n"
                    "  결과 파일은 보존합니다 — /done 의 복구 경로 또는 재검증이 소비합니다."
                )
```

**수정 이유**: "파일 삭제는 소비자 소유"(update_docs.py:102-104 주석, 20260710_verifier-gate-deadlock 교훈)라는 기존 원칙을 "게이트 없음" 분기만 어기고 판정을 삭제하고 있었다. `delete_ok=False` 면 `/done` 복구 경로(`plan_gate_cli.py:181-216` → `lib.verifier_result_path(root)`)가 소비한다.

---

## 사이드이펙트 검토 (단일 세션·비워크트리 프로젝트 기준 — 기존 동작 불변 확인)

- **(a)** 비워크트리: 조상 중 `.git` 이 "gitdir:…worktrees…" 파일인 곳이 없으므로 1단계 탐색이 항상 공집합 → 2단계가 기존 코드와 문자 그대로 동일. 서브모듈은 `worktrees` 세그먼트 요구로 오탐 없음. 추가 비용은 조상당 `is_file()` 1회. 워크트리 세션은 상태가 트리별 분리되며, 원본에 이미 쌓인 혼합 게이트는 기존 24h 잔류 경고(plan_gate.py:203-214)와 `plan_gate_gc.py` 가 정리, 이행기 미스매치는 (c) fallback 이 흡수.
- **(a) 알려진 edge**: 워크트리에서 `/plan-gate-off` 시 플래그가 원본에 있으면 `disable_plan_gate`(lib:118-120)가 존재 시에만 unlink 라 에러 없이 "원본 플래그 잔존으로 여전히 활성" — CLI 안내 메시지 명시 또는 main-root fallback 후속 개선 후보.
- **(b)** 단일 파일 5회 반복인 진짜 thrash: 기존과 동일하게 5회째 차단, soft hint 4회째 발화, green Bash 리셋(plan_gate_bash.py:127-129) 유효. 달라지는 것은 연좌 차단 제거뿐(완화 방향만, 강화 방향 회귀 없음). 여러 파일을 각 4회씩 도는 패턴은 파일별 soft hint 와 LARGE_FAN_FILES advisory 가 커버.
- **(c)** 정상 경로(approved/verified 게이트 존재)는 476 분기 미진입으로 불변. 게이트 없이 verifier 를 돌리면 결과 파일이 남지만 다음 실행이 덮어쓰고 `/done`·gc 가 폐기 — "근거가 남는" 쪽이 "판정이 소리 없이 사라지는" 쪽보다 안전. fallback 이 원본 게이트를 잘못 전이시킬 위험은 (a) 적용 전 혼합 상태에 한정 — 의도된 흡수 경로이며, (a)(b)(c) 정착 후에는 발동하지 않는다.

## 참고: Claude Code 본체 관련 사항 (플러그인 밖)

- 공식 문서는 워크트리 세션의 `CLAUDE_PROJECT_DIR` 값을 미정의. 실측: 세션 시작 위치에 고정(원본에서 시작 후 EnterWorktree → 원본 루트 유지 / 워크트리에서 시작 → 워크트리, GitHub anthropics/claude-code#36360). Bash 툴 환경에는 미주입. 관련 이슈: #36360, #46808. 플러그인 수정 (a)는 이 비결정성 자체를 우회하도록 설계됨(env 이전에 워크트리 감지).

## 발견 프로젝트(daesung) 내 즉시 조치 사항

- [x] 워크트리 todo.md·메모리에 anomalib hidden-file 함정 및 학습 전 로드 카운트 assert 기록 (별건, 사이클1에서 처리)
- [ ] 오염된 현재 게이트 `plan-gate_1783665127520_9ee549` 마감: 이미지 사이클1은 verifier 실질 ✅(증거: 워크트리 completion_report.md 4번째 항목) — `/skip-verify` + `/done` 으로 마감 (판정 유실은 본 결함 #2 의 결과이지 검증 누락이 아님)
- [ ] 플러그인 수정 반영 전까지: 워크트리 세션에서 verifier 결과가 게이트에 안 붙는 현상은 재발한다 — 마감은 위 우회로 처리
- [ ] `.claude/memory/lessons.md` "하네스 관련 패턴" 섹션에 본 건 기록 (현재 섹션 비어 있음 — 첫 항목)

> 이 레포트는 claude 레포지토리(plugins/project-init) 기준으로 작성됐다. 라인 번호는 2.17.6 배포본(`~/.claude/plugins/cache/hunminkim/project-init/2.17.6/`) 기준이므로, 레포 소스와 배포본이 다르면 함수명 기준으로 위치를 재확인할 것.
