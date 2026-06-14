"""plan-gate 공통 라이브러리.

상태 관리, 체크포인트(git tag/stash), 트리거 휴리스틱, 프로젝트 감지를 담당.
훅 스크립트(plan_gate.py, plan_approval.py, plan_gate_cli.py, plan_gate_gc.py,
update_docs.py)에서 공유한다.

상태 파일: <project>/.claude/state/plan_gate.json
체크포인트: git tag `.claude/gate/<gate_id>/clean`
            git stash entry (message에 gate_id 포함)
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


def enable_plan_gate(root: Path) -> None:
    p = root / PLAN_GATE_FLAG
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(now_iso() + "\n")


def disable_plan_gate(root: Path) -> None:
    p = root / PLAN_GATE_FLAG
    if p.exists():
        p.unlink()


PREFER_NO_GIT_FLAG = ".claude/plan_gate_no_git"


def prefers_no_git(root: Path) -> bool:
    """.claude/plan_gate_no_git 존재 시 git 체크포인트를 끄고 cp 스냅샷을 강제한다.

    git repo 이면서도 plan-gate 가 git tag/stash 를 만들지 않기를 원하는 사용자용
    명시적 opt-out. (예: 별도 VCS 워크플로우와 충돌 회피, git 추적 비선호)
    """
    return (root / PREFER_NO_GIT_FLAG).exists()


def set_prefer_no_git(root: Path) -> None:
    p = root / PREFER_NO_GIT_FLAG
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(now_iso() + "\n")


def unset_prefer_no_git(root: Path) -> None:
    p = root / PREFER_NO_GIT_FLAG
    if p.exists():
        p.unlink()


# ── 상태 파일 입출력 ─────────────────────────────────────────────────────
def state_path(root: Path) -> Path:
    return root / ".claude" / "state" / "plan_gate.json"


def load_state(root: Path) -> dict[str, Any]:
    p = state_path(root)
    if not p.exists():
        return {"current_gate_id": None, "gates": {}}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {"current_gate_id": None, "gates": {}}


def save_state(root: Path, state: dict[str, Any]) -> None:
    p = state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
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
    """target(상대/절대)을 루트 상대경로로 정규화. 루트 밖이면 None."""
    p = Path(target)
    if not p.is_absolute():
        p = root / p
    try:
        return str(p.resolve().relative_to(root.resolve()))
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
    """체크포인트 정리: git 프라이빗 ref 삭제 + cp 디렉토리 제거(best-effort)."""
    if gate.get("checkpoint_commit"):
        _git(root, "update-ref", "-d", snapshot_ref(gate["id"]))
    shutil.rmtree(cp_checkpoint_dir(root, gate["id"]), ignore_errors=True)


# ── todo.md 해시 ────────────────────────────────────────────────────────
def todo_md_path(root: Path) -> Path:
    return root / "tasks" / "todo.md"


def hash_todo_md(root: Path) -> tuple[str | None, float | None]:
    """todo.md의 sha256과 mtime. 없으면 (None, None)."""
    p = todo_md_path(root)
    if not p.exists():
        return None, None
    try:
        content = p.read_bytes()
        return hashlib.sha256(content).hexdigest(), p.stat().st_mtime
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


def make_gate(gate_id: str | None = None) -> dict[str, Any]:
    return {
        "id": gate_id or new_gate_id(),
        "created_at": now_iso(),
        "state": "created",
        "edit_count": 0,
        "edit_count_post_approval": 0,       # 승인 후 편집 수 (verifier_remind 용)
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
        "do_not_touch": [],                    # 매니페스트 do-not-touch 패턴 (deny-first)
        "manifest_sha256": None,               # 매니페스트 블록 원문 sha256 (TOCTOU 고정)
        "verifier_status": None,
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
        # 승인 시점 누적치를 initial 로 1회 고정(이미 있으면 보존 — 재승인 누적 방지)
        if gate.get("initial_edit_count") is None:
            gate["initial_edit_count"] = gate.get("edit_count", 0)
            gate["initial_unique_files"] = len(gate.get("unique_files", []))
    elif name == "retry":
        # 같은 체크포인트·계획에서 재구현 → scope/계획/initial 보존, 시도 카운터만 리셋
        gate["state"] = "approved"
        gate["verifier_status"] = None
        gate["edit_count_post_approval"] = 0
        gate["file_edit_counts"] = {}
    elif name == "replan":
        # 계획 재작성 → 체크포인트만 유지, 카운터·계획·scope 전부 리셋
        gate["state"] = "created"
        gate["approved_auto"] = False
        gate["approved_at"] = None
        gate["edit_count"] = 0
        gate["edit_count_post_approval"] = 0
        gate["file_edit_counts"] = {}
        gate["unique_files"] = []
        gate["initial_edit_count"] = None
        gate["initial_unique_files"] = None
        gate["todo_md_sha256"] = None
        gate["todo_md_mtime"] = None
        gate["scope"] = []
        gate["do_not_touch"] = []
        gate["manifest_sha256"] = None
        gate["verifier_status"] = None
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
    if tool_name not in ("Edit", "Write", "MultiEdit"):
        return None
    fp = tool_input.get("file_path")
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
                existing = ignore_file.read_text(encoding="utf-8") if ignore_file.exists() else ""
                if existing and not existing.endswith("\n"):
                    existing += "\n"
                ignore_file.write_text(existing + pattern + "\n", encoding="utf-8")
                return pattern, reason
            except OSError:
                pass
    return None


# ── 매니페스트 파싱 (step 3 — 스코프 계약 파싱·노출, 강제는 step 5) ─────────
# tasks/todo.md 안의 짝 마커 블록에서 scope / do-not-touch 파일 패턴을 읽는다.
# 짝 마커(BEGIN/END)로 단일 마커의 "어디서 끝나는가" 모호성을 제거한다.
# 이 단계는 파싱·노출만 — 어떤 편집도 차단하지 않는다(강제는 step 5,
# plan_gate_scope_enabled 플래그 뒤 기본 OFF). 미선언/파싱 실패 → fail-open:
# 스코프 없음 = thrash-only 모드. 절대 default-deny 금지(확정 결정 Q1).
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
    """매니페스트 블록 원문(마커 포함)의 sha256 — 런 중 TOCTOU 고정용.

    todo.md 전체가 아니라 매니페스트 영역만 해시해 무관한 계획 본문 편집과 분리한다.
    매니페스트 없으면 None.
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


def scope_allows(
    target: str,
    gate: dict[str, Any],
    root: Path,
    ignore_patterns: list[str] | None = None,
) -> bool:
    """target(상대/절대)이 게이트 스코프 계약상 허용되는지 (deny-first).

    - 매니페스트 미선언(has_manifest False) → True (fail-open, thrash-only).
    - .plan-gateignore 일치 → True (생성물/락파일은 스코프 검사 우회).
    - do-not-touch 일치 → False (scope 보다 우선, detour 로도 못 품).
    - scope 일치 → True, 아니면 False(루트 밖 포함).

    ※ step 3 은 이 함수를 노출만 하고 강제하지 않는다 — 차단은 step 5.
    매칭은 루트 상대경로 정규화 기준 fnmatch (절대경로 미스매치 버그 반복 금지).
    """
    if not has_manifest(gate):
        return True
    rel = _rel_to_root(root, target)
    if rel is None:
        return False  # 루트 밖 — 스코프 안에 있을 수 없다
    if ignore_patterns is None:
        ignore_patterns = load_gate_ignore(root)
    if is_gate_ignored(str(root / rel), root, ignore_patterns):
        return True
    if any(fnmatch.fnmatch(rel, pat) for pat in gate.get("do_not_touch", [])):
        return False
    return any(fnmatch.fnmatch(rel, pat) for pat in gate.get("scope", []))


def format_broad_glob_hint(manifest: dict[str, list[str]]) -> str:
    """넓은 글롭 매니페스트 자동승인 보류 안내 (additionalContext 용, D6)."""
    broad = ", ".join(p for p in manifest.get("scope", []) if is_broad_glob(p))
    return (
        f"\n{DIVIDER}\n"
        f"[plan-gate] ⚠️  넓은 글롭 매니페스트 — 자동 승인 보류\n"
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
    p.write_text(now_iso())


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
        "  차단 시점에 git tag + git stash로 자동 체크포인트를 생성하므로,\n"
        "  /rollback 으로 안전하게 되돌릴 수 있습니다.\n"
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


def format_trigger_message(
    gate: dict[str, Any],
    show_intro: bool,
    diff_summary: str,
) -> str:
    """첫 plan-gate 발동 메시지 (사용자+Claude 둘 다 봄)."""
    parts = [
        "",
        DIVIDER,
        "🛑 PLAN-GATE 차단됨 — 사용자 계획 승인 필요",
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
        f"🛑 PLAN-GATE LOCK — 이전 작업 결정 대기 중\n"
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
        f"🛑 PLAN-GATE — 같은 파일 반복 편집(수렴 안 됨)\n"
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


def do_gate_done(root: Path, state: dict[str, Any], gate: dict[str, Any]) -> None:
    """gate를 done 상태로 닫고 체크포인트를 정리한다.
    plan_gate_cli.cmd_done 과 detect_task_boundary 에서 공유.
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
        return json.loads(p.read_text())
    except Exception:
        return {"file_edits": {}}


def _save_patch_history(root: Path, history: dict[str, Any]) -> None:
    p = patch_history_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(history, ensure_ascii=False, indent=2))
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
    lines = ["", "[plan-gate] ⚠️  tasks/todo.md 자동 승인 보류 — 계획 보강 필요"]
    for issue in issues:
        lines.append(f"  - {issue}")
    lines += [
        "  tasks/todo.md를 보강 후 다시 편집하면 자동 승인됩니다.",
        "  (권장: ## 목표 섹션 + 이유 한 줄 + `- [ ]` 체크리스트 2개 이상)",
        "",
    ]
    return "\n".join(lines)
