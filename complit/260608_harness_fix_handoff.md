> ⚠️ **SUPERSEDED (2026-06-08)** — 이 문서는 #1-A(정규식 훅) 채택을 전제로 작성됨.
> 실제로는 검토 후 **#1-A 폐기, #1-B만 채택(v1.27.0)**. 최종 결정은
> `260608_harness_fix_report.md` 의 "처리 결과" 섹션 참조. 아래 내용은 기록용.

# 하네스 수정 핸드오프 (복붙용 단일 문서) — project-init plugin

> **이 문서 하나면 끝.** DMS Stage-2 head 단정 사건 → upstream(project-init plugin) 수정까지
> 재현·적용에 필요한 모든 것을 한 곳에 모음. 아래 0~5 순서대로 읽으면 됨.
> 출처: `260608_harness_fix_report.md` + lessons.md(라인 27·30) + 실제 현재 파일 4개.

---

## 0. 한 줄 요약

사용자 가설 검증 중 메인 Claude 가 `head`/`forward` 만 보고 `❌` 단정 → 실제론 loss/assigner 까지
영향 → 사용자 정정. **"확신 없는 단정(✅/❌)" 사전 가드가 0건**이라 같은 패턴 재발 가능.
수정안: `detect_bug_report.py` 키워드 확장(#1-A) + 템플릿 CLAUDE.md 자연어 backstop(#1-B), 이중 가드.

---

## 1. 동형 사고 패턴 (lessons.md 라인 27 + 30)

**라인 27 (2026-06-03) — "report 작성 ≠ 적용" 패턴**

| 날짜 | 사건 | 교훈 |
|------|------|------|
| 2026-06-03 | harness_fix_report 의 "즉시 조치 사항" 미적용 → 같은 알림 재발. 어제 report 에 "constraints.yaml exclude_dirs 추가" 를 명시했으나 **체크박스만 적고 실제 적용 안 함**. 오늘 cleanup_suggest 가 `exp_` prefix 로 정상 yaml 을 두 번째 오탐지 → 사용자 "네이밍 규칙이 잘못된 거 아니냐" 지적 | **report 작성 ≠ 적용.** "즉시 조치 사항" 체크박스는 그 사이클 내에 실제 파일 수정·commit 까지 마쳐야 함. 표시만 하면 다음 세션에 휘발. + 프로젝트 정상 명명규칙 vs plugin default temp_patterns 충돌 시 default 가 아닌 **project-local override (`docs/constraints.yaml`)** 가 정답 |

**라인 30 (2026-06-08) — 본 사건**

| 날짜 | 사건 | 교훈 |
|------|------|------|
| 2026-06-08 | DMS Stage-2 head 사용 사용자 가설 검증. head/forward 만 보고 사용자 가설 ③ 을 ❌ 로 단정. 영향 모듈이 여러 군데(head + loss + assigner)일 가능성을 짚지 않고 부분 정보로 답. 사용자 "수정의 여지" 지적 | 정답을 모를 때 **확신 없는 부분에 단정 표시(✅/❌) 사용 금지.** "확인한 범위: A·B" + "확인 안 한 가능성: C·D 모듈도 영향 줄 수 있어 추가 확인 필요" 두 줄로 분리. 단일 모듈로 결론 내기 전 "이 동작에 관여할 수 있는 다른 모듈(loss/assigner/runtime hook 등) 있는가?" 자체 질문 |

---

## 2. 수정 리포트 본문 (260608_harness_fix_report.md)

- **프로젝트**: DAIR-YOLO  **점검일**: 2026-06-08 (KST)  **판정**: ❌ 하네스 수정 필요

### 발견된 문제 요약

| # | 문제 | 심각도 | 유형 |
|---|------|--------|------|
| 1 | "확신 없는 단정(✅/❌)" 사전 가드 완전 부재 — 가설 검증 시 메인이 일부 모듈만 보고 단정 가능 | ❌ | 템플릿 구조 |
| 2 | completion_report ↔ checklist 정합성 차이 4건 | ⚠️ | 일회성 |

### 본 사건 요약

- 사용자 가설("Pose 0번 Head + 객체 1,2번 Head 사용")에 대해 `forward_head`/`_inference` 만 읽고 `❌` 단정
- 영향 모듈이 여러 군데(head + loss + assigner + runtime)일 가능성 명시 안 함
- 사용자 "Assigner 부분 보고와볼래?" 후 `DMSPoseLoss` 의 **scale-split TAL**(face GT→P5, non-face GT→P3/P4) 발견 → 사용자 가설이 학습 의미상 정확함을 정정

핵심: "특정 모듈 안 봤다"가 아니라 **"확인 안 된 가능성을 명시하지 않고 단정한 행동 자체"** 가 문제.

기존 가드 비교 — 본 패턴 사전 가드 0건:
- `detect_bug_report.py` (THINK BEFORE FIXING) — 버그/에러 키워드 트리거. 가설 검증 발화 미발동
- `detect_user_correction.py` — 교정 *후* lessons.md 갱신 환기 (사후)
- `design-precheck.py` — 설계 키워드만

### 후보 비교표

| 후보 | 효과 | False-positive | 비용 | 권장 |
|------|------|----------------|------|------|
| #1-A `detect_bug_report.py` 확장 | 사전 강제 | 중(환기 톤이라 작음) | 30줄+테스트 | ✅ 채택 |
| 신규 `detect_hypothesis_check.py` | 동일 | 동일 | wiring+신규파일. 비쌈 | ✗ 분리 이득 없음 |
| #1-B CLAUDE.md template 자연어 룰 | LLM 자가 가드(backstop) | 없음 | 1줄 | ✅ 채택(#1-A 병행) |
| verifier.md 수정 | 사후 — 본 사건 못 잡음 | 없음 | 0 | ✗ |

### 이 프로젝트(DAIR-YOLO) 내 즉시 조치 — 체크박스만 적지 말고 같은 사이클 내 commit 까지

- [ ] `CLAUDE.md` `## 개발 워크플로우` 에 "단정 전 영향 범위 확인" 1줄 추가 (#1-B 본 프로젝트 버전, upstream 반영 전 즉시 효과)
- [ ] `.claude/memory/lessons.md` 2026-06-08 항목에 "→ CLAUDE.md 가드 1줄 추가 완료 (날짜)" 추적 표기
- [ ] (선택) `.claude/hooks/detect_hypothesis_check.py` 사본 신설 — upstream 반영 전 즉시 효과 (template 갱신 후 제거)
- [ ] completion_report ↔ checklist 정합성 차이 4건 매핑 (별개 ⚠️)

---

## 3. [수정 #1-A] detect_bug_report.py — 현재 전문 + 변경 지점

**파일**: `plugins/project-init/hooks/detect_bug_report.py`

### 현재 전문 (검증됨, 라인 1-99)

```python
#!/usr/bin/env python3
"""UserPromptSubmit hook — 버그 보고 감지 시 'Think Before Fixing' 체크리스트 주입.

출력 채널: 환기 (exit 0 + stdout hookSpecificOutput.additionalContext JSON)

동작:
  1. stdin JSON에서 prompt 추출
  2. 버그 키워드 검사 (한영 혼재)
  3. 자명한 오탈자·누락 키워드 있으면 허용 (즉시 수정 예외)
  4. 버그 키워드 O + 오탈자 X → additionalContext 로 체크리스트 주입 (차단 아님)
"""
from __future__ import annotations

import json
import re
import sys

BUG_PATTERNS = [
    r"버그", r"오류", r"에러", r"안\s*돼", r"안\s*됨", r"실패",
    r"\bbug\b", r"\berror\b", r"\bbroken\b", r"\bfailing\b",
    r"작동\s*안", r"동작\s*안", r"crash", r"깨짐",
]

# 원인과 범위가 자명한 경우 — 즉시 수정 허용
TRIVIAL_PATTERNS = [
    r"오탈자", r"typo", r"스펠",
    r"import\s*(누락|missing)", r"missing\s*import",
    r"들여쓰기", r"indentation",
]

DIVIDER = "━" * 55


def _matches(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    prompt: str = data.get("prompt", "") or ""
    if not prompt:
        return 0

    # 버그 증상 기술 없이 수정 명령만 있으면 통과 (기능 요청·질문)
    if not _matches(prompt, BUG_PATTERNS):
        return 0

    if _matches(prompt, TRIVIAL_PATTERNS):
        return 0

    msg = "\n".join([
        "",
        DIVIDER,
        "[THINK BEFORE FIXING] 버그 보고 감지 — 즉시 수정 전 필수 확인",
        DIVIDER,
        "",
        "수정 전 반드시 아래 세 가지를 먼저 보고할 것:",
        "  1. 추정 원인  (어느 레이어, 어떤 코드)",
        "  2. 수정 범위  (건드릴 파일·함수)",
        "  3. 불확실한 점 (재현 조건·기대 동작 중 불명확한 것)",
        "",
        "세 항목이 명확할 때만 수정 진행.",
        "불명확하면 'Assumption: ...' 으로 가정을 명시한 뒤 확인 요청.",
        "",
        "예외(즉시 수정 가능): 오탈자·import 누락처럼 원인과 범위가 자명한 것.",
        DIVIDER,
        "",
    ])
    advisory = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": msg,
        }
    }
    sys.stdout.write(json.dumps(advisory, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

### 변경 ① — BUG_PATTERNS 아래에 HYPOTHESIS_PATTERNS 추가

```python
# 사용자 가설/사실 확인 발화 — 단정 전 영향 범위 확장 검토 필요
HYPOTHESIS_PATTERNS = [
    r"맞[지나]\??$",          # "맞지?", "맞나?" 문장 끝
    r"이게\s.{1,20}\?",        # "이게 ~?"
    r"확인\s*해\s*줘",
    r"검증\s*해\s*줘",
    r"가설",
    r"어떻게\s*동작",
    r"\bis\s+this\s+correct\b",
    r"\bdoes\s+this\s+work\b",
    r"\bverify\b",
]
```

### 변경 ② — main() 분기 교체 (현재 라인 63-68 영역)

현재의 `if not _matches(BUG_PATTERNS): return 0` / `if _matches(TRIVIAL): return 0` 단순 분기를
아래 3-way 분기로 교체. (msg 생성부는 `_bug_msg()` / `_hypothesis_msg()` 헬퍼로 분리)

```python
    if _matches(prompt, BUG_PATTERNS) and not _matches(prompt, TRIVIAL_PATTERNS):
        msg = _bug_msg()
    elif _matches(prompt, HYPOTHESIS_PATTERNS):
        msg = _hypothesis_msg()
    else:
        return 0
```

### 변경 ③ — `_hypothesis_msg()` 본문

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[THINK BEFORE ASSERTING] 사실 확인/가설 검증 요청 감지
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

단정 (✅/❌) 표시 전 다음 두 줄을 분리해서 응답할 것:
  1. "확인한 범위:"     — 직접 읽은 파일·함수
  2. "확인 안 한 가능성:" — 이 동작에 관여할 수 있으나
                            아직 안 본 모듈 (loss/assigner/runtime hook 등)

확인 안 한 가능성이 있으면 → 단정 금지, "추가 확인 필요" 표기.
영향 모듈이 1개로 좁혀졌을 때만 ✅/❌ 사용.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

- **이유**: 같은 훅 패턴(UserPromptSubmit + 키워드 + additionalContext) 재사용 → 신규 훅 없이 최소 변경. 훅 의미가 "버그 보고" → "단정 전 사고 가드" 로 확장.
- **FP 위험**: 중(일상어 "맞지?"). 단 환기 톤(차단 X)이라 비용 작음.
- **잊지 말 것**: `plugins/project-init/.claude-plugin/plugin.json` 버전 번프 + `marketplace.json` description 동기화 + 태그.

---

## 4. [수정 #1-B] 템플릿 CLAUDE.md — Think Before Coding 섹션

**파일**: `plugins/project-init/skills/project-init/assets/templates/CLAUDE.md` (라인 119-127)

### 현재 섹션 (검증됨)

```markdown
## Think Before Coding (구현 전 필수 확인)

- **가정 명시**: 구현 전 내가 전제한 것이 있으면 반드시 "Assumption:" 으로 명시한다
  - 가정이 틀렸을 때 되돌리기 어려운 작업이면 먼저 확인 요청
- **혼란 표면화**: 요청의 의미가 불명확하면 추측하지 않고 무엇이 불명확한지 명시한 뒤 묻는다 — "잘 모르겠어서 일단 해봤습니다" 금지
- **해석 분기 표면화**: 명령을 2가지 이상으로 해석할 수 있으면 위험도와 무관하게 각 해석을 나열하고 선택을 요청한다
  - 예외: 결과가 사실상 동일한 경우(단어만 다르고 실행 결과가 같을 때)만 바로 진행 가능
- **단순한 방법 제안**: 더 단순한 접근이 보이면 구현 전에 언급한다 ("더 간단한 방법이 있습니다: X. 계속 원래 방향으로 진행할까요?")
- **절충안 명시**: 설계 선택에 트레이드오프가 있으면 조용히 선택하지 않고 제시한 뒤 결정을 요청한다
```

### 추가할 1줄 (섹션 마지막 `절충안 명시` 다음)

```markdown
- **단정 전 영향 범위 확인**: 사용자 가설을 ✅/❌ 로 답하기 전, 이 동작에 관여할 수 있는 모듈 (단일 진입점 + 호출 체인 + cross-cutting hook: loss/assigner/runtime/postproc 등) 을 한 번 나열한다. 1개로 좁혀지지 않으면 단정 금지 → "확인한 범위" + "확인 안 한 가능성" 두 줄로 분리. ML/프레임워크 코드는 head/forward 만으로 동작이 결정되지 않는다.
```

- **이유**: 훅이 패턴을 놓쳤을 때 자연어 backstop. 매 세션 자동 로드 → 0-cost. 훅(#1-A) + 자연어(#1-B) 이중 가드.
- **FP 위험**: 없음(환기성 자연어).

---

## 5. hooks.json wiring (참고 — 변경 불필요)

**파일**: `plugins/project-init/hooks/hooks.json`

```json
"UserPromptSubmit": [
  {
    "hooks": [
      { "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/detect_bug_report.py", "timeout": 5 },
      { "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/detect_user_correction.py", "timeout": 5 },
      { "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/plan_approval.py", "timeout": 10 },
      { "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/detect_task_boundary.py", "timeout": 10 },
      { "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/delegation_due_diligence.py", "timeout": 5 }
    ]
  }
]
```

→ #1-A 는 같은 `detect_bug_report.py` 안에서 분기만 확장 → wiring 변경 불필요.

---

## 적용 순서 (요약)

1. #1-A: `detect_bug_report.py` 변경 ①②③ → ruff 통과 확인 → 행위 검증(가설 발화로 훅 발동 테스트)
2. #1-B: 템플릿 CLAUDE.md 1줄 추가
3. `plugin.json` 버전 번프(minor) + `marketplace.json` description 동기화 + `README.md` 동기화
4. 커밋 → `git tag -a vX.Y.Z` → `git push origin main --tags`
5. (DAIR-YOLO 측) §2 "즉시 조치 사항" 4건을 같은 사이클 내 commit 까지 완료 — **체크박스만 적지 말 것**(lessons 라인 27)
