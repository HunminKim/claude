#!/usr/bin/env python3
"""
verifier가 docs/.verifier_result.json 을 생성하면
자동으로 checklist.md, completion_report.md, technical_doc.md 를 업데이트한다.

이 파일은 verifier 외에 아무도 건드려서는 안 된다.
"""

import json
import os
import re
import sys
from pathlib import Path

# 1. 훅 입력 읽기
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

tool_name = data.get("tool_name", "")
tool_input = data.get("tool_input", {})
file_path = tool_input.get("file_path", "")

# 2. Write 툴로 .verifier_result.json 을 쓴 경우만 처리
if tool_name != "Write" or not file_path.endswith(".verifier_result.json"):
    sys.exit(0)

result_path = Path(file_path)
if not result_path.exists():
    sys.exit(0)

# 3. 결과 파일 읽기
try:
    with open(result_path) as f:
        result = json.load(f)
except Exception as e:
    print(f"[update_docs] 결과 파일 읽기 실패: {e}", file=sys.stderr)
    sys.exit(1)

docs_dir = result_path.parent
feature_name = result.get("feature_name", "알 수 없음")
timestamp = result.get("timestamp", "")
verdict = result.get("verdict", "❓")
test_items = result.get("test_items", [])
issues = result.get("issues", [])
code_smells = result.get("code_smells", [])
critical_constraints = result.get("critical_constraints", [])
evidence = result.get("evidence", "")
impl = result.get("implementation", {})

# ── checklist.md 업데이트 ──────────────────────────────────────────────────
checklist_path = docs_dir / "checklist.md"
checklist_phase = result.get("checklist_phase", "")
checklist_row = result.get("checklist_row", None)

if checklist_path.exists():
    content = checklist_path.read_text()
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
        checklist_path.write_text("\n".join(lines))
        print(f"[update_docs] checklist.md 업데이트 완료: {feature_name}")
    else:
        hint = f"{checklist_phase} #{checklist_row}" if checklist_phase else feature_name
        print(
            f"[update_docs] ⚠️ checklist.md 업데이트 실패: '{hint}' 항목 없음 — 수동 업데이트 필요",
            file=sys.stderr,
        )

# ── completion_report.md 업데이트 ─────────────────────────────────────────
report_path = docs_dir / "completion_report.md"
if report_path.exists():
    content = report_path.read_text()

    # 테이블 마지막 행 다음에 항목 추가
    issues_text = "\n".join(f"- {i}" for i in issues) if issues else "없음"
    test_rows = "\n".join(
        f"| {t['item']} | {t['result']} | {t.get('note', '')} |" for t in test_items
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
        print(
            f"[update_docs] completion_report.md 이미 등록됨: '{feature_name}', 건너뜀",
            file=sys.stderr,
        )
    else:
        content += section
        report_path.write_text(content)
        print(f"[update_docs] completion_report.md 업데이트 완료: {feature_name}")

# ── technical_doc.md 업데이트 ─────────────────────────────────────────────
tech_path = docs_dir / "technical_doc.md"
if tech_path.exists():
    content = tech_path.read_text()

    files_text = (
        "\n".join(f"- `{f['path']}` — {f['role']}" for f in impl.get("files", [])) or "- 없음"
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
    tech_path.write_text(content)
    print(f"[update_docs] technical_doc.md 업데이트 완료: {feature_name}")

# ── CLAUDE.md 알려진 버그/제약 업데이트 ──────────────────────────────────
if critical_constraints:
    claude_path = docs_dir.parent / "CLAUDE.md"
    if claude_path.exists():
        content = claude_path.read_text()
        section_header = "## 알려진 버그 / 제약"
        new_items = "\n".join(f"- {c}" for c in critical_constraints)

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
            claude_path.write_text("\n".join(lines))
        else:
            # 섹션 자체가 없으면 파일 끝에 추가
            content += f"\n\n{section_header}\n\n{new_items}\n"
            claude_path.write_text(content)

        print(f"[update_docs] CLAUDE.md 알려진 버그/제약 업데이트: {len(critical_constraints)}건")

# ── plan-gate state 갱신 (D3) ─────────────────────────────────────────────
# verifier 결과를 plan-gate 상태에 반영. 자동 /done이나 /rollback은 하지 않고,
# 사용자에게 결정 토큰(/retry, /done, /rollback)을 요청하는 안내만 출력.
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import plan_gate_lib as _pglib  # noqa: E402

    _root = _pglib.find_project_root()
    if _root and _pglib.is_project_init_managed(_root) and verdict in ("✅", "❌"):
        _state = _pglib.load_state(_root)
        _gate = _pglib.current_gate(_state)
        if _gate and _gate["state"] in ("approved", "verified"):
            _gate["state"] = "verified"
            _gate["verifier_status"] = verdict
            _pglib.save_state(_root, _state)
            _div = "━" * 60
            if verdict == "✅":
                _smells_block = ""
                if code_smells:
                    _smells_lines = "\n".join(f"  • {s}" for s in code_smells)
                    _smells_block = (
                        f"\n▌ 설계 냄새 (판정 영향 없음 — 관찰 기록)\n{_smells_lines}\n"
                    )
                print(
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
            else:
                _issues_text = (
                    "\n".join(f"  • {i}" for i in issues) if issues else "  (상세 사유 없음)"
                )
                _has_ckpt = bool(_gate.get("checkpoint_clean_tag"))
                _rollback_line = (
                    "  /rollback  변경을 모두 되돌리고 처음부터 다시\n"
                    if _has_ckpt
                    else "  /rollback  ⚠️  체크포인트 없음 — 사용 불가 (/skip 또는 /done 권장)\n"
                )
                print(
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
                    f"▌ 사용자에게 다음 토큰 중 하나 입력 요청\n"
                    f"  /retry     같은 체크포인트에서 Claude 가 문제를 수정해 재시도\n"
                    f"  /skip      현재 상태 보존하며 gate 마감 (문제 인지 후 유지)\n"
                    f"  /done      현재 상태 보존하며 gate 마감 (/skip 과 동일)\n"
                    f"{_rollback_line}"
                    f"\n"
                    f"▌ Claude 행동 지시\n"
                    f"  발견된 문제와 추정 원인을 한국어로 풀어 설명하고,\n"
                    f"  네 가지 선택지의 의미를 사용자가 결정할 수 있게 안내한다.\n"
                    f"  추가 Edit 시도는 D1 lock 으로 차단되므로 사용자 결정 전까지 멈춘다.\n"
                    f"{_div}"
                )
except Exception as _e:  # plan-gate 통합 실패는 verifier 흐름을 깨뜨리지 않는다
    print(f"[update_docs] plan-gate 통합 경고: {_e}", file=sys.stderr)

# 4. 결과 파일 삭제 (verifier 전용 임시 파일)
result_path.unlink()
print("[update_docs] .verifier_result.json 삭제 완료")
