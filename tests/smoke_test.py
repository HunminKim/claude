#!/usr/bin/env python3
"""하네스 스모크 테스트 — 훅에 가짜 stdin JSON 을 주입해 행위로 검증한다.

목적: "조용히 실패하는" 훅 시스템의 특성상 고장이 무증상이다.
정적 읽기 대신 실제 실행으로 핵심 보호 경로가 살아 있는지 확인한다.

사용법: python3 tests/smoke_test.py
종료코드: 0 = 전부 통과, 1 = 실패 있음

검증 항목:
  1. plan-gate: 같은 파일 Edit 5회 → 5회째 차단 (v1.28.0 회귀 수정 보호)
  2. plan-gate: 서로 다른 파일 7회 → 차단 없음 (오버블로킹 방지)
  3. plan-gate: 같은 파일 Write 5회 → 차단 (기존 동작 유지)
  4. plan_approval: /skip-verify 토큰 → gate done + ⏭️ 기록
  5. update_docs: stdout 은 advisory JSON 단독 (평문 오염 시 환기 무효)
  6. dangerous_bash: .env 차단 / .env.example 허용 / rm -rf 차단
  7. 채널 JSON 형태: stop_alert·session_start 가 hookSpecificOutput 래퍼 출력
  8. 스캐폴드 정합: 템플릿 settings.json 훅 ↔ SKILL.md ↔ 실물 3중 일치
  9. 버전 동기화: marketplace.json description ↔ 각 plugin.json
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / "plugins" / "project-init" / "hooks"
TEMPLATES = REPO / "plugins" / "project-init" / "skills" / "project-init" / "assets" / "templates"
SKILL_MD = REPO / "plugins" / "project-init" / "skills" / "project-init" / "SKILL.md"

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✔ {name}")
    else:
        FAIL += 1
        print(f"  ✘ {name}  {detail}")


def run_hook(hook: Path, payload: dict, project: Path) -> subprocess.CompletedProcess[str]:
    env = {"CLAUDE_PROJECT_DIR": str(project), "PATH": "/usr/bin:/bin:/usr/local/bin"}
    return subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(project),
        env=env,
    )


def make_project(base: Path, name: str) -> Path:
    p = base / name
    (p / ".claude").mkdir(parents=True)
    (p / ".claude" / "plan_gate_enabled").touch()
    subprocess.run(["git", "init", "-q", str(p)], check=True)
    subprocess.run(["git", "-C", str(p), "config", "user.email", "smoke@test"], check=True)
    subprocess.run(["git", "-C", str(p), "config", "user.name", "smoke"], check=True)
    subprocess.run(["git", "-C", str(p), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    return p


def edit_payload(tool: str, file: Path) -> dict:
    return {"tool_name": tool, "tool_input": {"file_path": str(file)}}


def set_gate(project: Path, **fields) -> None:
    sp = project / ".claude" / "state" / "plan_gate.json"
    d = json.loads(sp.read_text())
    gid = list(d["gates"])[-1]
    d["current_gate_id"] = gid
    d["gates"][gid].update(fields)
    sp.write_text(json.dumps(d))


def get_gate(project: Path) -> dict:
    sp = project / ".claude" / "state" / "plan_gate.json"
    d = json.loads(sp.read_text())
    return d["gates"][list(d["gates"])[-1]]


def t_plan_gate(base: Path) -> None:
    print("[1-3] plan-gate 트리거")
    hook = HOOKS / "plan_gate.py"

    p = make_project(base, "pg_edit")
    rcs = [run_hook(hook, edit_payload("Edit", p / "app.py"), p).returncode for _ in range(5)]
    check("같은 파일 Edit 5회째 차단", rcs[:4] == [0, 0, 0, 0] and rcs[4] == 2, f"rcs={rcs}")

    p = make_project(base, "pg_distinct")
    rcs = [run_hook(hook, edit_payload("Edit", p / f"f{i}.py"), p).returncode for i in range(7)]
    check("서로 다른 파일 7회 무차단", all(rc == 0 for rc in rcs), f"rcs={rcs}")

    p = make_project(base, "pg_write")
    rcs = [run_hook(hook, edit_payload("Write", p / "app.py"), p).returncode for _ in range(5)]
    check(
        "같은 파일 Write 5회째 차단 (1~4회는 통과)",
        rcs[:4] == [0, 0, 0, 0] and rcs[4] == 2,
        f"rcs={rcs}",
    )


def t_skip_verify(base: Path) -> None:
    print("[4] /skip-verify 토큰")
    p = make_project(base, "skipverify")
    (p / ".claude" / "agents").mkdir(parents=True)
    (p / ".claude" / "agents" / "verifier.md").write_text("placeholder")
    run_hook(HOOKS / "plan_gate.py", edit_payload("Edit", p / "a.py"), p)
    set_gate(p, state="approved")
    r = run_hook(HOOKS / "plan_approval.py", {"prompt": "/skip-verify"}, p)
    g = get_gate(p)
    check(
        "skip-verify → done + ⏭️",
        r.returncode == 0 and g["state"] == "done" and g.get("verifier_status") == "⏭️",
        f"rc={r.returncode} state={g['state']} vs={g.get('verifier_status')}",
    )


def t_update_docs(base: Path) -> None:
    print("[5] update_docs stdout 순도")
    p = make_project(base, "updatedocs")
    (p / ".claude" / "agents").mkdir(parents=True)
    (p / ".claude" / "agents" / "verifier.md").write_text("placeholder")
    run_hook(HOOKS / "plan_gate.py", edit_payload("Edit", p / "a.py"), p)
    set_gate(p, state="approved")
    docs = p / "docs"
    docs.mkdir()
    (docs / "checklist.md").write_text("### Phase 1\n| 1 | login | ⬜ | - |\n")
    (docs / "completion_report.md").write_text("# r\n")
    (docs / "technical_doc.md").write_text("# t\n")
    result = {
        "feature_name": "login",
        "timestamp": "2026-06-10",
        "verdict": "✅",
        "test_items": [{"item": "t", "result": "pass"}],
        "issues": [],
        "evidence": "ok",
        "implementation": {"files": []},
        "checklist_phase": "Phase 1",
        "checklist_row": 1,
    }
    rj = docs / ".verifier_result.json"
    rj.write_text(json.dumps(result, ensure_ascii=False))
    r = run_hook(
        HOOKS / "update_docs.py",
        {"tool_name": "Write", "tool_input": {"file_path": str(rj)}},
        p,
    )
    parsed = None
    try:
        parsed = json.loads(r.stdout.strip())
    except Exception:
        pass
    check(
        "stdout = advisory JSON 단독 파싱",
        parsed is not None and "additionalContext" in parsed.get("hookSpecificOutput", {}),
        f"stdout[:80]={r.stdout[:80]!r}",
    )
    check("진행 로그는 stderr", "업데이트 완료" in r.stderr, f"stderr[:80]={r.stderr[:80]!r}")
    check("결과 파일 자동 삭제", not rj.exists())


def t_dangerous_bash(base: Path) -> None:
    print("[6] dangerous_bash_check")
    hook = HOOKS / "dangerous_bash_check.py"
    p = make_project(base, "danger")
    cases = [
        ("cat .env", 2),
        ("cat .env.production", 2),
        ("cat .env.example", 0),
        ("cat .env.sample", 0),
        ("cat id_rsa", 2),
        ("rm -rf /", 2),
        ("ls -la", 0),
    ]
    for cmd, expect in cases:
        r = run_hook(hook, {"tool_name": "Bash", "tool_input": {"command": cmd}}, p)
        check(f"{cmd!r} → rc={expect}", r.returncode == expect, f"rc={r.returncode}")


def t_channel_shapes(base: Path) -> None:
    print("[7] 환기 채널 JSON 형태")
    p = make_project(base, "channels")
    run_hook(HOOKS / "plan_gate.py", edit_payload("Edit", p / "a.py"), p)
    set_gate(p, state="approved", edit_count=3, approved_auto=False, verifier_status=None)

    r = run_hook(HOOKS / "plan_gate_session_start.py", {}, p)
    try:
        h = json.loads(r.stdout)["hookSpecificOutput"]
        ok = h["hookEventName"] == "SessionStart" and h["additionalContext"]
    except Exception:
        ok = False
    check("session_start → hookSpecificOutput(SessionStart)", bool(ok), f"out={r.stdout[:60]!r}")

    from datetime import datetime, timezone

    set_gate(p, last_edit_ts=datetime.now(timezone.utc).isoformat())
    r = run_hook(HOOKS / "plan_gate_stop_alert.py", {}, p)
    try:
        h = json.loads(r.stdout)["hookSpecificOutput"]
        ok = h["hookEventName"] == "Stop" and h["additionalContext"]
    except Exception:
        ok = False
    check("stop_alert → hookSpecificOutput(Stop)", bool(ok), f"out={r.stdout[:60]!r}")


def t_scaffold_consistency() -> None:
    print("[8] 스캐폴드 3중 정합")
    skill = SKILL_MD.read_text()
    settings = (TEMPLATES / ".claude" / "settings.json").read_text()
    import re

    hooks_in_settings = sorted(set(re.findall(r"hooks/([\w-]+\.py)", settings)))
    for h in hooks_in_settings:
        check(f"settings.json 훅 {h}: SKILL.md 배선", h in skill)
        check(f"settings.json 훅 {h}: 템플릿 실물", (TEMPLATES / ".claude" / "hooks" / h).exists())
    for ref in sorted(set(re.findall(r"assets/templates/[\w./-]+", skill))):
        rel = ref.removeprefix("assets/templates/")
        check(f"SKILL.md 참조 실존: {ref}", (TEMPLATES / rel).exists())
    for agent in ["verifier", "infra", "backend", "frontend", "deeplearning"]:
        check(f"agents/{agent}.md: SKILL.md 생성 배선", f"{agent}.md" in skill)


def t_command_files() -> None:
    """plan_approval 토큰마다 사용자 호출용 슬래시 커맨드가 존재해야 한다.

    현행 CLI 는 미등록 슬래시 입력(/done 등)을 거부하므로, 커맨드 파일이 없으면
    문서가 안내하는 슬래시 경로가 통째로 죽는다 (v1.29.0 현장 사고 사례).
    또한 전이 커맨드는 disable-model-invocation: true 로 Claude 자율 호출을 막아야 한다.
    """
    print("[10] 전이 토큰 ↔ 슬래시 커맨드 정합")
    cmds_dir = REPO / "plugins" / "project-init" / "commands"
    # plan_approval._ACTION_TOKENS 의 토큰 → 커맨드 파일명 (keep 은 skip 의 별칭)
    sys.path.insert(0, str(HOOKS))
    import plan_approval

    for token in plan_approval._ACTION_TOKENS:
        fname = "skip.md" if token == "keep" else ("approve-plan.md" if token == "approve" else f"{token}.md")
        f = cmds_dir / fname
        check(f"토큰 '{token}' → commands/{fname} 존재", f.exists())
        if f.exists():
            text = f.read_text()
            check(
                f"commands/{fname}: disable-model-invocation",
                "disable-model-invocation: true" in text,
            )
            action = plan_approval._ACTION_TOKENS[token]
            check(f"commands/{fname}: CLI 액션 '{action}' 호출", f"plan_gate_cli.py\" {action}" in text)


def t_secret_commit_guard(base: Path) -> None:
    """운영 정보 git 추적 차단 — .gitignore 템플릿 + pre-commit 2차 방어."""
    print("[12] 비밀 파일 git 추적 차단")
    gi = TEMPLATES / "gitignore"
    check(".gitignore 템플릿 존재", gi.exists())
    if gi.exists():
        text = gi.read_text()
        for pat in [".env", "!.env.example", "*.pem", "credentials.json", ".claude/state/"]:
            check(f".gitignore 템플릿에 {pat!r}", pat in text)

    p = base / "secguard"
    p.mkdir()
    subprocess.run(["git", "init", "-q", str(p)], check=True)
    subprocess.run(["git", "-C", str(p), "config", "user.email", "s@t"], check=True)
    subprocess.run(["git", "-C", str(p), "config", "user.name", "s"], check=True)
    hooks = p / ".githooks"
    hooks.mkdir()
    import shutil

    hook = hooks / "pre-commit"
    shutil.copy(TEMPLATES / ".githooks" / "pre-commit", hook)
    hook.chmod(0o755)
    subprocess.run(["git", "-C", str(p), "config", "core.hooksPath", ".githooks"], check=True)
    (p / "CLAUDE.md").write_text("# r")
    subprocess.run(["git", "-C", str(p), "add", "CLAUDE.md"], check=True)
    subprocess.run(["git", "-C", str(p), "commit", "-q", "-m", "init"], capture_output=True)

    def try_commit(fname: str) -> int:
        (p / fname).write_text("x")
        subprocess.run(["git", "-C", str(p), "add", "-f", fname], check=True)
        r = subprocess.run(["git", "-C", str(p), "commit", "-q", "-m", "t"], capture_output=True)
        subprocess.run(["git", "-C", str(p), "reset", "-q"], capture_output=True)
        return r.returncode

    check(".env 커밋 차단", try_commit(".env") != 0)
    check("id_rsa 커밋 차단", try_commit("id_rsa") != 0)
    check("server.pem 커밋 차단", try_commit("server.pem") != 0)
    check(".env.example 커밋 허용", try_commit(".env.example") == 0)
    check("일반 파일 커밋 허용", try_commit("app.py") == 0)


def t_platform_compat() -> None:
    """현행 Claude Code 호환성 — 플랫폼 드리프트 회귀 방지.

    v2.1.63에서 Task → Agent 개명으로 위임 가드가 조용히 죽었던 사고(260611 감사),
    MultiEdit 툴 소멸로 죽은 권고가 배포되던 사고의 재발을 막는다.
    """
    print("[11] 플랫폼 호환성")
    hooks_json = (REPO / "plugins" / "project-init" / "hooks" / "hooks.json").read_text()
    check("위임 가드 matcher에 Agent 포함", '"Agent|Task"' in hooks_json)
    pl_hooks = (REPO / "plugins" / "prompt-log" / "hooks" / "hooks.json").read_text()
    check("prompt-log matcher에 Agent 포함", "Agent" in pl_hooks)

    # delegation_prompt_check 행위: Agent 이름으로 차단/허용 (프로젝트 불필요 — 순수 입력 판정)
    hook = HOOKS / "delegation_prompt_check.py"
    r = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps({"tool_name": "Agent", "tool_input": {"subagent_type": "backend", "prompt": "x"}}),
        capture_output=True, text=True,
    )
    check("Agent 위임 + 4블록 누락 → 차단", r.returncode == 2, f"rc={r.returncode}")
    full = "TASK: x\nUSER_DECISIONS: 없음\nCONSTRAINTS: y\nGATE: approved"
    r = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps({"tool_name": "Agent", "tool_input": {"subagent_type": "backend", "prompt": full}}),
        capture_output=True, text=True,
    )
    ok = r.returncode == 0 and "hookSpecificOutput" in r.stdout
    check("Agent 위임 + 4블록 완비 → allow JSON", ok, f"rc={r.returncode}")

    # prompt-log 토큰 정규화 ↔ plan_approval._ACTION_TOKENS 동기
    sys.path.insert(0, str(HOOKS))
    sys.path.insert(0, str(REPO / "plugins" / "prompt-log" / "hooks"))
    import plan_approval

    import prompt_log_lib as pl

    check(
        "PL_TOKEN_VALUES == plan_approval 토큰 집합",
        set(plan_approval._ACTION_TOKENS) == pl.PL_TOKEN_VALUES,
        f"차이: {set(plan_approval._ACTION_TOKENS) ^ pl.PL_TOKEN_VALUES}",
    )
    for text, want in [("done", "done"), ("/done", "done"), ("/project-init:done", "done"), ("오늘 뭐했지", None)]:
        check(f"토큰 정규화 {text!r} → {want!r}", pl.pl_normalize_token(text) == want)
    check("Agent → agent 버킷", pl.pl_tool_bucket("Agent") == "agent")
    check("TaskCreate 는 other (오탐 방지)", pl.pl_tool_bucket("TaskCreate") == "other")

    # 죽은 툴 prose 잔존 금지 (matcher 의 하위호환 토큰은 허용)
    bad = subprocess.run(
        ["grep", "-rln", "MultiEdit 한 번\\|Edit/MultiEdit\\|Task(subagent_type",
         str(REPO / "plugins" / "project-init" / "skills")],
        capture_output=True, text=True,
    ).stdout.strip()
    check("템플릿 prose에 죽은 툴 권고 없음", not bad, f"잔존: {bad}")
    fm = subprocess.run(
        ["grep", "-rln", "tools:.*MultiEdit", str(REPO / "plugins" / "project-init" / "skills")],
        capture_output=True, text=True,
    ).stdout.strip()
    check("에이전트 frontmatter에 MultiEdit 없음", not fm, f"잔존: {fm}")


def t_version_sync() -> None:
    print("[9] 버전 동기화")
    mp = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
    by_name = {pl["name"]: pl for pl in mp.get("plugins", [])}
    for name in ["project-init", "harness-check", "prompt-log"]:
        pj = json.loads(
            (REPO / "plugins" / name / ".claude-plugin" / "plugin.json").read_text()
        )
        desc = by_name.get(name, {}).get("description", "")
        check(
            f"{name} v{pj['version']} ↔ marketplace description",
            f"v{pj['version']}" in desc,
            f"desc={desc[:60]!r}",
        )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="harness_smoke_") as td:
        base = Path(td)
        t_plan_gate(base)
        t_skip_verify(base)
        t_update_docs(base)
        t_dangerous_bash(base)
        t_channel_shapes(base)
        t_secret_commit_guard(base)
    t_scaffold_consistency()
    t_command_files()
    t_platform_compat()
    t_version_sync()
    print(f"\n결과: {PASS} 통과, {FAIL} 실패")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
