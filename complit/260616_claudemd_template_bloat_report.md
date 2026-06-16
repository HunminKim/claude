# 하네스 수정 레포트 — CLAUDE.md 템플릿 198줄 모순

- **대상**: claude (하네스 본체) — `plugins/project-init/`
- **점검일**: 2026-06-16 (KST)
- **판정**: ⚠️ 템플릿 구조 문제 (structural, not one-off)
- **트리거**: demo-init `/project-init` 결과 CLAUDE.md 198줄 생성 → SKILL.md 자체 규칙 "≤100줄" 위반
- **진단 방식**: harness-check 위임 — claude-code-guide(Anthropic best practice) + general-purpose(템플릿 중복 분석) 병렬

---

## 발견된 문제 요약

| # | 문제 | 심각도 | 유형 |
|---|------|--------|------|
| 1 | CLAUDE.md 템플릿이 198줄 — 제네릭 절차 규칙 ~165줄이 본문에 인라인됨 | ⚠️ | 템플릿 구조 |
| 2 | 그 ~165줄이 `workflow.md`(264줄)·`code-style.md`(78줄)와 3중 중복 | ⚠️ | 템플릿 구조 |
| 3 | 중복 내용이 매 세션 **2번 로드**됨 (CLAUDE.md 본문 + 헤더 line 4의 `@workflow.md` 강제 로드) | ⚠️ | 컨텍스트 비용 |
| 4 | 소유권 경계 미정의 — workflow.md:81 ↔ CLAUDE.md:78 이 서로를 SSOT로 가리킴 | ⚠️ | drift 위험 |

---

## 근본 원인 (표면이 아닌)

줄 수가 문제가 아니라 **잘못된 레이어에 제네릭 규칙이 적층된 것**이 원인. 인과 순서 (a)→(c)→(b):

**(a) 주 원인 — 잘못된 레이어 적층.** CLAUDE.md 템플릿에 모든 프로젝트 공통인 절차 규칙(서브에이전트 전략 91줄, Think Before Coding, 개발 워크플로우, 커밋, 주의사항)이 인라인됐다. 이 규칙들의 본래 집은 `memory/workflow.md`다 (workflow.md line 5: "3계층 과정 규칙. CLAUDE.md가 @참조로 세션 시작 시 로드한다"). 즉 workflow.md가 설계상 이 내용의 주인인데, **CLAUDE.md에도 같은 내용을 적고 지우지 않았다.**

**(c) 기여 — 경계 미정의.** "위임 규칙은 workflow.md 소유, CLAUDE.md 아님"을 강제하는 장치가 없다. 증거: workflow.md:81 은 "CLAUDE.md 의 … 참조"라 하고 CLAUDE.md:78 도 같은 말을 한다 — **둘이 서로를 SSOT로 지목** = 단일 주인이 없어 양쪽에 다 적힌 전형적 증상.

**(b) 기여 — 규칙만 선언, 템플릿 미정렬.** SKILL.md는 "≤100줄"을 3곳(line 109·183~198·536)에서 못박지만 템플릿은 그와 독립적으로 자라 198줄이 됐고 한 번도 reconcile되지 않았다. 템플릿 헤더 line 5("각 줄 기준: 없으면 Claude가 실수할까? — 아니라면 삭제")가 SKILL의 제외 기준을 재진술하는데 **템플릿이 자기 기준을 위반**한다.

### Anthropic 공식 근거 (claude-code-guide)
- CLAUDE.md는 **매 세션 시작 시 항상 로드**되어 매 턴 토큰을 소비 → "every session 보유해야 할 사실"만. **다단계 절차는 skill 또는 path-scoped rule로** 옮기라는 게 공식 권장.
- 로딩 모델: CLAUDE.md = always-on / `rules/*`(path-scoped) = 해당 파일 열 때만 / skill = 호출 시만. **제네릭 절차를 always-on 레이어에 두는 것 자체가 anti-pattern.**

---

## 중복 증거 (핵심 증상) — CLAUDE.md ↔ workflow.md

| 주제 | CLAUDE.md | workflow.md | 판정 |
|---|---|---|---|
| 서브에이전트 위임 흐름 + 에이전트 표 | 33-57 | 35-54 | 중복 (동일 표·동일 @backend/@infra 경계) |
| 위임 전 due diligence + Plan subagent 검증 | 59-73 | 56-79 | 중복 (동일 5항목 체크리스트) |
| 표준 위임 블록(TASK/USER_DECISIONS/…) | 75-102 | 81-90 | 중복 (workflow.md:81이 CLAUDE.md 참조라 명시) |
| 서브에이전트 보고 신뢰성(변경 증거) | 104-116 | 92-106 | 중복 |
| 위임 제약 | 118-123 | 108-111 | 중복 |
| plan-gate 동작(thrash/체크포인트/scope) | 31·138 | 179-258 | 중복 (workflow.md가 풀버전) |
| verifier 재시도 루프 / /compact / decisions.md / 장시간 Bash | 154-167·179 | 22-27·166-177·204-210 | 중복 |

추가로 **CLAUDE.md ↔ code-style.md** 도 중복 (SKILL.md:193이 금지하는데도): 외과적 변경 원칙(140-147) ↔ code-style.md 26-31·39, Think Before Coding(124-134) ↔ code-style.md 21-24.

→ **3중 중복(CLAUDE.md ↔ workflow.md ↔ code-style.md).** 어느 규칙을 고치면 2~3곳을 동시에 고쳐야 하고, 안 하면 조용히 divergence.

---

## Upstream 수정 (claude 레포 기준)

**대상 파일**: `plugins/project-init/skills/project-init/assets/templates/CLAUDE.md`

섹션별 조치 (대부분 **삭제-as-중복**, 일부만 MOVE):

| CLAUDE.md 섹션 / 줄 | 조치 | 대상 | 근거 |
|---|---|---|---|
| `## 서브에이전트 전략` 33-123 (91) | **삭제** | workflow.md 35-111 에 이미 존재 | 순수 중복. 단 "메인이 직접 처리" 리스트(38-41)만 workflow.md로 MOVE |
| `## Think Before Coding` 124-134 (11) | **MOVE** | rules/code-style.md `## 작업 프로세스` | 코딩 규율 → code edit 시 조건부 로드되는 곳이 올바른 트리거. 21-24 중복분은 drop |
| `## 개발 워크플로우 > 외과적 변경 원칙` 140-147 (8) | **삭제/부분 MOVE** | code-style.md 26-31·39 | 대부분 중복. "기존 파일 전체 재작성 금지"(147) 뉘앙스만 code-style.md로 |
| `## 개발 워크플로우` 나머지 135-186 (≈45) | **삭제** | workflow.md 다수 | 삭제-as-중복. 명령어 추가 규칙(183-185)·secrets→.env(177) 등 미존재분만 workflow.md로 MOVE |
| `## 커밋` 187-195 (9) | **MOVE** | workflow.md 신규 `## 커밋` | 커밋 포맷은 전 프로젝트 동일 = 제네릭 |
| `## 주의사항` 196-198 (3) | **MOVE** | workflow.md | debug 노트 컨벤션도 제네릭 |
| `## 알려진 버그 / 제약` line 31 | **삭제** | workflow.md 179-258 | 프레임(27-30)은 placeholder로 유지, prefill된 plan-gate 문단은 제거 |

**수정 후 CLAUDE.md 템플릿 잔존**: 헤더/preamble(트림) + 프로젝트 + 주요 구조 + 명령어 + 알려진 버그/제약(빈 placeholder) + `## 핵심 워크플로우 포인터`(workflow.md/code-style.md 가리키는 3-5줄). **목표 ~40-55줄** — 100 ceiling 아래, 프로젝트 고유 정보(버그·명령어·제약)에 예산을 남김.

---

## ⚠️ 더 깊은 구조 질문 (Phase 2 후보)

위 수정은 **중복 제거 + CLAUDE.md 트림**이다. 하지만 옮겨갈 `workflow.md`(264줄)는 **헤더 line 4의 `@workflow.md`로 매 세션 always-load** 된다. 즉 중복은 없어져도 always-on 컨텍스트 총량은 여전히 크다.

Anthropic 권장(절차 = skill 또는 path-scoped rule, on-demand)에 비춰보면:
- **code-style.md** → 이미 path-scoped(조건부) ✓ 올바름
- **workflow.md** → 현재 always @-load. 절차 규칙(위임·plan-gate)을 **on-demand skill**로 뺄지, 최소 always-load 코어 + 나머지 on-demand로 분할할지 = 별도 설계 결정.

→ **권고**: Phase 1(이 레포트의 중복 제거)로 모순 즉시 해소. Phase 2(workflow.md always-load 적정성)는 별도 논의.

---

## SKILL.md 동반 수정

- `### 3단계` 의 "≤100줄" 은 유지하되, **"포함할 것"에 "제네릭 절차는 CLAUDE.md 금지 → workflow.md/rules로" 명시 한 줄 추가**해 (a)/(c) 재발 방지.
- SKILL.md:128·296 의 pre-commit "CLAUDE.md 린트"가 **길이 체크를 실제로 하는지 확인** — 한다면 현 시드 템플릿(198줄)은 자기 린트에 걸림. 안 한다면 길이 게이트 추가 검토.

---

## 즉시 조치 / 미결 결정

- [ ] **결정 필요**: Phase 1 수정을 지금 적용할지 (= "하네스 고쳐줘" → 템플릿/SKILL 수정 → `plugin.json` 버전 번프 → 커밋·태그·푸시). 동작 영향 변경이므로 smoke_test + 실제 `/project-init` 재실행으로 행위 검증 필요.
- [ ] demo-init 의 198줄 CLAUDE.md: Phase 1 적용 후 재생성하거나 삭제 (테스트 산출물이라 보존 불요)
- [ ] Phase 2(workflow.md always-load) 별도 논의 여부
