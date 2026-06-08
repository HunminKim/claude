# 하네스 수정 레포트 — project-init plugin

- **프로젝트**: DAIR-YOLO
- **점검일**: 2026-06-08 (KST)
- **판정**: ❌ 하네스 수정 필요

---

## 발견된 문제 요약

| # | 문제 | 심각도 | 유형 |
|---|------|--------|------|
| 1 | "확신 없는 단정 (✅/❌) 표시" 사전 가드 완전 부재 — 사용자 가설 검증 시 메인 Claude 가 일부 모듈만 보고 단정 답변 가능 | ❌ | 템플릿 구조 |
| 2 | completion_report ↔ checklist 정합성 차이 4건 | ⚠️ | 일회성 (이 프로젝트 누적 산출물 매핑 누락) |

---

## 본 사건 요약

DAIR-YOLO 의 DMS Stage-2 head 사용에 대한 사용자 가설 검증 중 발생:
- 사용자 가설 ("Pose 0번 Head + 객체 1, 2번 Head 사용") 에 대해 메인 Claude 가 `forward_head` / `_inference` 만 읽고 `❌` 단정
- 영향 모듈이 여러 군데 (head + loss + assigner + runtime) 일 가능성 명시 안 함
- 사용자 "Assigner 부분 보고와볼래?" 지시 후 `DMSPoseLoss` 의 **scale-split TAL** (face GT→P5, non-face GT→P3/P4) 발견 → 사용자 가설이 학습 의미상 정확함을 정정

핵심: "특정 모듈 안 봤다" 가 아니라 **"확인 안 된 가능성을 명시하지 않고 단정한 행동" 자체** 가 문제.

기존 가드 비교:
- `detect_bug_report.py` (THINK BEFORE FIXING) — 버그/에러 키워드 트리거. 가설 검증 발화 미발동
- `detect_user_correction.py` — 교정 *후* lessons.md 갱신 환기 (사후)
- `design-precheck.py` — 설계 키워드만

→ 본 패턴 사전 가드 0건.

---

## Upstream 수정 대상 (claude_skills 레포지토리)

> 아래 문제는 템플릿 구조에서 비롯된 것으로, claude_skills 레포에서 수정해야 한다.

### [문제 #1-A] `detect_bug_report.py` 키워드 확장 — 가설 검증 발화 포함

**영향 파일**
- `plugins/project-init/hooks/detect_bug_report.py`

**현재 코드 (라인 18-33)**
```python
BUG_PATTERNS = [
    r"버그", r"오류", r"에러", r"안\s*돼", r"안\s*됨", r"실패",
    r"\bbug\b", r"\berror\b", r"\bbroken\b", r"\bfailing\b",
    r"작동\s*안", r"동작\s*안", r"crash", r"깨짐",
]
```

**수정 후 (BUG_PATTERNS 그대로 + HYPOTHESIS_PATTERNS 추가)**
```python
BUG_PATTERNS = [
    r"버그", r"오류", r"에러", r"안\s*돼", r"안\s*됨", r"실패",
    r"\bbug\b", r"\berror\b", r"\bbroken\b", r"\bfailing\b",
    r"작동\s*안", r"동작\s*안", r"crash", r"깨짐",
]

# 사용자 가설/사실 확인 발화 — 단정 전 영향 범위 확장 검토 필요
HYPOTHESIS_PATTERNS = [
    r"맞[지나]\??$",                # "맞지?", "맞나?" 문장 끝
    r"이게\s.{1,20}\?",              # "이게 ~?"
    r"확인\s*해\s*줘",
    r"검증\s*해\s*줘",
    r"가설",
    r"어떻게\s*동작",
    r"\bis\s+this\s+correct\b",
    r"\bdoes\s+this\s+work\b",
    r"\bverify\b",
]
```

그리고 main 함수 분기 (라인 64 부근) 다음에:
```python
    if _matches(prompt, BUG_PATTERNS) and not _matches(prompt, TRIVIAL_PATTERNS):
        msg = _bug_msg()
    elif _matches(prompt, HYPOTHESIS_PATTERNS):
        msg = _hypothesis_msg()
    else:
        return 0
```

`_hypothesis_msg()` 본문:
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

**수정 이유**: 같은 훅 패턴 (UserPromptSubmit + 키워드 + additionalContext) 재사용 → 신규 훅 추가 없이 최소 변경. detect_bug_report.py 의 의미가 "버그 보고" → "단정 전 사고 가드" 로 확장됨.

**False-positive 위험**: 중간 (일상 어휘 "맞지?" 등). 단 메시지가 환기 톤 (차단 X) 이라 비용 작음.

---

### [문제 #1-B] CLAUDE.md template `Think Before Coding` 에 단정 가드 항목 추가

**영향 파일**
- `plugins/project-init/skills/project-init/assets/templates/CLAUDE.md`

**현재 코드 (라인 119-128, `## Think Before Coding` 섹션 마지막)**
```markdown
- **가정 명시**: 구현 전 내가 전제한 것이 있으면 반드시 "Assumption:" 으로 명시한다
- **혼란 표면화**: ...
- **해석 분기 표면화**: ...
- **단순한 방법 제안**: ...
- **절충안 명시**: ...
```

**수정 후 (마지막에 1줄 추가)**
```markdown
- **단정 전 영향 범위 확인**: 사용자 가설을 ✅/❌ 로 답하기 전, 이 동작에 관여할 수 있는 모듈 (단일 진입점 + 호출 체인 + cross-cutting hook: loss/assigner/runtime/postproc 등) 을 한 번 나열한다. 1개로 좁혀지지 않으면 단정 금지 → "확인한 범위" + "확인 안 한 가능성" 두 줄로 분리. ML/프레임워크 코드는 head/forward 만으로 동작이 결정되지 않는다.
```

**수정 이유**: 훅이 패턴을 놓쳤을 때 자연어 backstop. 매 세션 자동 로드되므로 0-cost. 훅(#1-A) + 자연어(#1-B) 이중 가드.

**False-positive 위험**: 없음 (환기성 자연어).

---

### 후보 비교표

| 후보 | 효과 | False-positive | 구현 비용 | 권장 |
|------|------|----------------|-----------|------|
| #1-A `detect_bug_report.py` 확장 | 사전 강제 | 중 (환기 톤이라 비용 작음) | 30줄 + 테스트 | ✅ 채택 |
| 신규 `detect_hypothesis_check.py` | 동일 | 동일 | wiring + 신규 파일. 비싸다 | ✗ 분리 이득 없음 |
| #1-B CLAUDE.md template 자연어 룰 | LLM 자가 가드 (backstop) | 없음 | 1줄 | ✅ 채택 (#1-A 와 병행) |
| verifier.md 수정 | 사후 — 본 사건 못 잡음 | 없음 | 0 | ✗ |

---

## 이 프로젝트 내 즉시 조치 사항

> 템플릿 수정과 별개로 이 프로젝트에서 당장 해야 할 것들. lessons 라인 27 (2026-06-03 "report 작성 ≠ 적용") 교훈 — **체크박스만 적지 말고 같은 사이클 내 commit 까지 마칠 것**.

- [ ] `/workspace/CLAUDE.md` 의 `## 개발 워크플로우` 섹션에 **"단정 전 영향 범위 확인"** 1줄 추가 (위 #1-B 본 프로젝트 버전 — template upstream 반영 전 즉시 효과)
- [ ] `.claude/memory/lessons.md` 2026-06-08 항목 (DMS Stage-2 사건) 에 "→ CLAUDE.md 가드 1줄 추가 완료 (YYYY-MM-DD)" 추적 표기
- [ ] (선택) `.claude/hooks/` 에 `detect_hypothesis_check.py` 사본 신설 — upstream 반영 전 효과 즉시 확보 (template 갱신 후 제거)
- [ ] completion_report ↔ checklist 정합성 차이 4건 매핑 정리 (별개 ⚠️ 이슈)

---

## 참고

- 본 사건 결과 산출 D-017 (decisions.md) — DMS scale-split TAL 동작 정의 (기술 사실)
- 본 사건 교정 패턴 lessons.md 2026-06-08 (행동 차원)
- 1단계 점검 보고서 — agent `aee85bb5fcefa51bf` (중단) + `a90f6981bdf1a7bcb` (재실행)
- 2단계 정밀 분석 — `adf5ce3a312ca9a1e` (claude-code-guide) + `a2e6e35a2755e8958` (general-purpose)
- 이전 레포트 (06-03 phase E NPU) → `docs/harness_fix_report.20260603_phase_e_npu.md.bak` 로 archive

---

## 처리 결과 (2026-06-08, 적용 완료)

> 검토 후 리포트 권고를 **수정해서** 적용함. 아카이브용 정정 기록.

- **#1-A `detect_bug_report.py` 정규식 훅 확장 → 폐기.** 검증자(general-purpose) 행위 검토 결과:
  1. 위험 시점은 **사용자 prompt** 가 아니라 **Claude 의 응답**(단정하는 순간)인데, UserPromptSubmit 훅은 코드 읽기 전 prompt 만 보므로 신호의 시점·대상이 틀림.
  2. 자연어 가설 표현은 무한 → 정규식 FP/FN 두더지잡기. 특히 `$` 앵커가 멀티문장 가설(본 사건 유형)을 통째로 놓침.
  3. 잘못 발동하는 환기는 모든 환기를 무디게 함(boy-who-cried-wolf).
  → "Claude 가 출력에서 단정하려는 순간"을 잡을 훅 지점 자체가 없음. Subtraction-First 원칙에 따라 훅 미채택.
- **#1-B 템플릿 CLAUDE.md 자연어 룰 → 채택(단독 주 가드).** ML 특정 표현(`head/forward`) 제거하고 범용 룰로 다듬음. LLM 이 문맥으로 해석 → 키워드 트리거 불필요, FP 0, 매 세션 자동 로드.
- **버전**: project-init `1.26.4` → `1.27.0` (minor) + marketplace.json 동기화.
- **행위 검증**: 새 룰을 받은 서브에이전트가 인증 가설 질문에 ✅/❌ 단정 회피 + "확인한 범위/확인 안 한 가능성" 분리 확인 (통과).
- **잔여 ⚠️**: completion_report ↔ checklist 정합성 4건은 별개 이슈로 미처리.
