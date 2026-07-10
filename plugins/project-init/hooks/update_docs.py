#!/usr/bin/env python3
"""PostToolUse hook (matcher: Write) — verifier 결과 자동 문서화.

출력 채널: 환기 (exit 0 + stdout hookSpecificOutput.additionalContext JSON)
- verifier ✅/❌ 결과 advisory 만 stdout 에 단독 JSON 으로 출력한다
  → Claude 가 행동 지시("한국어 한 단락 요약" 등)를 받아 자기 응답에 반영
- 진행 로그(업데이트 완료/삭제/경고)는 전부 stderr — 사용자 터미널 전용
- 주의: stdout 에 평문과 JSON 을 섞으면 하네스가 advisory 를 파싱하지
  못해 환기가 무효가 된다 (v1.28.0 채널 분리 수정)

동작 단계:
verifier가 `*.verifier_result.json`(파일명 suffix 매칭 — docs/ 위치 무관)을 생성하면:
1. 실행 근거(evidence)가 없으면 verdict ✅→❌ 격하
   (docs/config 프로파일은 diff 교차 검증 통과 시 전-static ✅ 인정 — 과잉검증 완화)
2. checklist.md, completion_report.md, technical_doc.md 자동 갱신
3. ❌ 이슈가 있으면 CLAUDE.md "알려진 버그 / 제약" 섹션에 반영
4. plan-gate gate 상태 갱신 + 사용자 결정 유도 advisory(환기) 출력
   (❌ 는 failure_category 별로 권장 액션이 갈린다 — 구현 결함 vs 환경 제약 구분)
5. 결과 파일 폐기 — **gate 가 판정을 소비했을 때만**. 소비하지 못한 파일을 지우면
   plan_gate_cli 의 복구 경로가 재료를 잃어 /done 이 영구 거부된다(2026-07-10 데드락).
   보존된 파일은 /done 복구가 소비하거나 gate 닫힘(cleanup_checkpoint)이 폐기한다.
이 결과 파일은 verifier 외에 아무도 건드려서는 안 된다.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# Windows cp949 등 비UTF-8 콘솔에서 이모지·em-dash 입출력 시 UnicodeError 방지 (stdio UTF-8 고정)
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_gate_lib as lib  # noqa: E402


def _log(msg: str) -> None:
    """진행 로그 — 사용자 터미널 전용 (stderr). stdout 오염 금지."""
    print(msg, file=sys.stderr)


def _emit_advisory(msg: str) -> None:
    """verifier 결과 advisory — stdout 단독 JSON (Claude context 주입)."""
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": msg,
                }
            },
            ensure_ascii=False,
        )
    )


def main() -> int:
    # 1. 훅 입력 읽기
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path", "")

    # 2. Write 툴로 .verifier_result.json 을 쓴 경우만 처리
    if tool_name != "Write" or not file_path.endswith(".verifier_result.json"):
        return 0

    # 훅 입력의 계약 필드 `cwd` 로 상대 경로를 절대화한다. verifier 는
    # `docs/.verifier_result.json` 을 상대 경로로 쓰도록 지시받으므로, 프로세스 cwd 에
    # 의존하면 루트 유도가 흔들린다(gate 만 조용히 누락되는 사고의 원인).
    result_path = Path(file_path)
    hook_cwd = data.get("cwd") or ""
    if not result_path.is_absolute() and hook_cwd:
        result_path = Path(hook_cwd) / result_path
    if not result_path.exists():
        return 0

    # 3. 결과 파일 읽기
    try:
        with open(result_path, encoding="utf-8", errors="ignore") as f:
            result: dict[str, Any] = json.load(f)
    except Exception as e:
        _log(f"[update_docs] 결과 파일 읽기 실패: {e}")
        return 0

    # 4. 문서/게이트 갱신.
    #    verifier 는 LLM 이라 스키마 이탈이 정상 시나리오다. 과거: 비정형 test_items
    #    (dict 아닌 문자열)에 AttributeError → rc=1 + 임시파일 잔존 + 게이트 미갱신.
    #    삭제는 **소비자 소유**다 — gate 가 판정을 소비했을 때만 지운다. 소비하지 못한
    #    파일을 지우면 plan_gate_cli 의 복구 경로가 재료를 잃어 /done 이 영구 거부된다
    #    (2026-07-10 데드락). 남은 파일은 복구가 소비하거나 gate 닫힘이 폐기한다.
    try:
        rc, delete_ok = _process(result_path.parent, result)
    except Exception as e:
        _log(
            f"[update_docs] ⚠️ 결과 처리 실패(스키마 이탈?): {e} — 문서 자동화 건너뜀\n"
            "  결과 파일은 보존합니다 (/done 이 복구를 시도합니다)."
        )
        return 0

    if delete_ok:
        try:
            result_path.unlink()
            _log("[update_docs] .verifier_result.json 삭제 완료")
        except OSError:
            pass
    return rc


def _coerce_items(raw: Any) -> list[dict[str, Any]]:
    """test_items 를 dict 리스트로 정규화 — 문자열 항목은 {'item': ...} 로 감싼다."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for t in raw:
        if isinstance(t, dict):
            out.append(t)
        elif isinstance(t, str):
            out.append({"item": t})
    return out


def _str_list(raw: Any) -> list[str]:
    """리스트가 아니거나 비문자 항목이 섞여도 문자열 리스트로 정규화."""
    if not isinstance(raw, list):
        return [str(raw)] if raw else []
    return [str(x) for x in raw]


# ── 작업 유형별 검증 프로파일 / 실패 사유 분류 (verifier.md 스키마 확장) ──
# docs/config 프로파일은 실행 grounding 을 완화(전-static ✅ 허용)하는 유일한
# 유형이라, verifier 의 task_type 자가선언을 그대로 믿으면 코드 변경이 경량
# 프로파일로 빠져나가는 구멍이 된다 — 아래 diff 교차 검증(기계 강제)이 막는다.
_DOCS_PROFILE_TYPES = {"docs", "config"}
_DOCS_OK_SUFFIXES = (".md", ".rst", ".txt", ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg")
# 행동 지시 문서 — Claude·서브에이전트 동작을 바꾸므로 행위 검증 대상 (경량 프로파일 제외)
_INSTRUCTION_COMPONENTS = {".claude", ".github", ".githooks"}

# ❌ 실패 사유 분류 → 사용자 안내 분기. 구현 결함과 검증 한계/환경 제약이 같은
# 4택으로 뭉개지던 문제를 해소한다 — verdict 소비자(D1 lock 등)는 이진 유지라 무영향.
_FAILURE_CATEGORY_INFO = {
    "implementation_defect": (
        "구현 자체 결함",
        "/retry 로 같은 체크포인트에서 수정 후 재검증 권장",
    ),
    "test_gap": (
        "테스트 부재·부족 — 판정 근거 미확보",
        "테스트 보강 후 @verifier 재검증 권장 (구현 결함으로 단정하지 말 것)",
    ),
    "verification_limit": (
        "검증 정책·자산 한계 (예: eval 골든셋 부재)",
        "구현 문제가 아닐 수 있음 — 검증 자산 보강 후 재검증 또는 /skip(보존 마감) 권장",
    ),
    "environment_constraint": (
        "실행 환경 제약으로 검증 불가",
        "구현 문제가 아닐 수 있음 — 환경 확보 후 재검증 또는 /skip(보존 마감) 권장",
    ),
}


def _control_plane_rel(rel: str) -> bool:
    """plan-gate 운영 파일 — 어떤 게이트에서든 변하는 파일이라 diff 판정에서 제외."""
    return (
        rel in ("tasks/todo.md", ".plan-gateignore")
        or rel.endswith(".verifier_result.json")
        or rel.startswith(".claude/state/")
        or rel.startswith(".claude/plan_gate")
    )


def _diff_is_docs_only(docs_dir: Path) -> bool:
    """working tree 변경이 문서/설정 파일로만 구성됐는지 기계 교차 검증 (fail-closed).

    git status --porcelain 기준(untracked 신규 파일 포함 — diff HEAD 는 못 본다).
    판단 불가(비-git·명령 실패·변경 없음)면 False, 행동 지시 문서(.claude/**,
    CLAUDE.md, .githooks/**)나 비문서 확장자가 하나라도 섞이면 False.
    """
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(docs_dir), capture_output=True, text=True, timeout=10,
        )
        if top.returncode != 0 or not top.stdout.strip():
            return False
        root = top.stdout.strip()
        # -uall: untracked 디렉토리를 "?? dir/" 로 축약하지 않고 파일 단위로 나열
        # (축약되면 확장자 판정이 디렉토리명에 걸려 문서-only 를 오판한다)
        st = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            cwd=root, capture_output=True, text=True, timeout=10,
        )
        if st.returncode != 0:
            return False
    except Exception:
        return False

    rels: list[str] = []
    for line in st.stdout.splitlines():
        if len(line) < 4:
            continue
        rel = line[3:].strip().strip('"')
        if " -> " in rel:  # rename: 새 경로 기준으로 판정
            rel = rel.split(" -> ")[-1].strip().strip('"')
        rel = rel.replace("\\", "/").rstrip("/")
        if rel and not _control_plane_rel(rel):
            rels.append(rel)
    if not rels:
        return False  # 판정할 변경이 없음 — 근거 부재는 인정 아님 (fail-closed)

    for rel in rels:
        parts = rel.split("/")
        if parts[-1] == "CLAUDE.md" or any(c in _INSTRUCTION_COMPONENTS for c in parts):
            return False
        if not rel.lower().endswith(_DOCS_OK_SUFFIXES):
            return False
    return True


def _resolve_root(docs_dir: Path) -> Path | None:
    """gate 루트 유도 — 결과 파일 절대경로 기준(결정론) 우선, 실패 시 env/cwd 탐색.

    문서 갱신은 docs_dir(절대경로)를 쓰는데 gate 갱신만 find_project_root()(env/cwd)를
    써서, 훅 프로세스의 cwd 가 프로젝트 밖이면 문서만 갱신되고 gate 는 조용히 누락됐다.
    두 부작용이 같은 루트를 공유하면 함께 성공하거나 함께 실패한다.
    """
    candidate = docs_dir.parent
    if lib.is_project_init_managed(candidate):
        return candidate
    # 결과 파일이 놓인 트리가 곧 올바른 루트 — find_project_root 의 워크트리 감지가
    # docs 부모 기준으로 동작하도록 start 를 넘긴다(훅·CLI 와 같은 규칙).
    return lib.find_project_root(docs_dir.parent)


def _gate_touches_tree(gate: dict[str, Any], tree_root: Path) -> bool:
    """gate 의 편집 카운터에 tree_root 하위 경로가 섞여 있는가 — 교차 집계의 직접 증거."""
    try:
        resolved = tree_root.resolve()
    except OSError:
        return False
    for fp in gate.get("file_edit_counts", {}):
        try:
            Path(fp).resolve().relative_to(resolved)
            return True
        except (ValueError, OSError):
            continue
    return False


def _process(docs_dir: Path, result: dict[str, Any]) -> tuple[int, bool]:
    """결과 dict 를 문서·게이트에 반영. 필드는 전부 방어적으로 정규화해서 쓴다.

    반환: (rc, delete_ok). delete_ok 는 "결과 파일을 지워도 되는가" — gate 가 판정을
    소비했거나, 소비할 gate 자체가 없을 때만 True. 소비 가능한 gate 가 있는데 전이하지
    못했으면 False 로 보존해 /done 의 복구 경로에 재료를 남긴다.
    """
    feature_name = str(result.get("feature_name") or "알 수 없음")
    timestamp = str(result.get("timestamp") or "")
    # verdict 는 반드시 정규화해서 쓴다 — "✅ 통과" 같은 수식어가 붙으면 아래 grounding
    # 강등(if verdict == "✅")과 gate 전이가 통째로 건너뛰어진다. ❓ 는 전이하지 않는다.
    raw_verdict = result.get("verdict", "")
    verdict = lib.normalize_verdict(raw_verdict)
    test_items = _coerce_items(result.get("test_items"))
    issues = _str_list(result.get("issues"))
    code_smells = _str_list(result.get("code_smells"))
    critical_constraints = _str_list(result.get("critical_constraints"))
    evidence = str(result.get("evidence") or "")
    task_type = str(result.get("task_type") or "").strip().lower()
    failure_category = str(result.get("failure_category") or "").strip().lower()
    _raw_impl = result.get("implementation")
    impl = _raw_impl if isinstance(_raw_impl, dict) else {}

    # ── 실행 grounding 강제 (✅ 의 최소 조건 — verifier.md 규칙의 기계 강제) ──
    # verifier.md 는 "✅ 는 최소 1개 항목이 실제 실행으로 입증" 을 프로즈로만 요구했다.
    # 강제가 없으면 전 항목 static(코드만 읽음)인데 ✅ 를 줘도 그대로 통과해 '읽고
    # 통과시키기'가 새어든다. 여기서 기계 강제: 실행 입증이 없고 면제 사유도 없으면
    # ✅ 를 신뢰 불가로 보고 ❌ 로 강등한다 — 이후 checklist/보고서/advisory 가 전부
    # ❌ 단일 경로로 흐른다. 면제 2종:
    #   ① evidence 의 '전 항목 실행 불가' 마커 (verifier.md 명시, 전 유형 공통)
    #   ② docs/config 프로파일 — 변경이 문서/설정뿐임을 diff 로 교차 검증했을 때만
    #     (verifier 자가선언만으로는 인정 안 함 — 코드 변경의 경량 프로파일 위장 차단)
    _EXEC_METHODS = {"mocked", "isolated_exec", "production_exec"}
    if verdict == "✅":
        grounded = any((t.get("method") or "").strip() in _EXEC_METHODS for t in test_items)
        # verifier.md 가 명시한 면제 마커 전체("전 항목 실행 불가")를 요구한다 —
        # "실행 불가" substring 만 보면 다른 맥락의 문장으로도 면제가 성립하는 구멍.
        exempt = "전 항목 실행 불가" in (evidence or "")
        docs_profile = False
        if not grounded and not exempt and task_type in _DOCS_PROFILE_TYPES:
            docs_profile = _diff_is_docs_only(docs_dir)
            if docs_profile:
                _log(f"[update_docs] {task_type} 프로파일 — diff 교차 검증 통과, 전-static ✅ 인정")
        if not grounded and not exempt and not docs_profile:
            _cross = (
                f" (task_type={task_type} 선언됐으나 diff 교차 검증 실패 — 변경에 "
                f"문서/설정 외 파일이 포함됐거나 판단 불가)"
                if task_type in _DOCS_PROFILE_TYPES
                else ""
            )
            verdict = "❌"
            failure_category = failure_category or "test_gap"  # 강등 = 검증 미수행
            issues = list(issues) + [
                "실행 grounding 위반: 전 검증 항목이 static 이고 실행 입증도 면제 사유도 "
                "없어 ✅ 를 신뢰할 수 없습니다 — ❌ 로 강등. 최소 1개 항목을 실제 실행"
                "(mocked/isolated_exec/production_exec)으로 재검증하거나, 실행이 정말 "
                "불가능하면 evidence 에 '전 항목 실행 불가 — 사유' 를 명시하세요." + _cross
            ]

    # ── checklist.md 업데이트 ──────────────────────────────────────────────
    checklist_path = docs_dir / "checklist.md"
    checklist_phase = result.get("checklist_phase", "")
    checklist_row = result.get("checklist_row", None)

    if checklist_path.exists():
        content = checklist_path.read_text(encoding="utf-8", errors="ignore")
        lines = content.splitlines()
        updated = False

        if checklist_phase and checklist_row is not None:
            # Phase명 + 행 번호로 직접 접근 (텍스트 매칭 불필요)
            in_phase = False
            for i, line in enumerate(lines):
                if line.startswith("###") and checklist_phase in line:
                    in_phase = True
                    continue
                if in_phase and line.startswith("###"):
                    break
                if in_phase and f"| {checklist_row} |" in line:
                    parts = line.split("|")
                    if len(parts) >= 5:
                        parts[3] = f" {verdict} "
                        parts[4] = f" {timestamp} "
                        lines[i] = "|".join(parts)
                        updated = True
                        break
        else:
            # fallback: feature_name을 독립 셀로 정확히 매칭 (부분 일치 오탐 방지)
            # "login" 검색이 "admin login" 행에 잘못 걸리지 않도록 셀 경계를 확인한다.
            _cell_pat = re.compile(r"\|\s*" + re.escape(feature_name) + r"\s*\|")
            for i, line in enumerate(lines):
                if _cell_pat.search(line):
                    parts = line.split("|")
                    if len(parts) >= 5:
                        parts[3] = f" {verdict} "
                        parts[4] = f" {timestamp} "
                        lines[i] = "|".join(parts)
                        updated = True
                        break

        if updated:
            checklist_path.write_text("\n".join(lines), encoding="utf-8")
            _log(f"[update_docs] checklist.md 업데이트 완료: {feature_name}")
        else:
            hint = f"{checklist_phase} #{checklist_row}" if checklist_phase else feature_name
            _log(f"[update_docs] ⚠️ checklist.md 업데이트 실패: '{hint}' 항목 없음 — 수동 업데이트 필요")

    # ── completion_report.md 업데이트 ─────────────────────────────────────
    report_path = docs_dir / "completion_report.md"
    if report_path.exists():
        content = report_path.read_text(encoding="utf-8", errors="ignore")

        # 테이블 마지막 행 다음에 항목 추가
        issues_text = "\n".join(f"- {i}" for i in issues) if issues else "없음"
        test_rows = "\n".join(
            f"| {t.get('item', '?')} | {t.get('result', '')} | {t.get('note', '')} |"
            for t in test_items
        )
        smells_section = ""
        if code_smells:
            smells_lines = "\n".join(f"- {s}" for s in code_smells)
            smells_section = f"\n**설계 냄새 (CODE_SMELL)**\n{smells_lines}\n"
        section = f"""
### {feature_name} — {timestamp}
**판정**: {verdict}

**검증 항목**
| 항목 | 결과 | 비고 |
|------|------|------|
{test_rows}

**발견된 문제**: {issues_text}
{smells_section}
**검증 근거**: {evidence}

---"""

        # 동일 feature+timestamp 섹션이 이미 있으면 (재검증 반복) 건너뜀
        _section_key = f"### {feature_name} — {timestamp}"
        if _section_key in content:
            _log(f"[update_docs] completion_report.md 이미 등록됨: '{feature_name}', 건너뜀")
        else:
            content += section
            report_path.write_text(content, encoding="utf-8")
            _log(f"[update_docs] completion_report.md 업데이트 완료: {feature_name}")

    # ── technical_doc.md 업데이트 ─────────────────────────────────────────
    tech_path = docs_dir / "technical_doc.md"
    if tech_path.exists():
        content = tech_path.read_text(encoding="utf-8", errors="ignore")

        files_text = (
            "\n".join(f"- `{f.get('path', '?')}` — {f.get('role', '')}" for f in impl.get("files", [])) or "- 없음"
        )
        interface = impl.get("interface", {})

        section = f"""
### {feature_name} — {timestamp}

**구현 내용**
{impl.get("description", "-")}

**주요 로직**
{impl.get("logic", "-")}

**관련 파일**
{files_text}

**인터페이스**
- 입력: {interface.get("input", "-")}
- 출력: {interface.get("output", "-")}

---"""

        content += section
        tech_path.write_text(content, encoding="utf-8")
        _log(f"[update_docs] technical_doc.md 업데이트 완료: {feature_name}")

    # ── CLAUDE.md 알려진 버그/제약 업데이트 ──────────────────────────────
    claude_path = docs_dir.parent / "CLAUDE.md"
    if critical_constraints and claude_path.exists():
        content = claude_path.read_text(encoding="utf-8", errors="ignore")
        section_header = "## 알려진 버그 / 제약"
        # 이미 기재된 항목은 건너뛴다 — /retry 재검증 사이클마다 같은 제약이
        # 무한 누적되는 것을 방지 (dedup 없던 회귀). ⚠️ 라인 단위 정확 비교 —
        # substring(`f"- {c}" in content`)은 새 제약이 기존 줄의 접두면 신규인데도
        # "기존재"로 오판해 유실됐다(예: "캐시 무효화" ⊂ "- 캐시 무효화가 안 됨").
        existing_lines = {ln.strip() for ln in content.splitlines()}
        fresh = [c for c in critical_constraints if f"- {c}" not in existing_lines]
        if not fresh:
            _log("[update_docs] CLAUDE.md 제약 전부 기존재 — 건너뜀")
        else:
            new_items = "\n".join(f"- {c}" for c in fresh)

            if section_header in content:
                # 섹션 끝(다음 ## 또는 파일 끝) 바로 앞에 삽입
                lines = content.splitlines()
                insert_at = None
                in_section = False
                for i, line in enumerate(lines):
                    if line.strip() == section_header:
                        in_section = True
                        continue
                    if in_section and line.startswith("## "):
                        insert_at = i
                        break
                if insert_at is not None:
                    lines.insert(insert_at, new_items)
                else:
                    lines.append(new_items)
                claude_path.write_text("\n".join(lines), encoding="utf-8")
            else:
                # 섹션 자체가 없으면 파일 끝에 추가
                content += f"\n\n{section_header}\n\n{new_items}\n"
                claude_path.write_text(content, encoding="utf-8")

            _log(f"[update_docs] CLAUDE.md 알려진 버그/제약 업데이트: {len(fresh)}건")

    # ── plan-gate state 갱신 (D3) ─────────────────────────────────────────
    # verifier 결과를 plan-gate 상태에 반영. 자동 /done이나 /rollback은 하지 않고,
    # 사용자에게 결정 토큰(/retry, /done, /rollback)을 요청하는 안내만 출력.
    # delete_ok: 소비 가능한 gate 가 없거나 전이하지 못하면 False 로 보존한다 —
    # 삭제하면 verifier ✅/❌ 가 유실된다("파일 삭제는 소비자 소유" 원칙).
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
            # 단 원본 gate 의 편집 카운터에 이 워크트리 경로가 실제로 섞여 있을 때만
            # (교차 오염의 직접 증거) — 없으면 무관한 병렬 세션의 gate 를 남의 판정으로
            # 전이시키는 역방향 오염이 된다.
            if not _gate or _gate["state"] not in ("approved", "verified"):
                _main = lib.worktree_main_root(_root)
                if _main and lib.is_project_init_managed(_main):
                    _m_state = lib.load_state(_main)
                    _m_gate = lib.current_gate(_m_state)
                    if (
                        _m_gate
                        and _m_gate["state"] in ("approved", "verified")
                        and _gate_touches_tree(_m_gate, _root)
                    ):
                        _log("[update_docs] 워크트리에 gate 없음 — 교차 집계된 원본 gate 로 fallback")
                        _root, _state, _gate = _main, _m_state, _m_gate
            if not _gate or _gate["state"] not in ("approved", "verified"):
                delete_ok = False  # 판정 보존 — 삭제하면 verifier ✅/❌ 가 유실된다
                _log(
                    "[update_docs] ⚠️ 판정을 반영할 gate 가 없습니다"
                    f"(state={_gate['state'] if _gate else None!r}) — gate 갱신 건너뜀.\n"
                    "  결과 파일은 보존합니다 — /done 의 복구 경로 또는 재검증이 소비합니다."
                )
            elif verdict not in ("✅", "❌"):
                delete_ok = False  # 보존 — verifier 가 다시 쓰거나 /done 이 복구 시도
                _log(
                    f"[update_docs] ⚠️ verdict 파싱 불가({str(raw_verdict)!r}) — gate 갱신 건너뜀.\n"
                    '  verdict 는 정확히 "✅" 또는 "❌" 여야 합니다(수식어·혼재 불가).\n'
                    "  결과 파일은 보존합니다 — @verifier 를 다시 호출해 재작성하세요."
                )
            else:
                delete_ok = False  # 전이 성공을 확인하기 전까지는 보존
                lib.enter_verified(_gate, verdict)  # verified 진입 단일 출처
                lib.save_state(_root, _state)
                delete_ok = True  # 소비 완료 — 이제 지워도 안전
                _div = "━" * 60
                if verdict == "✅":
                    _smells_block = ""
                    if code_smells:
                        _smells_lines = "\n".join(f"  • {s}" for s in code_smells)
                        _smells_block = (
                            f"\n▌ 설계 냄새 (판정 영향 없음 — 관찰 기록)\n{_smells_lines}\n"
                        )
                    _msg = (
                        f"\n{_div}\n"
                        f"✅ verifier 검증 통과 — 사용자 결정 대기\n"
                        f"{_div}\n"
                        f"\n"
                        f"▌ 검증 결과 요약\n"
                        f"  feature: {feature_name}\n"
                        f"  판정   : {verdict}\n"
                        f"  근거   : {evidence or '(없음)'}\n"
                        f"{_smells_block}"
                        f"\n"
                        f"▌ 사용자에게 다음 토큰 중 하나 입력 요청\n"
                        f"  /done      작업 완료로 마감 (체크포인트 정리)\n"
                        f"  /rollback  변경을 모두 되돌리고 시작점으로 복원\n"
                        f"\n"
                        f"▌ Claude 행동 지시\n"
                        f"  변경 사항 핵심을 한국어로 한 단락 요약하고 위 두 옵션을\n"
                        f"  안내한 뒤, 사용자 입력을 기다린다.\n"
                        f"{_div}"
                    )
                    _emit_advisory(_msg)
                else:
                    _issues_text = (
                        "\n".join(f"  • {i}" for i in issues) if issues else "  (상세 사유 없음)"
                    )
                    _has_ckpt = bool(_gate.get("checkpoint_commit") or _gate.get("cp_snapshot"))
                    _rollback_line = (
                        "  /rollback  변경을 모두 되돌리고 처음부터 다시\n"
                        if _has_ckpt
                        else "  /rollback  ⚠️  체크포인트 없음 — 사용 불가 (/skip 또는 /done 권장)\n"
                    )
                    # 실패 사유 분류 — 구현 결함과 검증 한계/환경 제약의 안내를 가른다
                    if failure_category in _FAILURE_CATEGORY_INFO:
                        _cat_desc, _cat_hint = _FAILURE_CATEGORY_INFO[failure_category]
                        _cat_block = (
                            f"▌ 실패 분류 (verifier 보고)\n"
                            f"  {failure_category}: {_cat_desc}\n"
                            f"  권장: {_cat_hint}\n"
                        )
                    else:
                        _cat_block = (
                            "▌ 실패 분류 (verifier 보고)\n"
                            "  (미분류 — verifier 가 failure_category 를 기입하지 않음)\n"
                        )
                    _msg = (
                        f"\n{_div}\n"
                        f"❌ verifier 검증 실패 — 사용자 결정 대기\n"
                        f"{_div}\n"
                        f"\n"
                        f"▌ 검증 결과 요약\n"
                        f"  feature: {feature_name}\n"
                        f"  판정   : {verdict}\n"
                        f"\n"
                        f"▌ 발견된 문제\n"
                        f"{_issues_text}\n"
                        f"\n"
                        f"{_cat_block}"
                        f"\n"
                        f"▌ 사용자에게 다음 토큰 중 하나 입력 요청\n"
                        f"  /retry     같은 체크포인트에서 Claude 가 문제를 수정해 재시도\n"
                        f"  /skip      현재 상태 보존하며 gate 마감 (문제 인지 후 유지)\n"
                        f"  /done      현재 상태 보존하며 gate 마감 (/skip 과 동일)\n"
                        f"{_rollback_line}"
                        f"\n"
                        f"▌ Claude 행동 지시\n"
                        f"  발견된 문제와 추정 원인을 한국어로 풀어 설명하고,\n"
                        f"  실패 분류를 반영해(구현 결함이 아닐 수 있으면 그 사실을 명시)\n"
                        f"  네 가지 선택지의 의미를 사용자가 결정할 수 있게 안내한다.\n"
                        f"  추가 Edit 시도는 D1 lock 으로 차단되므로 사용자 결정 전까지 멈춘다.\n"
                        f"{_div}"
                    )
                    _emit_advisory(_msg)
    except Exception as _e:  # plan-gate 통합 실패는 verifier 흐름을 깨뜨리지 않는다
        _log(f"[update_docs] plan-gate 통합 경고: {_e}")

    return 0, delete_ok  # 삭제는 main 이 delete_ok 를 보고 결정한다 (소비자 소유)


if __name__ == "__main__":
    sys.exit(main())
