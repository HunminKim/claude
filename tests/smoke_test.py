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

import ast
import json
import os
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
    # 실제 환경 PATH 를 상속해 git 등이 비표준 경로(Homebrew/nix)에 있어도 찾는다
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project)}
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
    # 작업 디렉토리 루트 전체 삭제 — 특정 경로(/workspace) 하드코딩 대신 CLAUDE_PROJECT_DIR 동적 비교
    r = run_hook(hook, {"tool_name": "Bash", "tool_input": {"command": f"rm -rf {p}"}}, p)
    check("CLAUDE_PROJECT_DIR 전체 삭제 → 차단", r.returncode == 2, f"rc={r.returncode}")


def t_secret_read_guard() -> None:
    """비밀 파일 내용 노출 — 다중 우회 경로 fail-closed 차단 (순수 입력 판정)."""
    print("[7b] 비밀 파일 노출 우회 차단")
    bash = HOOKS / "dangerous_bash_check.py"
    guard = HOOKS / "secret_read_guard.py"

    def bash_rc(cmd: str) -> int:
        return subprocess.run(
            [sys.executable, str(bash)],
            input=json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}),
            capture_output=True, text=True,
        ).returncode

    # 차단돼야 하는 우회 경로
    for cmd in [
        "cat .env", "grep API .env", "awk '{print}' .env", "sed -n p .env",
        "xxd .env", "strings .env", "base64 .env", "cut -d= -f2 .env", "sort .env",
        "tac .env", "rg x .env", "source .env && echo $K", ". .env",
        "python3 -c \"open('.env')\"", "cat < .env", "cp .env /tmp/x", "mv .env /tmp/y",
        "scp .env host:/", "cat .env.production", "cat id_rsa", "cat server.pem",
    ]:
        check(f"차단: {cmd[:34]}", bash_rc(cmd) == 2, "노출됨")

    # 통과해야 하는 정상 명령 (오탐 방지)
    for cmd in [
        "cp .env.example .env", "cat .env.example", "echo 'K=V' >> .env",
        "chmod 600 .env", "ls -la .env", "grep TODO src/app.py", "python3 app.py",
        "cat README.md", "sed -i s/a/b/ src/x.py",
    ]:
        check(f"통과: {cmd[:34]}", bash_rc(cmd) == 0, "오탐 차단")

    # Grep 툴로 비밀 파일 content 읽기 차단
    for tool, inp, want in [
        ("Grep", {"path": ".env", "pattern": ".", "output_mode": "content"}, 2),
        ("Read", {"file_path": "/p/.env"}, 2),
        ("Grep", {"path": "src/", "pattern": "TODO"}, 0),
        ("Read", {"file_path": "/p/README.md"}, 0),
    ]:
        rc = subprocess.run(
            [sys.executable, str(guard)],
            input=json.dumps({"tool_name": tool, "tool_input": inp}),
            capture_output=True, text=True,
        ).returncode
        check(f"{tool} {str(inp)[:30]} → rc={want}", rc == want, f"rc={rc}")


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
        # 평문 토큰마다 동명 슬래시 커맨드가 1:1 존재해야 한다 (별칭 비대칭 방지)
        fname = f"{token}.md"
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


def t_delegation_guard(base: Path) -> None:
    """위임 가드 일반화(M1) — .claude/agents/ 의 커스텀 에이전트면 발화, 유틸/미정의는 통과.

    특정 에이전트 이름(backend 등) 하드코딩을 제거하고 프로젝트 정의 에이전트에
    일반화한 변경의 회귀 방지. 어떤 이름(@data 등)이든 .claude/agents 에 있으면 검사한다.
    """
    print("[12] 위임 가드 일반화")
    hook = HOOKS / "delegation_prompt_check.py"
    proj = make_project(base, "deleg")
    (proj / ".claude" / "agents").mkdir(parents=True)
    (proj / ".claude" / "agents" / "backend.md").write_text("# backend")
    (proj / ".claude" / "agents" / "data.md").write_text("# data")

    def run(subagent: str, prompt: str) -> subprocess.CompletedProcess[str]:
        payload = {"tool_name": "Agent", "tool_input": {"subagent_type": subagent, "prompt": prompt}}
        return run_hook(hook, payload, proj)

    full = "TASK: x\nUSER_DECISIONS: 없음\nCONSTRAINTS: y\nGATE: approved"
    check("커스텀 backend + 4블록 누락 → 차단", run("backend", "x").returncode == 2)
    r = run("backend", full)
    check(
        "커스텀 backend + 4블록 완비 → allow JSON",
        r.returncode == 0 and "hookSpecificOutput" in r.stdout,
        f"rc={r.returncode}",
    )
    check("커스텀 @data 에이전트도 가드 발화 (일반화)", run("data", "x").returncode == 2)
    check("유틸 Plan 에이전트는 통과", run("Plan", "x").returncode == 0)
    check("미정의 에이전트는 통과", run("nonexistent", "x").returncode == 0)


def t_install_python_gate(base: Path) -> None:
    """install.sh 0단계 — 구버전 python3 에서 설치 차단 (PR #4 버전 게이트 회귀 방지).

    가짜 python3(3.6 흉내) stub 을 PATH 앞에 두고 install.sh 를 실행한다.
    0단계에서 exit 1 로 끝나므로 claude(마켓플레이스 등록)는 호출되지 않아 부작용이 없다.
    """
    print("[13] install.sh Python 버전 게이트")
    bindir = base / "fakebin"
    bindir.mkdir()
    stub = bindir / "python3"
    # install.sh 의 `python3 -c '...'` 호출에서 print 는 "3.6" 출력, sys.exit 비교는 전부 미달(1)
    stub.write_text(
        "#!/bin/sh\n"
        'case "$2" in\n'
        "  *print*) echo '3.6' ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n"
    )
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ.get('PATH', '')}"}
    r = subprocess.run(
        ["bash", str(REPO / "install.sh")],
        capture_output=True, text=True, env=env, cwd=str(REPO),
    )
    out = r.stdout + r.stderr
    check("3.6 환경에서 설치 차단 (exit 1)", r.returncode == 1, f"rc={r.returncode}")
    check("차단 메시지에 버전 안내 포함", "3.6" in out, f"out={out[-120:]!r}")


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
    for text, want in [
        ("done", "done"),
        ("/done", "done"),
        ("/project-init:done", "done"),
        ("/any-plugin:skip", "skip"),  # 임의 네임스페이스 일반화 (M2)
        ("오늘 뭐했지", None),
    ]:
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


_PEP604_GENERICS = {"list", "dict", "tuple", "set", "frozenset", "type"}


def _anno_needs_future(anno: ast.expr) -> bool:
    """어노테이션 노드가 PEP604(BitOr) 또는 제네릭 서브스크립트를 쓰면 True."""
    for sub in ast.walk(anno):
        if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr):
            return True
        if (
            isinstance(sub, ast.Subscript)
            and isinstance(sub.value, ast.Name)
            and sub.value.id in _PEP604_GENERICS
        ):
            return True
    return False


def _collect_annotations(tree: ast.Module) -> list:
    """함수 시그니처·변수 어노테이션 노드만 모은다 (본문 비트연산 오탐 방지)."""
    annos = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if node.returns:
                annos.append(node.returns)
            a = node.args
            annos += [x.annotation for x in a.posonlyargs + a.args + a.kwonlyargs if x.annotation]
        elif isinstance(node, ast.AnnAssign) and node.annotation:
            annos.append(node.annotation)
    return annos


def _hook_needs_future(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(_anno_needs_future(a) for a in _collect_annotations(tree))


def t_hook_future_imports() -> None:
    """훅이 PEP604/제네릭 어노테이션을 쓰면 from __future__ import annotations 필수 (3.8 호환).

    리포트 260612: 템플릿 훅 3종이 future import 누락 + `Path | None` 사용으로 3.8 에서 TypeError.
    future import 가 PEP563 으로 어노테이션을 문자열화해야 3.7~3.9 에서 안전하다.
    """
    print("[14] 훅 future-import 일관성 (3.8 호환)")
    hook_dirs = [HOOKS, REPO / "plugins" / "prompt-log" / "hooks", TEMPLATES / ".claude" / "hooks"]
    offenders = []
    for d in hook_dirs:
        for f in sorted(d.glob("*.py")):
            if "from __future__ import annotations" in f.read_text(encoding="utf-8"):
                continue
            if _hook_needs_future(f):
                offenders.append(f.name)
    check("PEP604/제네릭 쓰는 훅은 future import 보유", not offenders, f"위반: {offenders}")


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
        t_secret_read_guard()
        t_channel_shapes(base)
        t_secret_commit_guard(base)
        t_delegation_guard(base)
        t_install_python_gate(base)
    t_scaffold_consistency()
    t_command_files()
    t_platform_compat()
    t_hook_future_imports()
    t_version_sync()
    print(f"\n결과: {PASS} 통과, {FAIL} 실패")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
