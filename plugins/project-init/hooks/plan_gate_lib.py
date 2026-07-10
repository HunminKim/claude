"""plan-gate 공통 라이브러리.

상태 관리, 체크포인트(git 프라이빗 ref 스냅샷 / 비-git cp 디렉토리), 스코프 강제,
트리거 휴리스틱, 프로젝트 감지를 담당. 훅 스크립트(plan_gate.py, plan_gate_bash.py,
plan_approval.py, plan_gate_cli.py, plan_gate_gc.py, update_docs.py)에서 공유한다.

상태 파일: <project>/.claude/state/plan_gate.json
체크포인트: git 프라이빗 ref `refs/plan-gate/<gate_id>/checkpoint` (working tree 1회 스냅샷)
            비-git/opt-out: `.claude/state/checkpoints/<gate_id>/` cp 디렉토리
            (v1 git tag/stash 백엔드는 refname 위반·stash drop 유실로 폐기 — 부록 C)
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ── 정책 디폴트 (D5/D2/D7) ───────────────────────────────────────────────
# thrash(flailing) 감지: 동일 파일 반복 패치 임계. green Bash(테스트 통과) 시 리셋되어
# 정상 반복은 회피하고 수렴 없는 반복(계속 실패)만 도달한다(설계 D9).
TRIGGER_REPEAT_RATIO = 5   # 단일 파일 편집 횟수 임계값: 같은 코드 파일 5회 이상 반복 시 차단
GC_MAX_AGE_DAYS = 30

# 큰 미승인 변경 → /approve-plan 권장(비차단 advisory) 임계. 규모는 "계획 필요"의
# 신뢰 프록시가 아니므로(3줄도 위험·300줄 리네임은 사소) 차단이 아닌 환기 1회만 한다.
LARGE_OP_LINES = 50    # 단일 편집이 추가하는 코드 줄(공백·주석 제외) 이 값 이상이면 환기
LARGE_FAN_FILES = 3    # 미승인 게이트에서 건드린 코드 파일 수가 이 값 이상이면 환기

# 새 요청 진입 시 "계획 필요 여부 자가 판단" cue (열린 게이트 없을 때만, detect_task_boundary).
# 매 프롬프트 도배 방지: 짧은 프롬프트·slash 는 제외하고, 스로틀로 빈도 제한한다.
# 정책 본문은 workflow.md 가 상주로 보유 — 훅 cue 는 가벼운 재무장만 한다.
MIN_PROMPT_CHARS = 25            # 이 길이 미만 프롬프트(연속·짧은 응답)는 cue 생략
SEMANTIC_CUE_THROTTLE_MIN = 15   # 직전 cue 로부터 이 분(分) 이내면 재출력 생략

# 자동으로 .plan-gateignore에 추가할 후보 패턴 (파일명 기준, fnmatch)
_AUTO_IGNORE_CANDIDATES: list[tuple[str, str]] = [
    ("*.md", "문서"),
    ("*.rst", "문서"),
    (".gitignore", "VCS 메타"),
    (".gitattributes", "VCS 메타"),
    (".editorconfig", "에디터 설정"),
    ("LICENSE*", "프로젝트 메타"),
    ("CHANGELOG*", "프로젝트 메타"),
    ("NOTICE*", "프로젝트 메타"),
    ("AUTHORS*", "프로젝트 메타"),
    ("*.lock", "의존성 잠금 파일"),
    ("*-lock.json", "의존성 잠금 파일"),
]

# ── 작업 경계 타임아웃 ───────────────────────────────────────────────────
BOUNDARY_TIMEOUT_MINUTES = 60  # 마지막 편집으로부터 이 시간 이상 경과 시 자동 done

# ── 패치 이력 임계값 ─────────────────────────────────────────────────────
PATCH_WARN_DAYS = 14
PATCH_WARN_THRESHOLD = 3
PATCH_BLOCK_DAYS = 30
PATCH_BLOCK_THRESHOLD = 5
PATCH_MAX_ENTRIES_PER_FILE = 50

GATE_STATES = {"created", "approved", "verified", "rolled_back", "done"}


# ── 프로젝트 감지 ────────────────────────────────────────────────────────
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


PLAN_GATE_FLAG = ".claude/plan_gate_enabled"


def is_project_init_managed(root: Path) -> bool:
    """project-init 플러그인이 초기화한 프로젝트인지 확인 (verifier.md 기준)."""
    return (root / ".claude" / "agents" / "verifier.md").exists()


def is_plan_gate_enabled(root: Path) -> bool:
    """.claude/plan_gate_enabled 파일 존재 시 plan-gate 활성.
    verifier.md와 독립적으로 on/off 가능.
    """
    return (root / PLAN_GATE_FLAG).exists()


def is_plan_gate_manageable(root: Path) -> bool:
    """plan-gate CLI(done/off/rollback 등)가 이 디렉토리에서 동작해도 되는지.

    강제 훅은 plan_gate_enabled 만으로 켜지므로, 게이트가 켜져 있으면 verifier.md
    유무와 무관하게 **항상 닫을 수 있어야** 한다 (강제는 되는데 CLI 가 verifier.md
    부재로 거부하면 닫을 수 없는 데드락이 된다). project-init 프로젝트면 아직 게이트가
    켜지기 전이라도 /plan-gate-on 등을 허용한다.
    """
    return is_plan_gate_enabled(root) or is_project_init_managed(root)


def enable_plan_gate(root: Path) -> None:
    p = root / PLAN_GATE_FLAG
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(now_iso() + "\n", encoding="utf-8")


def disable_plan_gate(root: Path) -> None:
    p = root / PLAN_GATE_FLAG
    if p.exists():
        p.unlink()


PREFER_NO_GIT_FLAG = ".claude/plan_gate_no_git"


def prefers_no_git(root: Path) -> bool:
    """.claude/plan_gate_no_git 존재 시 git 체크포인트를 끄고 cp 스냅샷을 강제한다.

    git repo 이면서도 plan-gate 가 git 프라이빗 ref 스냅샷을 만들지 않기를 원하는
    사용자용 명시적 opt-out. (예: 별도 VCS 워크플로우와 충돌 회피, git 추적 비선호)
    ⚠️ 이 경우 checkpoint_commit 이 없어 layer-2 enforce 는 shadow 로 강등된다(H-2).
    """
    return (root / PREFER_NO_GIT_FLAG).exists()


def set_prefer_no_git(root: Path) -> None:
    p = root / PREFER_NO_GIT_FLAG
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(now_iso() + "\n", encoding="utf-8")


def unset_prefer_no_git(root: Path) -> None:
    p = root / PREFER_NO_GIT_FLAG
    if p.exists():
        p.unlink()


# ── 스코프 강제 모드 (R4 — 단일 3상태 플래그) ─────────────────────────────
# 불리언 하나로는 {off, shadow, enforce} 직교 축을 표현 못 한다(롤아웃 ↔ on/off).
# .claude/plan_gate_scope 파일 내용으로 모드를 표현 — 부재/미지값이면 shadow(기본).
PLAN_GATE_SCOPE_FLAG = ".claude/plan_gate_scope"
SCOPE_MODES = ("off", "shadow", "enforce")
# 기본 모드 = shadow: 매니페스트를 선언했으면 스코프 밖 편집을 기본적으로 *환기*한다
# (차단·삭제는 안 함 — enforce 만 파괴적). 매니페스트가 없으면 모드 무관 no-op 이라
# 이 기본값은 "스코프를 선언한 프로젝트"에서만 효력이 생긴다. off 는 명시 선택지로 남는다.
DEFAULT_SCOPE_MODE = "shadow"


def scope_mode(root: Path) -> str:
    """스코프 강제 모드: off|shadow(기본·감지·기록만)|enforce(차단·롤백).

    플래그 파일 부재·미지값은 모두 DEFAULT_SCOPE_MODE(shadow)로 떨어진다 — shadow 는
    차단·삭제가 없어(환기만) 기본값으로 안전하다. off 는 파일에 'off' 가 명시됐을 때만.
    """
    p = root / PLAN_GATE_SCOPE_FLAG
    if not p.exists():
        return DEFAULT_SCOPE_MODE
    try:
        val = p.read_text(encoding="utf-8", errors="ignore").strip().lower()
    except OSError:
        return DEFAULT_SCOPE_MODE
    return val if val in SCOPE_MODES else DEFAULT_SCOPE_MODE


def set_scope_mode(root: Path, mode: str) -> None:
    """모드 플래그 기록. 세 모드 모두 리터럴로 기록한다.

    부재=shadow(기본)로 의미가 바뀌었으므로 off 도 파일을 지우지 않고 'off' 를
    명시 기록해야 한다(안 그러면 /plan-gate-scope-off 가 도로 기본 shadow 가 된다).
    """
    p = root / PLAN_GATE_SCOPE_FLAG
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(mode + "\n", encoding="utf-8")


def revert_scope_if_enforced(root: Path) -> bool:
    """게이트 닫힘 시 enforce → shadow 자동 복귀 (stale enforce 청소). 복귀했으면 True.

    enforce 는 프로젝트 단위로 영속해 게이트가 닫혀도 남는다 → 한 작업을 위해 켠
    enforce 가 무관한 다음 작업에서 조용히 신규 파일을 삭제하는 사고(stale enforce)를
    막는다. '안전한 방향(파괴 해제)'으로만 자동 전환하므로 opt-in 불필요. off 는 사용자
    명시 선택이라 건드리지 않고, shadow 는 이미 기본이라 변화 없음.
    """
    if scope_mode(root) != "enforce":
        return False
    set_scope_mode(root, "shadow")
    log_audit(root, "scope_auto_revert", from_mode="enforce", to_mode="shadow")
    return True


# ── 상태 파일 입출력 ─────────────────────────────────────────────────────
def state_path(root: Path) -> Path:
    return root / ".claude" / "state" / "plan_gate.json"


def load_state(root: Path) -> dict[str, Any]:
    p = state_path(root)
    if not p.exists():
        return {"current_gate_id": None, "gates": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {"current_gate_id": None, "gates": {}}


def save_state(root: Path, state: dict[str, Any]) -> None:
    p = state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


# ── git 헬퍼 ────────────────────────────────────────────────────────────
def _git(root: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=check,
    )


def has_git(root: Path) -> bool:
    r = _git(root, "rev-parse", "--git-dir")
    return r.returncode == 0


def head_sha(root: Path) -> str | None:
    r = _git(root, "rev-parse", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else None


def working_tree_clean(root: Path) -> bool:
    r = _git(root, "status", "--porcelain")
    return r.returncode == 0 and r.stdout.strip() == ""


# ── cp 디렉토리 헬퍼 (비-git 백엔드 내용 저장소) ──────────────────────────
# 비-git/opt-out 시 record_touched 가 편집 전 원본을 여기 복사하고
# rollback_checkpoint 가 복원에 쓴다 (git 은 프라이빗 ref 커밋이 내용 출처).
def cp_checkpoint_dir(root: Path, gate_id: str) -> Path:
    return root / ".claude" / "state" / "checkpoints" / gate_id


def _cp_restore_file(src: Path, dst: Path, existed: bool) -> None:
    """한 파일을 스냅샷 상태로 되돌린다. 존재했으면 원본 복사, 신규였으면 삭제."""
    try:
        if existed:
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        elif dst.exists():
            dst.unlink()
    except OSError:
        pass


# ── v2 체크포인트: 프라이빗 ref 스냅샷(git 내용) + touched 매니페스트(롤백 대상) ──
# v1 tag/stash 백엔드 폐기 사유: tag 는 refname 규칙 위반(`.claude/...` — 컴포넌트가
# `.` 으로 시작 불가)으로 생성 자체가 불가했고(C1), stash pop 실패 시 drop 으로
# 데이터를 유실했다(C2). v2 는 게이트 열 때 working tree 를 프라이빗
# ref(refs/plan-gate/<id>/checkpoint) 커밋으로 1회 스냅샷하고(사용자 인덱스·stash·
# 브랜치 무간섭), 편집된 파일만 touched 매니페스트(gate['cp_snapshot']=
# {relpath: 편집전존재여부})에 기록한다. 롤백은 매니페스트 구동: 존재했던 파일은
# 내용 복원(git=스냅샷 커밋 checkout / 비-git=cp 디렉토리), 신규 파일은 삭제,
# 매니페스트 밖 무관 파일은 보존. git/비-git 이 동일 매니페스트 모델을 공유한다.
PLAN_GATE_REF_PREFIX = "refs/plan-gate/"


def snapshot_ref(gate_id: str) -> str:
    return f"{PLAN_GATE_REF_PREFIX}{gate_id}/checkpoint"


def _worktree_tree_sha(root: Path, gate_id: str) -> str | None:
    """임시 인덱스(GIT_INDEX_FILE)로 working tree 전체의 tree 객체 SHA 생성.

    사용자의 실제 인덱스(staging)를 건드리지 않는다. 실패 시 None.
    """
    tmp_index = state_path(root).parent / f".cpindex_{gate_id}"
    env = {**os.environ, "GIT_INDEX_FILE": str(tmp_index)}

    def _g(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=str(root), env=env, capture_output=True, text=True
        )

    try:
        tmp_index.parent.mkdir(parents=True, exist_ok=True)
        if head_sha(root) and _g("read-tree", "HEAD").returncode != 0:
            return None
        if _g("add", "-A").returncode != 0:
            return None
        wt = _g("write-tree")
        return wt.stdout.strip() if wt.returncode == 0 else None
    finally:
        try:
            tmp_index.unlink()
        except OSError:
            pass


def create_snapshot(root: Path, gate: dict[str, Any]) -> str | None:
    """게이트 열 때 1회: working tree 전체를 프라이빗 ref 커밋으로 캡처.

    clean/dirty 무관하게 캡처(C1 의 'dirty 면 tag 스킵' 한계 제거).
    반환: 스냅샷 commit SHA. 비-git 이거나 실패 시 None (→ cp 디렉토리 백엔드).
    """
    if not has_git(root):
        return None
    tree = _worktree_tree_sha(root, gate["id"])
    if not tree:
        return None
    head = head_sha(root)
    args = [
        "-c", "user.name=plan-gate", "-c", "user.email=plan-gate@localhost",
        "commit-tree", tree, "-m", f"plan-gate checkpoint {gate['id']}",
    ]
    if head:
        args += ["-p", head]
    ct = _git(root, *args)
    if ct.returncode != 0:
        return None
    commit = ct.stdout.strip()
    _git(root, "update-ref", snapshot_ref(gate["id"]), commit)
    log_audit(root, "snapshot_created", gate_id=gate["id"], commit=commit[:12])
    return commit


def _rel_to_root(root: Path, target: str) -> str | None:
    """target(상대/절대)을 루트 상대경로로 정규화. 루트 밖이면 None.

    forward-slash 로 반환한다(as_posix). str() 은 Windows 에서 백슬래시를 내어
    스냅샷 manifest 키가 'sub\\new.py' 처럼 OS 종속·비포터블해진다 — 매처(_path_match)는
    이미 정규화하지만 manifest/audit 키 자체를 POSIX 로 고정해 OS 간 일관성을 보장한다.
    """
    p = Path(target)
    if not p.is_absolute():
        p = root / p
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _cp_backup(root: Path, gate_id: str, rel: str, src: Path) -> bool:
    """비-git 백엔드용: 원본을 cp 디렉토리에 복사. 성공 시 True."""
    dest = cp_checkpoint_dir(root, gate_id) / rel
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return True
    except OSError:
        return False


def record_touched(root: Path, gate: dict[str, Any], target: str) -> None:
    """편집 직전 1회: gate['cp_snapshot'] 에 {relpath: 편집전존재여부} 기록.

    비-git(checkpoint_commit 없음)이면 원본을 cp 디렉토리에 복사해 내용도 보존한다.
    git 이면 내용은 스냅샷 커밋이 출처이므로 존재 비트만 기록한다.
    이미 기록된 파일은 건드리지 않아 게이트 열림 시점 상태가 보존된다.
    """
    rel = _rel_to_root(root, target)
    if rel is None:
        return
    manifest = gate.get("cp_snapshot")
    if manifest is None:
        manifest = {}
        gate["cp_snapshot"] = manifest
    if rel in manifest:
        return
    src = root / rel
    existed = src.exists()
    if existed and not gate.get("checkpoint_commit"):
        # 비-git: 내용 백업 실패 시 기록 안 함(잘못된 복원 방지)
        if not _cp_backup(root, gate["id"], rel, src):
            return
    manifest[rel] = existed


def _rollback_one(
    root: Path, rel: str, existed: bool, commit: str | None, cpdir: Path
) -> None:
    """한 파일을 체크포인트 상태로 되돌린다. git=커밋 checkout / 비-git=cp 디렉토리."""
    if not commit:
        _cp_restore_file(cpdir / rel, root / rel, existed)  # 기존 3-arg 헬퍼 재사용
        return
    if existed:
        _git(root, "checkout", commit, "--", rel)
        return
    dst = root / rel
    if dst.exists():
        try:
            dst.unlink()
        except OSError:
            pass


def rollback_checkpoint(root: Path, gate: dict[str, Any]) -> bool:
    """체크포인트로 편집 전 상태 복원. touched 매니페스트 구동.

    존재했던 파일은 내용 복원(git=스냅샷 커밋 checkout / 비-git=cp 디렉토리),
    신규 파일은 삭제, 매니페스트 밖 무관 파일은 보존. 성공 시 True.
    """
    manifest = gate.get("cp_snapshot") or {}
    commit = gate.get("checkpoint_commit")
    if not manifest and not commit:
        return False
    cpdir = cp_checkpoint_dir(root, gate["id"])
    for rel, existed in manifest.items():
        _rollback_one(root, rel, existed, commit, cpdir)
    cleanup_checkpoint(root, gate)
    log_audit(root, "rollback", gate_id=gate["id"], files=len(manifest))
    return True


def cleanup_checkpoint(root: Path, gate: dict[str, Any]) -> None:
    """gate 닫힘 정리: git 프라이빗 ref 삭제 + cp 디렉토리 + 미소비 verifier 결과 제거.

    do_gate_done(=/done·/skip-verify)과 rollback_to_checkpoint(=/rollback)이 공유하는
    유일한 gate 닫힘 지점이다. 결과 파일을 여기서 함께 폐기하지 않으면, 소비되지 못한
    판정이 다음 gate 로 넘어가 검증한 적 없는 gate 를 done 으로 만든다. 전부 best-effort.
    """
    if gate.get("checkpoint_commit"):
        _git(root, "update-ref", "-d", snapshot_ref(gate["id"]))
    shutil.rmtree(cp_checkpoint_dir(root, gate["id"]), ignore_errors=True)
    discard_verifier_result(root)


# ── layer-2 스코프 스윕 (R1 — git-status 구동, 매니페스트 비의존) ──────────
# 핵심 교훈(rev.3 R1): touched 매니페스트만 롤백하면 정작 잡으라는 Bash 우회
# 쓰기(스코프밖 파일)가 매니페스트에 안 들어가 invisible 했다. 따라서 실제
# working tree 변경 전체를 git status 로 훑어 스코프밖을 처리한다. git 전용 —
# 비-git 은 등가 스윕이 없어 빈 목록(호출자가 detect/warn 만).
def _git_status_entries(root: Path) -> list[tuple[str, str | None]]:
    """working tree 변경/신규 파일을 (경로, 원본경로 or None) 목록으로.

    `-z` 사용 — 경로를 verbatim(따옴표·C-style 이스케이프 없음, NUL 구분)으로 받아
    공백·특수문자·유니코드 경로를 정확히 파싱한다(H-3). 리네임/카피(R/C)는 새 경로와
    원본 경로(porcelain -z 는 `XY new` NUL `orig`)를 함께 반환해 in-scope 원본 복원
    판단에 쓴다(H-4).
    """
    r = _git(root, "status", "--porcelain", "-z", "--untracked-files=all")
    if r.returncode != 0:
        return []
    entries: list[tuple[str, str | None]] = []
    tokens = r.stdout.split("\0")
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if len(tok) < 4:
            i += 1
            continue
        status, path = tok[:2], tok[3:]
        if status[0] in ("R", "C") and i + 1 < len(tokens):
            entries.append((path, tokens[i + 1] or None))  # (새 경로, 원본 경로)
            i += 2
        else:
            entries.append((path, None))
            i += 1
    return entries


def _snapshot_has_path(root: Path, commit: str, rel: str) -> bool:
    """스냅샷 커밋 트리에 해당 경로가 존재했는지 (git cat-file probe)."""
    return _git(root, "cat-file", "-e", f"{commit}:{rel}").returncode == 0


def sweep_effective_mode(root: Path, gate: dict[str, Any], mode: str) -> str:
    """enforce 라도 복원 출처(git 스냅샷 커밋)가 없으면 파괴 금지 → shadow 강등(H-2).

    스냅샷이 없으면 파일이 게이트 열림 시점에 존재했는지 판정할 수 없어 신규/기존
    구분도 복원도 불가능하다 — 무백업 삭제로 빠지는 fail-open 을 막고 감지만 한다.

    ⚠️ 결합 주의: 이 강등은 체크포인트 기능이 스코프 강제 수위를 결정하는 지점이다 —
    /plan-gate-no-git(체크포인트 opt-out)이나 스냅샷 실패가 enforce 를 조용히
    무력화한다. 의도된 안전 결합(무백업 삭제 금지)이므로 절단하지 말 것. 강등 시
    format_scope_sweep 이 사유를 반드시 환기한다(침묵 강등 금지). 행위 계약은
    smoke_test [30] H-2 가 고정한다.
    """
    if mode == "enforce" and not gate.get("checkpoint_commit"):
        return "shadow"
    return mode


def scope_sweep(root: Path, gate: dict[str, Any], mode: str) -> dict[str, list[str]]:
    """layer-2: git-status 로 실제 변경 파일을 훑어 스코프밖을 처리 (R1).

    반환 {"removed":[...], "warned":[...]}. enforce 안전 정책:
    - 스코프 밖 *신규* 파일(스냅샷에 없음) → rm (명백한 Bash 우회 생성물).
    - 스코프 밖 *기존* 파일 수정 → 되돌리지 않고 경고만(C-1: 사용자 직접 편집일 수
      있어 checkout 으로 덮으면 데이터 손실). audit + 최종 diff 로 surface.
    - 스코프 밖으로의 리네임으로 in-scope 원본이 사라졌으면 원본 복원(H-4).
    - 스냅샷 커밋 없으면 enforce→shadow 강등(H-2, sweep_effective_mode).
    shadow/강등: 전부 warned 로만 기록(롤백 없음). off/매니페스트없음/비-git → 빈 결과.
    """
    result: dict[str, list[str]] = {"removed": [], "warned": []}
    if mode == "off" or not has_manifest(gate) or not has_git(root):
        return result
    effective = sweep_effective_mode(root, gate, mode)
    commit = gate.get("checkpoint_commit")
    cpdir = cp_checkpoint_dir(root, gate["id"])
    ignore_patterns = load_gate_ignore(root)
    for path, orig in _git_status_entries(root):
        if scope_allows(str(root / path), gate, root, ignore_patterns):
            continue
        existed = _snapshot_has_path(root, commit, path) if commit else True
        if effective == "enforce" and not existed:
            _rollback_one(root, path, False, commit, cpdir)  # 신규 스코프밖 → 삭제
            result["removed"].append(path)
            # 스코프 밖으로의 리네임으로 사라진 in-scope 원본 복원
            if orig and _snapshot_has_path(root, commit, orig) and not (root / orig).exists():
                _rollback_one(root, orig, True, commit, cpdir)
                result["removed"].append(f"{orig} (원본 복원)")
        else:
            result["warned"].append(path)  # 기존 파일 수정 or shadow → 경고만(데이터 보호)
    if result["removed"] or result["warned"]:
        log_audit(
            root,
            "scope_violation_" + ("enforced" if effective == "enforce" else "shadow"),
            gate_id=gate["id"],
            removed=result["removed"][:20],
            warned=result["warned"][:20],
        )
    return result


# ── todo.md 해시 ────────────────────────────────────────────────────────
def todo_md_path(root: Path) -> Path:
    return root / "tasks" / "todo.md"


def hash_todo_md(root: Path) -> tuple[str | None, float | None]:
    """todo.md의 sha256과 mtime. 없으면 (None, None).

    개행을 universal-newline 으로 정규화(read_text)한 뒤 해시한다 — plan_gate.py 의
    캡처측이 read_text().encode() 로 LF 기준 해시를 만들므로, 여기서 read_bytes 로
    원본 CRLF 를 해시하면 Windows(CRLF todo.md)에서 두 해시가 어긋나 /approve-plan 이
    'todo.md 변경됨' 으로 첫 시도부터 부당 거부된다. 양측을 LF 기준으로 통일한다.
    """
    p = todo_md_path(root)
    if not p.exists():
        return None, None
    try:
        content = p.read_text(encoding="utf-8", errors="ignore")
        return hashlib.sha256(content.encode()).hexdigest(), p.stat().st_mtime
    except Exception:
        return None, None


# ── gate 객체 ───────────────────────────────────────────────────────────
def new_gate_id() -> str:
    ts = int(time.time() * 1000)
    suffix = uuid.uuid4().hex[:6]
    return f"plan-gate_{ts}_{suffix}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_audit(root: Path, action: str, **kwargs: Any) -> None:
    """plan-gate 액션을 .claude/state/plan_gate_audit.log에 JSON Lines로 기록."""
    audit_path = root / ".claude" / "state" / "plan_gate_audit.log"
    entry: dict[str, Any] = {"ts": now_iso(), "action": action, **kwargs}
    try:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def strip_command_prefix(text: str) -> str:
    """슬래시·플러그인 네임스페이스 prefix 를 벗긴 토큰 문자열을 반환(정규화 SSOT).

    plan-gate 전이 토큰은 실데이터에서 3가지 형태로 들어온다 (260618 F-005):
    - 평문:          "done"                 (UserPromptSubmit fallback 경로)
    - 슬래시:        "/done"
    - 네임스페이스:  "/project-init:done", "/임의플러그인:done"
    `lstrip("/")` 만 하던 인라인 정규화가 네임스페이스 형태를 못 벗겨 전이가 silent
    실패하던 drift 를 제거한다. 네임스페이스 prefix 는 임의 플러그인 호환을 위해
    일반 정규식(`^<word>:`)으로 제거한다 — 특정 플러그인명을 하드코딩하지 않는다.
    매칭 가능한 액션 토큰인지의 판정은 호출자 책임(_ACTION_TOKENS 조회).
    """
    t = text.strip().lstrip("/")
    return re.sub(r"^[\w.-]+:", "", t)


def make_gate(gate_id: str | None = None) -> dict[str, Any]:
    return {
        "id": gate_id or new_gate_id(),
        "created_at": now_iso(),
        "state": "created",
        "edit_count": 0,
        "edit_count_post_approval": 0,       # 승인 후 편집 수 (verifier_remind 용)
        "bash_count_post_approval": 0,       # 승인 후 실질 Bash 성공 수 (verifier_remind 용 — Bash 전용 작업 커버)
        "unique_files": [],
        "file_edit_counts": {},              # {파일경로: 편집횟수} — thrash(반복) 감지용
        "initial_edit_count": None,
        "initial_unique_files": None,
        "approved_at": None,
        "approved_auto": False,
        "last_edit_ts": None,
        "last_successful_bash_ts": None,       # green Bash 수렴 신호 — thrash 리셋 기준(D9)
        "todo_md_sha256": None,
        "todo_md_mtime": None,
        "checkpoint_commit": None,             # git 프라이빗 ref 스냅샷 커밋 SHA (비-git 이면 None)
        "cp_snapshot": None,                   # touched 매니페스트 {relpath: 편집전존재여부} (롤백 구동)
        "scope": [],                           # 매니페스트 scope 패턴 (빈 목록 = thrash-only)
        "expansions": [],                      # subplan 으로 audit 하에 추가된 스코프 패턴 (replan 시 리셋)
        "do_not_touch": [],                    # 매니페스트 do-not-touch 패턴 (deny-first)
        "manifest_sha256": None,               # 매니페스트 블록 sha256 (저장만 — 미소비. 실 TOCTOU 가드는 todo_md_sha256)
        "verifier_status": None,
        "large_advisory_seen": False,          # 큰 미승인 변경 환기 1회 dedup
    }


def current_gate(state: dict[str, Any]) -> dict[str, Any] | None:
    gid = state.get("current_gate_id")
    if not gid:
        return None
    return state.get("gates", {}).get(gid)


def set_current_gate(state: dict[str, Any], gate: dict[str, Any]) -> None:
    state.setdefault("gates", {})[gate["id"]] = gate
    state["current_gate_id"] = gate["id"]


def clear_current_gate(state: dict[str, Any]) -> None:
    state["current_gate_id"] = None


# ── 상태 전이 중앙화 (step 4) ─────────────────────────────────────────────
# 5개 변이 지점(plan_gate.py 자동승인 + cli approve/retry/replan)의 카운터·계획
# 리셋을 단일 출처로 모은다. 과거 "카운터 오염" 회귀(replan 미리셋·thrash 잔류)가
# 흩어진 인라인 리셋 때문이었다. 전이마다 리셋 집합이 다르므로(source-aware)
# 단일 transition(gate, to_state) 통합은 금지 — 명명 전이별로 자기 필드만 건드린다.
# done 은 부수효과(체크포인트 정리·아카이브)를 동반하므로 do_gate_done 이 담당.
# 데이터 적재(매니페스트 파싱·todo 해시)는 호출자 책임 — 이 함수는 리셋만 한다.
_LEGAL_TRANSITION_FROM: dict[str, set[str]] = {
    "approve_auto": {"created"},  # 자동승인: 생성 직후 fresh gate
    "approve_manual": {"created"},  # /approve-plan: 선승인도 fresh(created)에서 출발
    "retry": {"verified"},  # verifier ❌ 후 같은 체크포인트 재구현
    "replan": {"created", "approved", "verified"},  # 계획 재작성 (활성 상태 어디서나)
}


def transition(gate: dict[str, Any], name: str) -> dict[str, Any]:
    """게이트 상태 전이 + 전이별 필드 리셋의 단일 출처(step 4, R6 source-aware).

    name ∈ {approve_auto, approve_manual, retry, replan}. 공통: 합법 from-state
    가드(불법이면 ValueError) + state 설정. 리셋 필드는 전이마다 다르다 — naive
    통합 금지(카운터 오염 회귀). 전이별 리셋 집합은 아래 분기가 단일 출처다.
    """
    legal = _LEGAL_TRANSITION_FROM.get(name)
    if legal is None:
        raise ValueError(f"unknown transition: {name!r}")
    if gate.get("state") not in legal:
        raise ValueError(
            f"illegal transition {name!r} from state={gate.get('state')!r} "
            f"(legal from: {sorted(legal)})"
        )

    if name in ("approve_auto", "approve_manual"):
        gate["state"] = "approved"
        gate["approved_at"] = now_iso()
        gate["approved_auto"] = name == "approve_auto"
        gate["edit_count_post_approval"] = 0
        gate["bash_count_post_approval"] = 0
        # 승인 시점 누적치를 initial 로 1회 고정(이미 있으면 보존 — 재승인 누적 방지)
        if gate.get("initial_edit_count") is None:
            gate["initial_edit_count"] = gate.get("edit_count", 0)
            gate["initial_unique_files"] = len(gate.get("unique_files", []))
    elif name == "retry":
        # 같은 체크포인트·계획에서 재구현 → scope/계획/initial 보존, 시도 카운터만 리셋
        gate["state"] = "approved"
        gate["verifier_status"] = None
        gate["edit_count_post_approval"] = 0
        gate["bash_count_post_approval"] = 0
        gate["file_edit_counts"] = {}
    elif name == "replan":
        # 계획 재작성 → 체크포인트만 유지, 카운터·계획·scope 전부 리셋
        gate["state"] = "created"
        gate["approved_auto"] = False
        gate["approved_at"] = None
        gate["edit_count"] = 0
        gate["edit_count_post_approval"] = 0
        gate["bash_count_post_approval"] = 0
        gate["file_edit_counts"] = {}
        gate["unique_files"] = []
        gate["initial_edit_count"] = None
        gate["initial_unique_files"] = None
        gate["todo_md_sha256"] = None
        gate["todo_md_mtime"] = None
        gate["scope"] = []
        gate["expansions"] = []
        gate["do_not_touch"] = []
        gate["manifest_sha256"] = None
        gate["verifier_status"] = None
    return gate


# ── verifier 판정 문자열 정규화 (단일 출처) ──────────────────────────────
# verifier 는 LLM 이라 verdict 에 수식어가 붙는다. 정확 일치만 요구하면 gate 갱신이
# 조용히 누락되고(→ /done 영구 거부 + grounding 강등 우회), 반대로 접두만 보면
# "✅ (일부 항목 실패)" 같은 조건부 통과까지 승격되는 fail-open 이 된다.
# 규칙: 조건 없는 통과만 ✅ / 실패는 관대하게 ❌ / 나머지는 ❓(전이 금지, 호출자가 경고).
_BENIGN_PASS_SUFFIX = frozenset({"", "통과", "pass", "passed", "성공", "ok"})
_VERDICT_SUFFIX_STRIP = " \t:：·—–-()[]{}<>\"'`.,"


def normalize_verdict(raw: Any) -> str:
    """verifier verdict 를 ✅/❌/❓ 로 정규화. update_docs 와 복구 경로가 공유한다."""
    s = str(raw or "").strip()
    if not s:
        return "❓"
    if s.startswith("❌"):
        return "❌"
    if s.startswith("✅"):
        rest = s[1:]
        if "❌" in rest:
            return "❓"  # ✅/❌ 혼재 — 판정 불가
        if rest.strip(_VERDICT_SUFFIX_STRIP).lower() in _BENIGN_PASS_SUFFIX:
            return "✅"
        return "❓"  # 조건부 통과("조건부", "일부 실패") — 승격 금지
    return "❓"


# ── verifier 결과 파일 (control-plane) ───────────────────────────────────
def verifier_result_path(root: Path) -> Path:
    """verifier 판정 결과 파일 경로 — update_docs / plan_gate_cli 공유 단일 출처."""
    return Path(root) / "docs" / ".verifier_result.json"


def discard_verifier_result(root: Path) -> None:
    """결과 파일 폐기(best-effort). 소비에 성공했거나 gate 가 닫힐 때만 호출한다.

    소비 실패 시 삭제하면 복구 경로(_recover_verifier_from_file)의 재료가 사라져
    /done 이 영구 거부된다. 반대로 gate 가 닫혔는데 남겨두면 낡은 판정이 다음
    gate 를 승격시킨다 — 삭제 시점은 이 둘 사이에 정확히 놓여야 한다.
    """
    try:
        verifier_result_path(root).unlink()
    except OSError:
        pass


def verifier_result_is_stale(root: Path, gate: dict[str, Any]) -> bool:
    """결과 파일이 현재 gate 승인 시각보다 오래됐는가 (= 이전 gate 의 판정).

    복구 경로는 gate id 대조가 없다. 낡은 파일이 남아 있으면 검증한 적 없는 새
    gate 가 남의 ✅ 로 done 처리된다. mtime 대조로 그 경로를 막는다.
    판단 불가(시각 없음·파일 없음·파싱 실패)면 stale 아님으로 본다(fail-open —
    정상 사용을 막지 않기 위해. 진짜 방어는 gate 닫힘 시 discard 다).
    """
    approved_at = gate.get("approved_at")
    if not approved_at:
        return False
    try:
        approved = datetime.fromisoformat(approved_at)
        mtime = datetime.fromtimestamp(verifier_result_path(root).stat().st_mtime, timezone.utc)
    except (OSError, ValueError):
        return False
    if approved.tzinfo is None:
        approved = approved.replace(tzinfo=timezone.utc)
    return mtime < approved


def enter_verified(gate: dict[str, Any], verdict: str) -> dict[str, Any]:
    """verifier 판정 반영(→verified)의 단일 출처.

    update_docs.py(정상 경로)와 plan_gate_cli 의 결과 파일 복구 경로가 공유한다 —
    직접 대입이 여러 곳에 흩어지면 한쪽만 필드가 추가·리셋되는 회귀가 생긴다.
    approved(첫 판정)·verified(재판정 덮어쓰기)에서만 합법. verdict 는 ✅/❌.
    """
    if gate.get("state") not in ("approved", "verified"):
        raise ValueError(
            f"illegal enter_verified from state={gate.get('state')!r} (legal: approved, verified)"
        )
    gate["state"] = "verified"
    gate["verifier_status"] = verdict
    return gate


# ── 트리거 휴리스틱 (D5) ─────────────────────────────────────────────────
def _max_code_repeat(gate: dict[str, Any]) -> int:
    """코드 파일 중 가장 많이 편집된 횟수. doc 파일은 제외."""
    counts = gate.get("file_edit_counts", {})
    return max((c for fp, c in counts.items() if not is_doc_path(fp)), default=0)


def _unique_code_files(gate: dict[str, Any]) -> int:
    """doc 제외 코드 파일 수 (unique_files 트리거용)."""
    return sum(1 for fp in gate["unique_files"] if not is_doc_path(fp))


def trigger_threshold_exceeded(gate: dict[str, Any]) -> bool:
    return _max_code_repeat(gate) >= TRIGGER_REPEAT_RATIO


def converged_since_last_edit(gate: dict[str, Any]) -> bool:
    """직전 편집 이후 green Bash(수렴 신호, D9)가 발생했는지.

    created(미승인) 게이트의 자동 롤오버 판정용: 작은 미승인 편집은 approve·done
    둘 다 불요이므로, 편집 뒤 통과한 Bash 가 작업을 사실상 마감했다고 보고
    다음 편집 때 게이트를 조용히 닫는다(닫는 신호를 사람이 칠 필요 없음).
    last_edit_ts 가 없으면(편집 0회) 롤오버 대상 아님 → False.
    """
    edited = gate.get("last_edit_ts")
    converged = gate.get("last_successful_bash_ts")
    if not edited or not converged:
        return False
    return converged > edited  # ISO 8601 문자열은 사전순=시간순


# ── 검증 명령 판별 (green-bash 수렴 신호 필터) ───────────────────────────
# 아무 exit-0 명령(ls·cat·git status)이 수렴으로 인정되면 thrash·롤오버 신호가
# 의미를 잃는다. 테스트·빌드·린트·타입체크 등 "코드를 검증하는" 명령의 성공만
# 수렴으로 본다. 단어형 러너는 \b 로, 서브커맨드 필요한 런처(go/cargo/npm…)는
# 동사와 함께 매칭한다. 미인식 시 False(=리셋 안 함) — fail-closed 로 신호를 보수.
# ⚠️ 결합 주의: 이 정규식 하나가 3개 기능의 공유 입력이다 —
#   ① thrash 카운터 리셋 (plan_gate_bash.py green-reset)
#   ② soft hint 억제 (같은 리셋 경유)
#   ③ created 게이트 자동 롤오버 = 체크포인트 삭제 시점
#     (converged_since_last_edit → plan_gate.py 롤오버 → do_gate_done)
# 러너를 추가·제거하면 "롤백 불가능해지는 순간"이 함께 바뀐다.
# 행위 계약은 smoke_test [23](리셋)·[25b](롤오버)·[25d](비검증 명령 제외)가 고정한다.
_VERIFY_RE = re.compile(
    r"\b(pytest|unittest|nosetests|tox|jest|vitest|mocha|playwright|cypress|"
    r"rspec|phpunit|mypy|pyright|ruff|flake8|pylint|eslint|tslint|tsc|rubocop|"
    r"ctest|gtest|bazel|make|gradle|gradlew|mvn|cmake)\b",
    re.IGNORECASE,
)
_VERIFY_COMPOUND_RE = re.compile(
    r"\b(go\s+(test|build|vet)|"
    r"cargo\s+(test|build|check|clippy)|"
    r"(npm|yarn|pnpm)\s+(run\s+)?(test|build|lint|typecheck|check)|"
    r"dotnet\s+(test|build)|"
    r"rake\s+(test|spec)|"
    r"python3?\s+-m\s+(pytest|unittest|tox|mypy|ruff|pylint|flake8))",
    re.IGNORECASE,
)


def is_verification_command(command: str) -> bool:
    """Bash 명령이 테스트/빌드/린트 등 코드 '검증' 명령이면 True (수렴 신호 자격)."""
    if not command:
        return False
    return bool(_VERIFY_RE.search(command) or _VERIFY_COMPOUND_RE.search(command))


# ── 실질 작업 Bash 판별 (verifier_remind 의 Bash 전용 작업 커버) ──────────────
# 배경: verifier_remind 는 Edit|Write 매처에만 걸려 있어 `docker build`·학습 API 호출처럼
# 파일을 안 고치는 작업은 검증 상기가 영원히 발화하지 않았다(사각지대). 명령어 화이트리스트
# (docker|curl|kubectl…)는 반드시 새므로, 대신 Claude Code 의 **내장 read-only 집합**을
# 제외 목록으로 쓴다 — 이 집합은 공식 문서에 고정돼 있어 안정적이다.
_READ_ONLY_CMDS = frozenset(
    {"ls", "cat", "echo", "pwd", "head", "tail", "grep", "find", "wc", "which", "diff", "stat", "du", "cd"}
)
# git 은 서브커맨드로 갈린다 — 조회 계열만 read-only (tag/config/push 등은 상태를 바꾼다)
_GIT_READ_ONLY_SUB = frozenset(
    {"status", "log", "diff", "show", "ls-files", "rev-parse", "check-ignore", "blame", "describe"}
)
# 복합 명령 구분자 — 권한 규칙과 동일하게 각 서브명령을 독립 평가한다
_CMD_SEPARATOR_RE = re.compile(r"&&|\|\||\||;|\n")


def _subcommand_is_read_only(sub: str) -> bool:
    """서브명령 하나가 내장 read-only 집합에 속하나. 판단 불가는 False(fail-closed = 실질 작업)."""
    tokens = sub.strip().split()
    if not tokens:
        return True  # 빈 조각은 무시 (구분자 분해 부산물)
    head = tokens[0]
    if head == "git":
        return len(tokens) > 1 and tokens[1] in _GIT_READ_ONLY_SUB
    return head in _READ_ONLY_CMDS


def is_substantive_command(command: str) -> bool:
    """Bash 명령이 '실질 작업'인가 — 전 서브명령이 read-only 면 False.

    fail-closed: 모르는 명령은 실질 작업으로 센다. 상기(advisory)라 오탐 비용이 낮고,
    미탐(빌드·배포·학습이 검증 없이 지나감)의 비용이 훨씬 크다.
    """
    if not command or not command.strip():
        return False
    return not all(_subcommand_is_read_only(s) for s in _CMD_SEPARATOR_RE.split(command))


def verifier_remind_count(gate: dict[str, Any]) -> int:
    """승인 후 누적 작업 수 = 편집 + 실질 Bash. verifier_remind 발화 기준의 단일 진실 원천.

    두 훅(verifier_remind=Edit 경로, plan_gate_bash=Bash 경로)이 같은 총계를 보므로,
    한 작업이 총계를 1 올리고 짝수일 때만 발화 → 두 훅이 같은 총계에 이중 발화하지 않는다.
    """
    return gate.get("edit_count_post_approval", 0) + gate.get("bash_count_post_approval", 0)


# ── 편집 규모 추정 (큰 미승인 변경 환기용) ───────────────────────────────
_COMMENT_PREFIX_RE = re.compile(r"^\s*(#|//|/\*|\*|<!--|--|;)")


def _code_lines(text: str) -> int:
    """공백·단순 prefix 주석을 제외한 줄 수. 문자열 리터럴 안 주석은 보수적으로 코드로 셈."""
    n = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        if _COMMENT_PREFIX_RE.match(line):
            continue
        n += 1
    return n


def edit_added_code_lines(tool_name: str, tool_input: dict[str, Any], root: Path | None) -> int:
    """이 편집이 추가하는 코드 줄 수 추정. doc 파일은 0. 미지원/누락 필드는 0(fail-open)."""
    target = extract_target_file(tool_name, tool_input, project_root=root)
    if target and is_doc_path(target):
        return 0
    if tool_name == "Edit":
        return _code_lines(tool_input.get("new_string") or "")
    if tool_name == "Write":
        return _code_lines(tool_input.get("content") or "")
    if tool_name == "MultiEdit":
        return sum(_code_lines(e.get("new_string") or "") for e in tool_input.get("edits") or [])
    if tool_name == "NotebookEdit":
        return _code_lines(tool_input.get("new_source") or "")
    return 0


# ── doc 경로 판별 ───────────────────────────────────────────────────────
_DOC_PREFIXES = ("docs/", "tasks/", ".claude/memory/")
_DOC_SUFFIXES = (".md", ".rst", ".txt")
_DOC_NAMES = {"README.md", "CHANGELOG.md", "CLAUDE.md"}


def is_doc_path(fp: str) -> bool:
    """문서/정형 갱신 파일 여부. True이면 반복 편집 트리거 카운트에서 제외."""
    norm = fp.replace("\\", "/").lstrip("./")
    base = norm.rsplit("/", 1)[-1]
    if base in _DOC_NAMES:
        return True
    if any(norm.startswith(p) for p in _DOC_PREFIXES):
        return True
    if any(norm.endswith(s) for s in _DOC_SUFFIXES):
        return True
    return False


# ── tool input 분석 ─────────────────────────────────────────────────────
def extract_target_file(
    tool_name: str,
    tool_input: dict[str, Any],
    project_root: Path | None = None,
) -> str | None:
    if tool_name not in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return None
    # NotebookEdit 는 file_path 가 아닌 notebook_path 를 쓴다 (C-2 커버리지)
    fp = tool_input.get("notebook_path") if tool_name == "NotebookEdit" else tool_input.get("file_path")
    if not isinstance(fp, str):
        return None
    # 프로젝트 루트 외부 경로(메모리·설정 파일 등)는 카운터에서 제외
    if project_root is not None:
        try:
            Path(fp).resolve().relative_to(project_root.resolve())
        except (ValueError, OSError):
            return None
    return fp


def load_gate_ignore(root: Path) -> list[str]:
    """프로젝트 루트의 .plan-gateignore 파일에서 패턴 목록을 읽는다."""
    ignore_file = root / ".plan-gateignore"
    if not ignore_file.exists():
        return []
    patterns: list[str] = []
    for line in ignore_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def is_gate_ignored(file_path: str, root: Path, patterns: list[str]) -> bool:
    """파일이 .plan-gateignore 패턴에 해당하면 True."""
    if not patterns:
        return False
    p = Path(file_path)
    name = p.name
    try:
        rel = str(p.resolve().relative_to(root.resolve()))
    except (ValueError, OSError):
        rel = file_path
    return any(fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel, pat) for pat in patterns)


def auto_add_gate_ignore(file_path: str, root: Path, existing_patterns: list[str]) -> tuple[str, str] | None:
    """파일이 자동 추가 후보이고 아직 무시 목록에 없으면 .plan-gateignore에 패턴을 추가한다.

    추가된 경우 (pattern, reason)을 반환, 해당 없으면 None.
    """
    if is_gate_ignored(file_path, root, existing_patterns):
        return None
    name = Path(file_path).name
    for pattern, reason in _AUTO_IGNORE_CANDIDATES:
        if fnmatch.fnmatch(name, pattern):
            ignore_file = root / ".plan-gateignore"
            try:
                existing = ignore_file.read_text(encoding="utf-8", errors="ignore") if ignore_file.exists() else ""
                if existing and not existing.endswith("\n"):
                    existing += "\n"
                ignore_file.write_text(existing + pattern + "\n", encoding="utf-8")
                return pattern, reason
            except OSError:
                pass
    return None


# ── 매니페스트 파싱 (스코프 계약 — 강제는 scope_mode 플래그로 분기) ─────────
# tasks/todo.md 안의 짝 마커 블록에서 scope / do-not-touch 파일 패턴을 읽는다.
# 짝 마커(BEGIN/END)로 단일 마커의 "어디서 끝나는가" 모호성을 제거한다.
# 파싱 결과는 승인 시 gate.scope/do_not_touch 에 저장되고, 강제 여부는
# scope_mode(off|shadow|enforce) 가 결정한다(기본 shadow — 부재=shadow). 미선언/파싱
# 실패 → fail-open: 스코프 없음 = thrash-only 모드. 절대 default-deny 금지(결정 Q1).
MANIFEST_MARKERS: dict[str, tuple[str, str]] = {
    "scope": (
        "<!-- plan-gate: scope BEGIN -->",
        "<!-- plan-gate: scope END -->",
    ),
    "do_not_touch": (
        "<!-- plan-gate: do-not-touch BEGIN -->",
        "<!-- plan-gate: do-not-touch END -->",
    ),
}


def _extract_manifest_block(text: str, begin: str, end: str) -> list[str]:
    """begin/end 짝 마커 사이의 패턴 줄 목록. 마커 없거나 짝이 안 맞으면 빈 목록(fail-open)."""
    bi = text.find(begin)
    if bi < 0:
        return []
    bi += len(begin)
    ei = text.find(end, bi)
    if ei < 0:
        return []  # 종료 마커 없음 → 미선언 취급 (default-deny 금지)
    patterns: list[str] = []
    for line in text[bi:ei].splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("<!--"):
            continue
        s = re.sub(r"^[-*]\s+", "", s)  # 불릿(- / *) 접두 허용
        if s:
            patterns.append(s)
    return patterns


def parse_manifest(text: str | None) -> dict[str, list[str]] | None:
    """todo.md 본문에서 scope/do-not-touch 매니페스트 파싱.

    반환: {"scope": [...], "do_not_touch": [...]} 또는 매니페스트 미선언이면 None.
    scope 블록(짝 마커)이 비거나 없으면 None — 강제할 계약이 없다(fail-open).
    """
    if not text:
        return None
    sb, se = MANIFEST_MARKERS["scope"]
    scope = _extract_manifest_block(text, sb, se)
    if not scope:
        return None
    db, de = MANIFEST_MARKERS["do_not_touch"]
    return {"scope": scope, "do_not_touch": _extract_manifest_block(text, db, de)}


def manifest_sha(text: str | None) -> str | None:
    """매니페스트 블록 원문(마커 포함)의 sha256.

    주의: 현재 이 값을 비교하는 가드는 없다(gate 에 저장만 됨). 런 중 TOCTOU 검증은
    todo_md_sha256(cmd_approve 에서 비교)이 담당한다 — 이 함수는 매니페스트 영역만
    해시해 무관한 계획 본문 편집과 분리하는 용도로 남아 있다. 매니페스트 없으면 None.
    """
    if not text:
        return None
    chunks: list[str] = []
    for begin, end in MANIFEST_MARKERS.values():
        bi = text.find(begin)
        if bi < 0:
            continue
        ei = text.find(end, bi)
        if ei < 0:
            continue
        chunks.append(text[bi : ei + len(end)])
    if not chunks:
        return None
    return hashlib.sha256("\n".join(chunks).encode("utf-8")).hexdigest()


def has_manifest(gate: dict[str, Any]) -> bool:
    """게이트가 스코프 매니페스트를 선언했는지. False → thrash-only 모드(스코프 강제 없음)."""
    return bool(gate.get("scope"))


def is_broad_glob(pattern: str) -> bool:
    """자동승인 비활성 대상인 '넓은 글롭' 판별 (D6).

    `**`·`*`·최상위 컴포넌트가 글롭인 패턴(`**/x`, `*/x`)은 사실상 전체 우회
    탈출구라 자동승인에서 배제하고 사람 /approve-plan 을 강제한다.
    `src/auth/**` 처럼 디렉토리로 한정된 글롭은 넓지 않다.
    """
    p = pattern.strip().strip("/")
    if p in ("", ".", "*", "**"):
        return True
    return p.split("/", 1)[0] in ("*", "**")


def manifest_has_broad_glob(manifest: dict[str, list[str]] | None) -> bool:
    """scope 패턴 중 넓은 글롭이 하나라도 있으면 True (자동승인 비활성, D6)."""
    if not manifest:
        return False
    return any(is_broad_glob(p) for p in manifest.get("scope", []))


def apply_manifest(root: Path, gate: dict[str, Any]) -> dict[str, list[str]] | None:
    """todo.md 매니페스트를 파싱해 gate 에 저장(scope/do_not_touch/manifest_sha256).

    사람 승인(/approve-plan) 경로용 — 넓은 글롭 가드는 적용하지 않는다(사람이 곧
    검토 게이트). 매니페스트 없으면 스코프 필드를 비운다(fail-open, thrash-only).
    반환: 파싱된 매니페스트 또는 None.
    """
    p = todo_md_path(root)
    try:
        text = p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""
    except OSError:
        text = ""
    manifest = parse_manifest(text)
    gate["scope"] = manifest["scope"] if manifest else []
    gate["do_not_touch"] = manifest["do_not_touch"] if manifest else []
    gate["manifest_sha256"] = manifest_sha(text) if manifest else None
    return manifest


# ── path-aware 글롭 매처 (R3 — fnmatch 의 `*`→`/` 삼킴 결함 제거) ──────────
def _glob_to_regex(pattern: str) -> str:
    """글롭 패턴을 path-aware 정규식 문자열로 변환.

    `*` = 한 경로 컴포넌트 내(슬래시 미포함), `**`(+옵션 `/`) = 0개 이상 컴포넌트
    횡단, `?` = 슬래시 아닌 한 글자. 나머지는 리터럴. 선행/후행 `/` 는 정규화.
    """
    p = pattern.strip().strip("/")
    out = ["^"]
    i, n = 0, len(p)
    while i < n:
        c = p[i]
        if c == "*":
            if i + 1 < n and p[i + 1] == "*":
                if i + 2 < n and p[i + 2] == "/":
                    out.append("(?:.*/)?")  # `**/x` → x, a/x, a/b/x 모두 매칭
                    i += 3
                else:
                    out.append(".*")  # 후행 `**` → 서브트리 전체
                    i += 2
            else:
                out.append("[^/]*")  # 단일 `*` → 한 컴포넌트 내
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    out.append("$")
    return "".join(out)


def _path_match(rel: str, pattern: str) -> bool:
    """rel(루트 상대경로)이 path-aware 글롭 pattern 에 매칭되는지 (R3)."""
    norm = rel.replace("\\", "/").strip("/")
    try:
        return re.match(_glob_to_regex(pattern), norm) is not None
    except re.error:
        return False


def expansion_hits_deny(pattern: str, do_not_touch: list[str]) -> bool:
    """확장 패턴이 do-not-touch 와 겹치면 True (subplan 입력 단계 deny-first, F-009).

    deny-first 를 enforcement(scope_allows) 시점에만 적용하면 do-not-touch 패턴도
    일단 expansions 에 들어가 "확장됨" 으로 오인 출력된다(inert 죽은 데이터).
    입력 시점에 미리 거른다. 패턴 대 패턴 정밀 교집합은 비싸므로 보수적 근사 —
    겹침을 과탐(거부) 방향으로 기운다(deny-list 안전 방향):
    - 동일 패턴, 또는
    - 확장 패턴의 글롭을 벗긴 대표 경로가 do-not-touch 글롭에 매칭(서브경로 포함), 또는
    - do-not-touch 의 대표 경로가 확장 글롭에 매칭(상위경로 포함)
    """
    rep_p = pattern.replace("**", "x").replace("*", "x")
    for d in do_not_touch:
        if pattern == d:
            return True
        rep_d = d.replace("**", "x").replace("*", "x")
        if _path_match(rep_p, d) or _path_match(rep_d, pattern):
            return True
    return False


# ── control-plane allowlist (R2 — 강제 ON 이어도 무조건 허용) ──────────────
# 매니페스트·게이트 상태·검증결과·ignore 파일을 스코프밖으로 막으면 /replan·
# subplan·verifier 핸드셰이크가 자멸한다(rev.3 R2). 이 패턴은 계약과 무관하게 허용.
# ⚠️ `.claude/**` 전체 허용은 과잉(H-5): `.claude/hooks/*.py`·`agents/*.md` 같은
# 실행 코드/스펙까지 스코프 강제 밖이 된다. plan-gate 가 *실제 쓰는* 운영 파일만
# 허용한다 — state 디렉토리 + `plan_gate_*` 플래그 파일(enabled/scope/no_git/
# off_explicit). 플래그가 untracked·스코프밖이라 스윕이 지우면 자멸하므로 이들만 면제.
_CONTROL_PLANE_ALLOW = (
    "tasks/todo.md",
    ".claude/state/**",
    ".claude/plan_gate_*",
    "docs/.verifier_result.json",
    ".plan-gateignore",
)


def is_control_plane(rel: str) -> bool:
    """plan-gate 자체 운영 파일(매니페스트·상태·검증결과·ignore)인지 (R2 allowlist)."""
    return any(_path_match(rel, pat) for pat in _CONTROL_PLANE_ALLOW)


def scope_allows(
    target: str,
    gate: dict[str, Any],
    root: Path,
    ignore_patterns: list[str] | None = None,
) -> bool:
    """target(상대/절대)이 게이트 스코프 계약상 허용되는지 (deny-first).

    - 매니페스트 미선언(has_manifest False) → True (fail-open, thrash-only).
    - control-plane(매니페스트·상태·검증결과·ignore) → True (R2, 무조건 허용).
    - .plan-gateignore 일치 → True (생성물/락파일은 스코프 검사 우회).
    - do-not-touch 일치 → False (scope 보다 우선, detour 로도 못 품).
    - scope 일치 → True, 아니면 False(루트 밖 포함).

    매칭은 루트 상대경로 정규화 기준 path-aware 글롭(_path_match, R3) — fnmatch 의
    '`*` 가 `/` 까지 삼킴' 결함을 제거해 계약이 적힌 것보다 넓게 허용하지 않는다.
    """
    if not has_manifest(gate):
        return True
    rel = _rel_to_root(root, target)
    if rel is None:
        return False  # 루트 밖 — 스코프 안에 있을 수 없다
    if is_control_plane(rel):
        return True  # R2: plan-gate 운영 파일은 강제 ON 이어도 무조건 허용
    if ignore_patterns is None:
        ignore_patterns = load_gate_ignore(root)
    if is_gate_ignored(str(root / rel), root, ignore_patterns):
        return True
    if any(_path_match(rel, pat) for pat in gate.get("do_not_touch", [])):
        return False  # deny-first — subplan 확장으로도 do-not-touch 는 못 뚫는다
    allowed = gate.get("scope", []) + gate.get("expansions", [])
    return any(_path_match(rel, pat) for pat in allowed)


# ── 차단/환기 안정 코드 ─────────────────────────────────────────────────
# 사람·기계 공용 진단 키 — "어느 가드가 왜 막았나"를 코드 하나로 판별한다.
# 사용자 메시지 헤더에 [코드] 로 태깅되고 audit action 과 1:1 매핑된다
# (사용자에게 모든 차단이 'plan-gate 하나'로 보여 원인 파악이 어려운 문제 해소).
# 코드 문자열은 안정 계약 — 변경 시 docs/MANUAL.md·plan-gate-help 스킬·smoke_test
# [42] 를 함께 갱신한다. failure-loop 가드는 별도 시스템이라 FL- 접두를 쓴다
# (detect_failure_loop.py 의 FL-LOOP — plan-gate 아님을 코드로 드러낸다).
CODE_TRIGGER = "PG-TRIGGER"  # 복잡도 임계 → 계획 승인 필요 (audit: trigger)
CODE_D1_LOCK = "PG-D1"  # verifier ❌ 미해결 편집 잠금 (audit: 없음 — 판정은 update_docs)
CODE_THRASH = "PG-THRASH"  # 같은 파일 반복 편집 (audit: thrash_approved)
CODE_SCOPE_L1 = "PG-SCOPE-L1"  # layer-1 스코프 밖 편집 거부 (audit: scope_deny_enforced)
CODE_DNT = "PG-DNT"  # do-not-touch 위반 거부 (audit: scope_deny_enforced)
CODE_SCOPE_SHADOW = "PG-SCOPE-SHADOW"  # shadow 위반 감지 (audit: scope_deny_shadow/scope_violation_shadow)
CODE_SCOPE_L2 = "PG-SCOPE-L2"  # layer-2 git-status 스윕 (audit: scope_violation_enforced)


def format_scope_deny(rel: str, gate: dict[str, Any]) -> str:
    """layer-1 스코프 밖 편집 거부 사유 (permissionDecisionReason, enforce)."""
    scope = ", ".join(gate.get("scope", [])) or "(없음)"
    dnt = gate.get("do_not_touch") or []
    dnt_line = f"\n  do-not-touch: {', '.join(dnt)}" if dnt else ""
    # do-not-touch 위반은 스코프 이탈과 원인이 다르다(subplan 으로도 못 품) — 코드 구분
    code = CODE_DNT if any(_path_match(rel, p) for p in dnt) else CODE_SCOPE_L1
    return (
        f"[plan-gate] 🛑 [{code}] 스코프 밖 편집 거부: {rel}\n"
        f"  이번 계획의 scope: {scope}{dnt_line}\n"
        f"  scope 안의 파일만 수정하거나, 계획 변경이 필요하면 /replan 으로 "
        f"tasks/todo.md 의 매니페스트를 갱신한 뒤 /approve-plan 하세요."
    )


def format_scope_shadow(rel: str, layer: str) -> str:
    """shadow 모드 위반 환기 (차단·롤백 없음 — additionalContext 용)."""
    return (
        f"[plan-gate] 👁  [{CODE_SCOPE_SHADOW}] 스코프 위반 감지(shadow, {layer}): {rel}\n"
        f"  enforce 모드였다면 거부/롤백됐을 변경입니다. 현재는 기록만 합니다 "
        f"(audit log)."
    )


def _bullet(items: list[str], n: int = 10) -> str:
    shown = "\n".join(f"  • {x}" for x in items[:n])
    more = f"\n  • ... ({len(items) - n}개 더)" if len(items) > n else ""
    return shown + more


def format_scope_sweep(
    removed: list[str], warned: list[str], effective: str, requested: str
) -> str:
    """layer-2 스윕 결과 환기 (Claude desync 방지, additionalContext 용).

    removed=enforce 가 삭제한 스코프 밖 신규 파일, warned=되돌리지 않고 경고만 한
    스코프 밖 변경(기존 파일 수정/shadow). requested↔effective 가 다르면(스냅샷 없어
    enforce→shadow 강등) 그 사실을 명시한다.
    """
    parts = [f"\n{DIVIDER}", f"[plan-gate] [{CODE_SCOPE_L2}] 스코프 위반 감지 (layer-2 / git-status 스윕)"]
    if removed:
        parts.append(f"↩️  스코프 밖 신규 변경 {len(removed)}건 롤백(삭제, enforce):")
        parts.append(_bullet(removed))
    if warned:
        why = (
            "기존 파일 수정 — 되돌리지 않음(사용자 직접 편집 보호)"
            if effective == "enforce"
            else "감지·기록만(shadow)"
        )
        parts.append(f"👁  스코프 밖 {len(warned)}건 {why}:")
        parts.append(_bullet(warned))
    if requested == "enforce" and effective != "enforce":
        parts.append(
            "⚠️  체크포인트 스냅샷이 없어 enforce 롤백을 건너뛰고 감지만 했습니다 "
            "— 신규 파일도 삭제하지 않았습니다(무백업 삭제 방지)."
        )
    parts.append(
        "필요한 변경이면 tasks/todo.md scope 에 추가(또는 /subplan)하세요. "
        "남은 스코프 밖 변경은 최종 diff 로 사용자가 검토합니다."
    )
    parts.append(DIVIDER)
    return "\n".join(parts)


def format_broad_glob_hint(manifest: dict[str, list[str]]) -> str:
    """넓은 글롭 매니페스트 주의 안내 (additionalContext 용, D6)."""
    broad = ", ".join(p for p in manifest.get("scope", []) if is_broad_glob(p))
    return (
        f"\n{DIVIDER}\n"
        f"[plan-gate] ⚠️  넓은 글롭 매니페스트 — 사람 검토 필요\n"
        f"{DIVIDER}\n"
        f"  scope 에 넓은 글롭이 있어 사람 검토가 필요합니다: {broad}\n"
        f"  넓은 글롭(`**`·최상위 글롭)은 스코프 계약을 사실상 무력화합니다.\n"
        f"  디렉토리로 한정하거나(예: src/auth/**), 의도한 것이면\n"
        f"  /approve-plan 으로 명시 승인하세요.\n"
        f"{DIVIDER}\n"
    )


# ── 토큰 정의 (D6) ──────────────────────────────────────────────────────
APPROVE_TOKENS = {"/approve-plan"}
DONE_TOKENS = {"/done"}
ROLLBACK_TOKENS = {"/rollback"}
RETRY_TOKENS = {"/retry"}
REPLAN_TOKENS = {"/replan"}
SKIP_TOKENS = {"/skip", "/keep"}  # /keep은 /skip의 별칭 — 둘 다 동일하게 동작

ALL_TOKENS = APPROVE_TOKENS | DONE_TOKENS | ROLLBACK_TOKENS | RETRY_TOKENS | REPLAN_TOKENS | SKIP_TOKENS


def detect_token(prompt: str) -> str | None:
    s = (prompt or "").strip()
    if s in ALL_TOKENS:
        return s
    return None


# ── intro flag (dismissable 안내) ───────────────────────────────────────
def intro_flag_path(root: Path) -> Path:
    return root / ".claude" / "state" / "plan_gate_intro_seen.flag"


def intro_seen(root: Path) -> bool:
    return intro_flag_path(root).exists()


def mark_intro_seen(root: Path) -> None:
    p = intro_flag_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(now_iso(), encoding="utf-8")


# ── git diff 요약 (Claude에게 컨텍스트 + 사용자에게 진행 상황 정보) ──────
def git_diff_summary(root: Path, max_diff_lines: int = 80) -> str:
    """차단 시점의 변경 사항 요약. stat + 일부 diff."""
    if not has_git(root):
        return "(git 미사용 — diff 정보 없음)"
    stat = _git(root, "diff", "--stat", "HEAD").stdout.strip()
    diff = _git(root, "diff", "HEAD", "--no-color").stdout.splitlines()
    diff_head = "\n".join(diff[:max_diff_lines])
    truncated = len(diff) > max_diff_lines
    parts = []
    if stat:
        parts.append("변경 통계:")
        parts.append(stat)
    if diff_head:
        parts.append("")
        parts.append("변경 일부 (앞 %d줄):" % max_diff_lines)
        parts.append("```")
        parts.append(diff_head)
        if truncated:
            parts.append(f"... ({len(diff) - max_diff_lines}줄 생략)")
        parts.append("```")
    return "\n".join(parts) if parts else "(변경 없음)"


# ── 트리거 사유 자연어화 ────────────────────────────────────────────────
def trigger_reason_human(gate: dict[str, Any]) -> str:
    reasons = []
    max_repeat = _max_code_repeat(gate)
    if max_repeat >= TRIGGER_REPEAT_RATIO:
        counts = gate.get("file_edit_counts", {})
        hot_file = max((fp for fp in counts if not is_doc_path(fp)), key=lambda f: counts[f], default="?")
        reasons.append(
            f"동일 파일 반복 편집 — {hot_file} {max_repeat}회 (임계 {TRIGGER_REPEAT_RATIO}회)"
        )
    return " / ".join(reasons) or "임계값 도달"


def _files_list(gate: dict[str, Any], max_n: int = 6) -> str:
    files = gate.get("unique_files", [])
    if not files:
        return "(없음)"
    if len(files) <= max_n:
        return "\n".join(f"  • {f}" for f in files)
    head = "\n".join(f"  • {f}" for f in files[:max_n])
    return head + f"\n  • ... ({len(files) - max_n}개 더)"


# ── 메시지 빌더 ─────────────────────────────────────────────────────────
DIVIDER = "━" * 60


def _intro_block() -> str:
    """첫 차단에만 함께 보여주는 plan-gate 소개."""
    return (
        "▌ plan-gate 란?\n"
        "  큰 변경을 사용자가 검토하지 못한 채로 진행되는 것을 막는 자동 게이트입니다.\n"
        "  게이트가 열릴 때 working tree 를 git 프라이빗 ref(비-git 은 cp 디렉토리)로\n"
        "  자동 스냅샷하므로, /rollback 으로 안전하게 되돌릴 수 있습니다.\n"
        "  비활성화: /plan-gate-off 입력 (.claude/plan_gate_enabled 삭제).\n"
        "  카운트 제외: 문서·메타파일은 .plan-gateignore 에 패턴을 추가하면\n"
        "              편집 카운터에서 빠집니다.\n"
        "  이 안내는 한 번만 표시됩니다.\n"
    )


def format_soft_hint(gate: dict[str, Any]) -> str:
    """트리거 직전 부드러운 경고 (차단 X)."""
    max_repeat = _max_code_repeat(gate)
    counts = gate.get("file_edit_counts", {})
    hot_file = max((fp for fp in counts if not is_doc_path(fp)), key=lambda f: counts[f], default=None)
    hot_info = f" ({hot_file} {max_repeat}회)" if hot_file else ""
    return (
        f"\n{DIVIDER}\n"
        f"⚠️  plan-gate 임박\n"
        f"{DIVIDER}\n"
        f"현재까지 {gate['edit_count']}회 편집 / 코드 파일 {_unique_code_files(gate)}개{hot_info}.\n"
        f"큰 작업이라면 미리 tasks/todo.md 에 계획을 작성해두는 것이 좋습니다.\n"
        f"{DIVIDER}\n"
    )


def format_plan_worthiness_cue() -> str:
    """새 작업 진입 시 '계획 필요 여부 자가 판단' 가벼운 재무장 (비차단 환기)."""
    return (
        "[plan-gate] 새 작업 — 진행하며 범위를 파악하고 스스로 판단하세요:\n"
        "  다단계·위험·기존 동작 변경이면 tasks/todo.md + /approve-plan 후 구현,\n"
        "  작고 가역적인 작업이면 그대로 진행(approve·done 불요). 과잉 계획 금지."
    )


def format_large_edit_advisory(added: int, fan: int) -> str:
    """큰 미승인 변경 → /approve-plan 권장 (비차단 환기, 강제 아님)."""
    reasons = []
    if added >= LARGE_OP_LINES:
        reasons.append(f"한 번에 코드 {added}줄 추가")
    if fan >= LARGE_FAN_FILES:
        reasons.append(f"코드 파일 {fan}개 변경")
    return (
        "[plan-gate] 📐 변경 규모가 큽니다 (" + ", ".join(reasons) + ").\n"
        "  계획되지 않은 큰 변경이라면, 진행 전 tasks/todo.md 에 계획을 적고\n"
        "  /approve-plan 으로 승인받는 것을 권장합니다.\n"
        "  (강제 아님 — 작고 단순한 작업이거나 이미 의도한 변경이면 무시하세요.)"
    )


def format_trigger_message(
    gate: dict[str, Any],
    show_intro: bool,
    diff_summary: str,
) -> str:
    """첫 plan-gate 발동 메시지 (사용자+Claude 둘 다 봄)."""
    parts = [
        "",
        DIVIDER,
        f"🛑 [{CODE_TRIGGER}] PLAN-GATE 차단됨 — 사용자 계획 승인 필요",
        DIVIDER,
        "",
        "▌ 왜 멈췄나",
        f"  복잡도 임계값 도달: {trigger_reason_human(gate)}",
        "",
        "▌ 지금까지 한 일",
        f"  • 파일 편집 {gate['edit_count']}회 / 영향 파일 {len(gate['unique_files'])}개",
        "  • 영향 파일 목록:",
        _files_list(gate),
    ]
    _ckpt = gate.get("checkpoint_commit")
    parts += [
        "",
        "▌ 자동으로 생성된 체크포인트",
        f"  • 스냅샷: {(_ckpt[:12] + ' (프라이빗 ref)') if _ckpt else 'cp 디렉토리 (git 미사용/opt-out)'}",
        "  ℹ️  편집 전 상태가 보존됐습니다. /rollback 으로 안전하게 되돌립니다.",
        "",
        "▌ 사용자에게 다음 토큰 중 하나 입력 요청",
        "  /approve-plan  계획을 승인하고 작업 재개",
        "  /replan        계획을 다시 짜고 재승인 (체크포인트 유지)",
        "  /rollback      체크포인트로 working tree 복원",
        "",
        "▌ 변경 사항 요약 (Claude 가 todo.md 작성에 활용)",
        diff_summary,
        "",
        "▌ Claude 행동 지시",
        "  0. 즉시 사용자에게 보고: 차단 이유·현재 상황을 한국어로 먼저 알린다.",
        "  1. 위 변경 사항·영향 파일을 근거로 tasks/todo.md 에",
        "     다음을 포함한 계획을 작성한다:",
        "     - 의도 한 줄 (왜 이 작업이 필요한가)",
        "     - ## 근본 원인 (증상이 아닌 원인. 임시 수정이면 '임시 수정' 명시)",
        "     - ## 해결 방법 (왜 이 접근 방식인가)",
        "     - 단계별 체크리스트 (- [ ] 형식, 최소 2개)",
        "     - 완료 기준: 기계적으로 판별 가능한 형태로 작성",
        "       예) pytest 0 failures / 재현 스크립트 에러 없이 종료",
        "  ★ 외과적 변경 원칙: '건드리면 안 되는 파일'도 명시할 것.",
        "     인접 코드·주석·포맷은 변경 대상이 아닌 한 손대지 않는다.",
        "  2. 사용자에게 위 안내를 한국어로 자연스럽게 풀어 안내한다.",
        "  3. 사용자가 토큰을 입력할 때까지 추가 Edit/Write 시도하지 않는다.",
        "",
    ]
    if show_intro:
        parts.append(_intro_block())
    parts += [DIVIDER, ""]
    return "\n".join(parts)


def format_multi_edit_hint(file_path: str) -> str:
    """동일 파일 재편집 감지 — 편집 묶기 유도 힌트 (additionalContext 용).

    주의: MultiEdit 툴은 현행 Claude Code 에서 제거됨 — 존재하지 않는 툴을
    권하지 않는다. 같은 응답 안에 Edit 을 모으거나 replace_all 을 권한다.
    """
    short = file_path.split("/")[-1]
    return (
        f"[plan-gate] {short} 은 이번 작업에서 이미 수정됐습니다. "
        f"같은 파일의 추가 수정은 한 응답 안에 Edit 호출을 모아서 처리하거나, "
        f"동일 문자열 반복 치환이면 replace_all 을 사용하세요. "
        f"(편집 호출마다 plan-gate 카운터가 누적됩니다.)"
    )


def format_d1_lock_message(gate: dict[str, Any]) -> str:
    """verifier ❌ 미해결 상태에서 새 Edit 시도 시."""
    has_ckpt = bool(gate.get("checkpoint_commit") or gate.get("cp_snapshot"))
    rollback_line = (
        "  /rollback  체크포인트로 복원 (이번 시도 폐기)\n"
        if has_ckpt
        else "  /rollback  ⚠️  체크포인트 없음 — 사용 불가 (/skip 또는 /done 권장)\n"
    )
    return (
        f"\n{DIVIDER}\n"
        f"🛑 [{CODE_D1_LOCK}] PLAN-GATE LOCK — 이전 작업 결정 대기 중\n"
        f"{DIVIDER}\n"
        f"\n"
        f"▌ 상태\n"
        f"  gate {gate['id']} 는 verifier 검증 ❌ 후 사용자 결정을 기다리고 있습니다.\n"
        f"  새 코드 수정 전에 이전 작업을 먼저 해결해야 합니다.\n"
        f"\n"
        f"▌ 사용자에게 다음 토큰 중 하나 입력 요청\n"
        f"  /retry     같은 체크포인트에서 재시도 (Claude 가 문제를 수정)\n"
        f"  /skip      현재 상태 그대로 gate 마감 (문제 인지 후 유지)\n"
        f"  /done      현재 상태 그대로 gate 마감 (/skip 과 동일)\n"
        f"{rollback_line}"
        f"\n"
        f"▌ Claude 행동 지시\n"
        f"  0. 즉시 사용자에게 보고: verifier 실패 이유와 선택지를 한국어로 먼저 알린다.\n"
        f"  1. 사용자가 토큰을 입력할 때까지 추가 Edit/Write 시도하지 않는다.\n"
        f"\n{DIVIDER}\n"
    )


def format_thrash_message(gate: dict[str, Any]) -> str:
    """승인 후 같은 파일 반복(thrash/flailing) 임계 도달 시 (차단)."""
    max_repeat = _max_code_repeat(gate)
    return (
        f"\n{DIVIDER}\n"
        f"🛑 [{CODE_THRASH}] PLAN-GATE — 같은 파일 반복 편집(수렴 안 됨)\n"
        f"{DIVIDER}\n"
        f"\n"
        f"▌ 무슨 일이?\n"
        f"  같은 코드 파일을 {max_repeat}회 반복 편집했습니다 (임계 {TRIGGER_REPEAT_RATIO}회).\n"
        f"  테스트가 통과하면(green Bash) 카운터가 리셋됩니다 — 계속 막힌다면\n"
        f"  접근 방식 자체를 재검토할 신호입니다.\n"
        f"\n"
        f"▌ 사용자에게 다음 토큰 중 하나 입력 요청\n"
        f"  /done      현재까지를 완료로 마감\n"
        f"  /replan    todo.md 갱신 후 재승인 (체크포인트 유지)\n"
        f"  /rollback  체크포인트로 복원\n"
        f"\n"
        f"▌ Claude 행동 지시\n"
        f"  0. 즉시 사용자에게 보고: 같은 파일이 수렴 없이 반복됨을 한국어로 알린다.\n"
        f"  1. 접근을 바꿀지(재검토) 현재로 마감할지 사용자가 정하게 안내한다.\n"
        f"  2. 사용자가 토큰을 입력할 때까지 추가 Edit/Write 시도하지 않는다.\n"
        f"\n{DIVIDER}\n"
    )


# ── gate done 공통 로직 ──────────────────────────────────────────────────


def do_gate_done(root: Path, state: dict[str, Any], gate: dict[str, Any]) -> bool:
    """gate를 done 상태로 닫고 체크포인트를 정리한다. enforce였으면 shadow로 복귀.
    plan_gate_cli.cmd_done 과 detect_task_boundary 에서 공유.
    반환: 스코프 모드가 enforce→shadow 로 자동 복귀했으면 True (호출자가 환기에 사용).
    """
    cleanup_checkpoint(root, gate)  # 프라이빗 ref 삭제 + cp 디렉토리 정리
    gate["state"] = "done"
    gate["closed_at"] = now_iso()
    # 완료 시점의 todo.md 해시를 보관 → 다음 사이클 자동 승인 가드에 활용
    try:
        sha, _ = hash_todo_md(root)
        gate["archived_todo_sha"] = sha
    except Exception:
        pass
    clear_current_gate(state)
    save_state(root, state)  # 상태 저장이 patch_history 기록보다 우선
    try:
        record_gate_closed(root, gate)
    except Exception:
        pass
    return revert_scope_if_enforced(root)  # 게이트 닫힘 = 사이클 종료 → stale enforce 청소


def do_gate_rolled_back(root: Path, state: dict[str, Any], gate: dict[str, Any]) -> bool:
    """gate 를 rolled_back 으로 닫는 단일 출처 (do_gate_done 의 rollback 짝).

    체크포인트 *복원*은 호출자(rollback_checkpoint)가 먼저 수행한다 — 여기서는
    상태 마감·저장·stale enforce 청소만 담당한다. closed_at 기록으로 GC(30일
    경과 종료 게이트 청소)가 rolled_back 도 정확한 시각 기준으로 정리한다.
    반환: enforce→shadow 자동 복귀 여부 (do_gate_done 과 동일 — 호출자가 환기).
    """
    gate["state"] = "rolled_back"
    gate["closed_at"] = now_iso()
    clear_current_gate(state)
    save_state(root, state)
    return revert_scope_if_enforced(root)


def last_archived_todo_sha(state: dict[str, Any]) -> str | None:
    """직전 done 사이클에서 기록한 todo.md 해시를 반환. 없으면 None.

    호출자가 이미 로드한 state 를 받는다 — 디스크 재읽기 방지.
    """
    done_gates = [
        g for g in state.get("gates", {}).values()
        if g.get("state") == "done" and g.get("archived_todo_sha")
    ]
    if not done_gates:
        return None
    latest = max(done_gates, key=lambda g: g.get("closed_at") or g.get("created_at") or "")
    return latest.get("archived_todo_sha")


# ── 패치 이력 (누더기 코드 방지) ─────────────────────────────────────────


def patch_history_path(root: Path) -> Path:
    return root / ".claude" / "state" / "patch_history.json"


def _load_patch_history(root: Path) -> dict[str, Any]:
    p = patch_history_path(root)
    if not p.exists():
        return {"file_edits": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {"file_edits": {}}


def _save_patch_history(root: Path, history: dict[str, Any]) -> None:
    p = patch_history_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def record_gate_closed(root: Path, gate: dict[str, Any]) -> None:
    """gate /done 시 unique_files를 patch_history에 기록."""
    files = gate.get("unique_files") or []
    if not files:
        return
    history = _load_patch_history(root)
    ts = now_iso()
    gate_id = gate["id"]
    for f in files:
        entries: list[dict[str, Any]] = history["file_edits"].setdefault(f, [])
        entries.append({"gate_id": gate_id, "ts": ts})
        if len(entries) > PATCH_MAX_ENTRIES_PER_FILE:
            history["file_edits"][f] = entries[-PATCH_MAX_ENTRIES_PER_FILE:]
    _save_patch_history(root, history)


def hot_file_check(root: Path, file_path: str | None) -> tuple[str | None, int]:
    """파일의 세션 간 수정 빈도 검사. 반환: ("warn"|"block"|None, count)"""
    if not file_path:
        return None, 0
    history = _load_patch_history(root)
    entries = history.get("file_edits", {}).get(file_path, [])
    now = datetime.now(timezone.utc)

    def _count(days: int) -> int:
        cutoff = now - timedelta(days=days)
        count = 0
        for e in entries:
            try:
                ts = datetime.fromisoformat(e["ts"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    count += 1
            except Exception:
                pass
        return count

    block_count = _count(PATCH_BLOCK_DAYS)
    if block_count >= PATCH_BLOCK_THRESHOLD:
        return "block", block_count

    warn_count = _count(PATCH_WARN_DAYS)
    if warn_count >= PATCH_WARN_THRESHOLD:
        return "warn", warn_count

    return None, 0


def format_hot_file_warn(file_path: str, level: str, count: int) -> str:
    short = file_path.split("/")[-1]
    if level == "warn":
        return (
            f"\n[hot-file] ⚠️  {short}: 최근 {PATCH_WARN_DAYS}일 내 {count}개 작업에서 수정됨.\n"
            f"  패치 누적 가능성 — 수정 전 리팩터링 필요 여부를 검토하세요.\n"
        )
    return (
        f"\n{DIVIDER}\n"
        f"[hot-file] 🔶 반복 수정 경고: {short}\n"
        f"{DIVIDER}\n"
        f"\n"
        f"  최근 {PATCH_BLOCK_DAYS}일 내 {count}개 작업에서 수정됨.\n"
        f"  패치가 누적되고 있을 수 있습니다.\n"
        f"\n"
        f"  권장 행동:\n"
        f"  1. tasks/todo.md에 리팩터링 필요 여부 또는 반복 수정 이유를 명시\n"
        f"  2. 밴드에이드 픽스라면 근본 원인 해결 시점을 기록\n"
        f"  작업은 계속 진행됩니다.\n"
        f"\n{DIVIDER}\n"
    )


# ── todo.md 품질 검증 ─────────────────────────────────────────────────────


def validate_todo_quality(root: Path) -> tuple[bool, list[str]]:
    """tasks/todo.md 최소 구조 검증. 반환: (통과 여부, 미달 항목 목록)"""
    p = todo_md_path(root)
    if not p.exists():
        return False, ["tasks/todo.md 없음"]
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False, ["tasks/todo.md 읽기 실패"]

    issues: list[str] = []
    stripped = text.strip()
    if len(stripped) < 30:
        issues.append(f"내용 부족 (현재 {len(stripped)}자, 최소 30자)")

    checklist_count = len(re.findall(r"- \[ \]", text))
    if checklist_count < 2:
        issues.append(
            f"체크리스트 항목 부족 (현재 {checklist_count}개, 최소 2개 — `- [ ]` 형식)"
        )

    # 섹션 헤더(## 이상) 또는 의도 문장(왜/목적/이유/because/goal/fix 등) 필요.
    # checkbox 나열만으로는 "왜 하는지"를 알 수 없어 승인 의미가 없다.
    has_section = bool(re.search(r"^#{1,3} .+", text, re.MULTILINE))
    has_intent = bool(
        re.search(
            r"(목적|이유|배경|왜|goal|because|fix|improve|add|remove|refactor|문제|원인)",
            text,
            re.IGNORECASE,
        )
    )
    if not has_section and not has_intent:
        issues.append(
            "의도·목적 없음 — '## 섹션' 헤더 또는 목적/이유를 설명하는 문장이 필요합니다"
        )

    return len(issues) == 0, issues


def format_todo_quality_hint(issues: list[str]) -> str:
    lines = ["", "[plan-gate] ⚠️  tasks/todo.md 계획 보강 필요 (승인 전 권장)"]
    for issue in issues:
        lines.append(f"  - {issue}")
    lines += [
        "  보강 후 /approve-plan 으로 명시 승인하세요 (승인 전까지 구현 게이트는 열리지 않습니다).",
        "  (권장: ## 목표 섹션 + 이유 한 줄 + `- [ ]` 체크리스트 2개 이상)",
        "",
    ]
    return "\n".join(lines)
