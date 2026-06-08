# 하네스 수정 레포트 — project-init plugin

- **프로젝트**: DAIR-YOLO
- **점검일**: 2026-06-03 (KST)
- **판정**: ❌ 하네스 수정 필요
- **현재 active plugin**: `hunminkim/project-init 1.25.0`
- **회귀 도입 origin**: project-init 1.7.1 (settings.json 템플릿에서 PostToolUse Write 누락), 1.12.1 까지 유지, 1.25.0 에서 복구
- **본 프로젝트 init 시점**: 1.12.1 (settings.json 에 PostToolUse 키 부재)

---

## 발견된 문제 요약

| # | 문제 | 심각도 | 유형 | 회귀 origin |
|---|------|--------|------|------------|
| 1 | `cleanup_suggest.py` 가 프로젝트 루트 풀-트리 rglob, `.gitignore` 무시, 비용 가드 없음 → 125GB 데이터 누적 시 매 Stop 마다 분 단위 지연 | ❌ | 템플릿 구조 (1.12.1 ~ 1.25.0 동일) | 최초 설계 |
| 2 | plugin `hooks.json` + `templates/.claude/settings.json` 양쪽에 PostToolUse Write 매처 등록 → `update_docs.py` 중복 실행 risk | ⚠️ | 템플릿 구조 (1.25.0) | 1.25.0 복구 시 중복 |
| 3 | `templates/docs/constraints.yaml` 에 `exclude_dirs` 같은 사용자 SKIP 키 부재 → 프로젝트별 보호 장치 없음 | ⚠️ | 템플릿 구조 | 최초 설계 |

---

## Upstream 수정 대상 (claude_skills 레포지토리)

### 문제 #1: `cleanup_suggest.py` 풀-트리 스캔

**영향 파일 (claude_skills 레포 기준)**
- `plugins/project-init/skills/project-init/assets/templates/.claude/hooks/cleanup_suggest.py`

**현재 코드 (요지)**
- L10–13: `SKIP_DIRS = {.git, node_modules, __pycache__, .venv, venv, env, .env, .mypy_cache, .pytest_cache, .ruff_cache}` — 코드 산출물만 제외
- L48–73: `scan_temp_files()` 가 `root.rglob("*")` 로 프로젝트 전체 순회. `.gitignore` 미반영, budget 가드 없음

**수정 방향 — Patch A (`.gitignore` 존중 + budget guard + exclude_dirs 통합)**

```python
# 헤더에 import 추가
import json, os, subprocess, sys, time
from pathlib import Path
from datetime import datetime

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "env", ".env", ".mypy_cache", ".pytest_cache", ".ruff_cache",
}

SCAN_BUDGET_SEC = 5.0  # 비용 가드 — 초과 시 silent abort

DEFAULT_PATTERNS = {
    "prefixes": ["tmp_", "scratch_", "debug_", "exp_"],
    "suffixes": ["_tmp", "_scratch", "_debug"],
    "dirs": ["tmp/", "scratch/", ".experiments/"],
    "exclude_dirs": [],  # 프로젝트별 추가 SKIP. 예: ["data", "runs", "preprocess"]
}


def _git_tracked_and_untracked(root: Path) -> list[Path] | None:
    """git 트래킹/언트래킹(.gitignore 적용) 파일 목록. git 미사용 시 None."""
    if not (root / ".git").exists():
        return None
    try:
        res = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard"],
            capture_output=True, text=True, timeout=3,
        )
        if res.returncode != 0:
            return None
        return [root / line for line in res.stdout.splitlines() if line]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def scan_temp_files(root: Path, patterns: dict) -> list[Path]:
    prefixes = patterns.get("prefixes", DEFAULT_PATTERNS["prefixes"])
    suffixes = patterns.get("suffixes", DEFAULT_PATTERNS["suffixes"])
    temp_dirs = [d.rstrip("/") for d in patterns.get("dirs", DEFAULT_PATTERNS["dirs"])]
    user_skip = {d.rstrip("/") for d in patterns.get("exclude_dirs", [])}
    skip = SKIP_DIRS | user_skip

    deadline = time.monotonic() + SCAN_BUDGET_SEC
    candidates = _git_tracked_and_untracked(root)

    def _is_temp(path: Path) -> Path | None:
        if not path.is_file():
            return None
        if any(part in skip for part in path.relative_to(root).parts):
            return None
        rel = path.relative_to(root)
        name, stem, str_rel = path.name, path.stem, str(rel)
        if any(str_rel.startswith(d) for d in temp_dirs):
            return path
        if any(name.startswith(p) for p in prefixes):
            return path
        if any(stem.endswith(s) for s in suffixes):
            return path
        return None

    found: list[Path] = []
    iterator = candidates if candidates is not None else root.rglob("*")
    for path in iterator:
        if time.monotonic() > deadline:
            return []  # silent abort — 비용 초과
        hit = _is_temp(path)
        if hit:
            found.append(hit)
    return sorted(found)
```

**수정 이유**: 
- `git ls-files -co --exclude-standard` 가 `.gitignore` 를 자동 존중 → 대용량 데이터/산출물 디렉토리 자동 제외
- `SCAN_BUDGET_SEC=5.0` 으로 timeout 30s 도달 전 silent abort → UX 차단 방지
- `exclude_dirs` 옵션으로 git 미사용 프로젝트도 보호 가능

---

### 문제 #2: `templates/.claude/settings.json` ↔ plugin `hooks.json` PostToolUse 중복

**영향 파일 (claude_skills 레포 기준)**
- `plugins/project-init/skills/project-init/assets/templates/.claude/settings.json`

**현재 코드 (1.25.0 L43~L58)**
```json
"PostToolUse": [
  {
    "matcher": "Write",
    "hooks": [
      {
        "type": "command",
        "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/update_docs.py\"",
        "timeout": 30
      }
    ]
  }
],
```

**수정 방향**: 위 블록 **삭제**. plugin `hooks.json` 의 PostToolUse Write 매처가 plugin install 시 자동 wiring 되므로 사용자 settings.json 에는 중복 등록할 필요 없음.

**수정 이유**: 
- 1.25.0 이 회귀를 복구하면서 plugin `hooks.json` + templates/settings.json 양쪽에 등록 → `update_docs.py` 가 매 Write 마다 2회 실행
- plugin hooks.json 한 곳에만 유지하는 게 sources of truth 명확

---

### 문제 #3: `templates/docs/constraints.yaml` 에 `exclude_dirs` 키 부재

**영향 파일 (claude_skills 레포 기준)**
- `plugins/project-init/skills/project-init/assets/templates/docs/constraints.yaml`

**현재 코드 (요지)**
```yaml
temp_patterns:
  prefixes: ["tmp_", "scratch_", "debug_", "exp_"]
  suffixes: ["_tmp", "_scratch", "_debug"]
  dirs: ["tmp/", "scratch/", ".experiments/"]
```

**수정 방향**: `temp_patterns` 블록 마지막에 다음 추가

```yaml
  # 스캔 제외 디렉토리 (대용량 데이터/산출물 보호)
  # .gitignore 가 이미 처리하는 경우 비워둬도 됨. 추가 보호용.
  exclude_dirs: []
  # 예 (ML 프로젝트):
  # exclude_dirs:
  #   - "data"
  #   - "preprocess"
  #   - "runs"
  #   - "dataset"
```

**수정 이유**: cleanup_suggest.py Patch A 의 `exclude_dirs` 키와 짝을 이뤄 사용자가 프로젝트별 안전장치를 명시적으로 구성 가능

---

### 문제 #4 (부가): SKILL.md 에 PostToolUse wiring 출처 명시

**영향 파일**
- `plugins/project-init/skills/project-init/SKILL.md`

**수정 방향**: verifier 안내 블록(`> ⚠️ verifier 인식 — 세션 재시작 필요`) 부근에 한 줄 추가

```markdown
> verifier 가 `docs/.verifier_result.json` 을 쓰면 plugin `hooks.json` 의
> `PostToolUse: Write` 매처가 `${CLAUDE_PLUGIN_ROOT}/hooks/update_docs.py` 를 자동 실행한다.
> 사용자 `.claude/settings.json` 에 별도 등록 불필요 (plugin install 시 자동 wiring).
```

**수정 이유**: workflow.md / verifier.md 가 "JSON 파일이 생성되면 update_docs.py 훅이 자동 처리 (PostToolUse Write 매칭)" 라고 약속하면서 wiring 출처가 사용자 settings.json 인지 plugin hooks.json 인지 불명확 → 유지보수 혼란

---

## 이 프로젝트 내 즉시 조치 사항

- [ ] `.claude/hooks/cleanup_suggest.py` 에 Patch A 로컬 적용 (다음 `/harness-update` 시 사용자 `n` 보존)
- [ ] `docs/constraints.yaml` 에 `exclude_dirs` 키 추가 + ML 프로젝트 예시 (`data`, `preprocess`, `runs`, `dataset`, `novatek`) 입력
- [ ] `.claude/settings.json` 은 현재 PostToolUse 키 없음 — plugin hooks.json 으로 cover 됨. **그대로 두기** (1.25.0 templates/settings.json 의 중복은 우리에게 영향 없음)
- [ ] `docs/checklist.md` 의 Phase 2 빈 작업명 2건 정리 (수동) — completion_report 격차 28건 점검은 별도 사이클
- [ ] `.claude/memory/lessons.md` 의 "하네스 관련 패턴" 섹션에 이번 점검 결과 1행 추가:
  ```
  | 2026-06-03 | Stop hook 풀-트리 스캔 | cleanup_suggest.py rglob 가 data/preprocess 125GB 풀스캔 → 분 단위 지연 | cleanup_suggest 에 git ls-files + budget guard + exclude_dirs 옵션 추가 (Patch A) | 매 Stop 마다 < 5s 보장 |
  ```

---

## 우선순위 권고 적용 순서

1. **plugin 1.25.0 `cleanup_suggest.py` Patch A** — 핵심 fix (사용자 차단 수준 문제 해소)
2. **plugin 1.25.0 `templates/docs/constraints.yaml` Patch B** — 사용자 보호 옵션 노출
3. **plugin 1.25.0 `templates/.claude/settings.json` 중복 PostToolUse 블록 제거** — 중복 실행 차단
4. **plugin 1.25.0 `SKILL.md` wiring 명시** — 문서 정합성

---

## 검증된 파일 경로 (절대경로)

- `/root/.claude/plugins/cache/hunminkim/project-init/1.25.0/skills/project-init/assets/templates/.claude/hooks/cleanup_suggest.py` (latest, 본문 로직 1.12.1 과 동일)
- `/root/.claude/plugins/cache/hunminkim/project-init/1.12.1/skills/project-init/assets/templates/.claude/hooks/cleanup_suggest.py` (회귀 본)
- `/root/.claude/plugins/cache/hunminkim/project-init/1.25.0/skills/project-init/assets/templates/.claude/settings.json` (PostToolUse 복구됨 — 중복 risk)
- `/root/.claude/plugins/cache/hunminkim/project-init/1.25.0/hooks/hooks.json` L46 (PostToolUse Write → update_docs.py 등록)
- `/root/.claude/plugins/cache/hunminkim/project-init/1.25.0/hooks/update_docs.py` 실재
- `/workspace/.claude/hooks/cleanup_suggest.py` (1.12.1 회귀본과 byte-identical)
- `/workspace/.claude/settings.json` (PostToolUse 키 없음 — 정상, plugin cover)
- `/workspace/docs/constraints.yaml` (`exclude_dirs` 키 없음)
