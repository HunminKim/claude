#!/usr/bin/env python3
"""
verifier가 docs/.verifier_result.json 을 생성하면
자동으로 checklist.md, completion_report.md, technical_doc.md 를 업데이트한다.

이 파일은 verifier 외에 아무도 건드려서는 안 된다.
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime

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
        # fallback: feature_name 텍스트 매칭
        for i, line in enumerate(lines):
            if feature_name in line and "|" in line:
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
        print(f"[update_docs] ⚠️ checklist.md 업데이트 실패: '{hint}' 항목 없음 — 수동 업데이트 필요", file=sys.stderr)

# ── completion_report.md 업데이트 ─────────────────────────────────────────
report_path = docs_dir / "completion_report.md"
if report_path.exists():
    content = report_path.read_text()

    # 테이블 마지막 행 다음에 항목 추가
    issues_text = "\n".join(f"- {i}" for i in issues) if issues else "없음"
    test_rows = "\n".join(
        f"| {t['item']} | {t['result']} | {t.get('note','')} |"
        for t in test_items
    )
    section = f"""
### {feature_name} — {timestamp}
**판정**: {verdict}

**검증 항목**
| 항목 | 결과 | 비고 |
|------|------|------|
{test_rows}

**발견된 문제**: {issues_text}

**검증 근거**: {evidence}

---"""

    content += section
    report_path.write_text(content)
    print(f"[update_docs] completion_report.md 업데이트 완료: {feature_name}")

# ── technical_doc.md 업데이트 ─────────────────────────────────────────────
tech_path = docs_dir / "technical_doc.md"
if tech_path.exists():
    content = tech_path.read_text()

    files_text = "\n".join(
        f"- `{f['path']}` — {f['role']}"
        for f in impl.get("files", [])
    ) or "- 없음"
    interface = impl.get("interface", {})

    section = f"""
### {feature_name} — {timestamp}

**구현 내용**
{impl.get('description', '-')}

**주요 로직**
{impl.get('logic', '-')}

**관련 파일**
{files_text}

**인터페이스**
- 입력: {interface.get('input', '-')}
- 출력: {interface.get('output', '-')}

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

# 4. 결과 파일 삭제 (verifier 전용 임시 파일)
result_path.unlink()
print(f"[update_docs] .verifier_result.json 삭제 완료")
