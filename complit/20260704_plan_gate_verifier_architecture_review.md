# [하네스 아키텍처 진단] plan-gate 중앙집중화 · verifier 단일화 — 보고 검증과 업데이트 안

> 사용자 보고 2건(문제점 1: plan-gate 중앙집중화, 문제점 2: verifier 단일화)을
> 코드·사고 이력·외부 운영 사례 기준으로 검증하고, 업데이트 필요 여부와 권장안을 도출한 진단 리포트.
> 검증 기준: project-init v2.17.0, `docs/MANUAL.md`, `plugins/project-init/hooks/*`, `complit/` 사고 이력.
> 작성: 2026-07-04 (KST) · 진단 전용 — 코드 수정 없음.

---

## 0. TL;DR

| 보고 | 판정 | 업데이트 권고 |
|------|------|--------------|
| 문제점 1: plan-gate 중앙집중화 | **부분 사실** — 책임 집중은 실재하나, 회귀 위험의 실체는 "파일이 크다"가 아니라 **공유 gate 상태의 무락(lock-free) 다중 쓰기 + 구체적 결합 3곳**. failure-loop는 이미 독립이라 보고 일부는 과장 | **전면 분해 비권장.** 대신 표적 보수 4건 (결합 절단 2 + 진단성 1 + 계약 고정 1) |
| 문제점 2: verifier 단일화 | **대체로 사실** — 작업 유형 축 분기 없음, 실패 원인 구조화 없음, 문서 변경에 오히려 과잉검증 방향 | **업데이트 권장.** 단일 verifier 유지 + **작업 유형별 검증 프로파일 + 실패 사유 taxonomy** 주입 (다중 verifier 에이전트는 비권장) |

---

## 1. 문제점 1 — plan-gate 중앙집중화

### 1.1 보고 검증 결과

**사실인 부분:**

- `plan_gate_lib.py` 1,653줄 한 파일에 상태 전이·체크포인트(git/cp 이중 백엔드)·layer-1/2 스코프·do-not-touch·thrash 휴리스틱·감사 로그·메시지 포맷터·todo 품질 검사·패치 이력이 공존. 이 lib 을 9개 스크립트가 소비하고, gate dict(`plan_gate.json`, 필드 20여 개)를 **7개 진입점이 각자 load→변형→save** 한다.
- 회귀 사고 이력 실재: stash 유령 삭제(PGSD-2026-05-11, `stash -u` 부작용), created 게이트 `/done` 오추천(2026-07-03), guard divergence 리포트(260616), plan_gate.py 주석에 박제된 순서 회귀("early-return 하면 반복편집 트리거가 영구히 죽는다 — v1.28.0 회귀", plan_gate.py:253-257).
- 사용자 인식 문제도 사실의 근거가 있음: 차단 원인별로 사람이 읽는 헤더·다음 액션 토큰은 다르지만, **기계 판독용 안정 에러 코드는 사용자 메시지에 없다**(감사 로그 action 문자열에만 존재).

**과장/오류인 부분:**

- **failure-loop는 중앙집중화되어 있지 않다.** `detect_failure_loop.py`(227줄)는 `plan_gate_lib` 를 import 조차 안 하는 완전 독립 훅이며 자체 상태 파일(`failure_log.json`)을 쓴다.
- 메시지 빌더(순수 함수 15개), 패치 이력/hot-file(별도 상태 파일), GC, CLI(COMMANDS SSOT)는 이미 절단면이 깨끗하다.
- 저장소는 이미 회귀를 인지하고 완화 장치를 넣어둔 상태다: `transition()` 4-전이 중앙화, smoke_test 행위 검증 강제, 회귀 이력 주석.

**회귀 위험의 실제 위치 (코드로 특정된 결합 3곳 + 경합 1곳):**

| # | 결합 | 내용 |
|---|------|------|
| C1 | `_VERIFY_RE` 다중 소비 | 검증 명령 정규식(lib:738-752) 하나가 ① thrash 카운터 리셋, ② soft hint, ③ **created 게이트 자동 롤오버 → 체크포인트 삭제 시점**(lib:718-730 → `do_gate_done`)을 동시에 좌우. 러너 하나 추가가 "롤백 불가능해지는 순간"을 바꾼다 |
| C2 | 체크포인트 ↔ 스코프 강등 | `sweep_effective_mode`(lib:476-484): `checkpoint_commit` 부재 시 enforce→shadow 자동 강등. `/plan-gate-no-git` 이 **스코프 enforce 를 조용히 무력화**하는 경로. 이 결합 때문에 `_approve_fresh_gate`(cli:73-99)가 스냅샷 캡처를 중복 구현 |
| C3 | `verifier_status` 우회 쓰기 | verified/done/rolled_back 진입이 `transition()` 을 우회해 3곳에 분산: `update_docs.py:326-327` 직접 대입, `plan_gate_cli.py:163-165` 파일 복구, `do_gate_done`/`cmd_rollback`. 읽는 곳은 6곳 |
| C4 | UserPromptSubmit 경합 | 같은 이벤트에 `plan_approval.py`(CLI 서브프로세스 경유 저장)와 `detect_task_boundary.py`(:85, :115)가 배선되어 같은 `plan_gate.json` 을 통째로 씀. `save_state` 는 atomic replace 일 뿐 락이 없어 last-writer-wins |

### 1.2 외부 근거 (운영 사례·권장 패턴)

- **Anthropic 공식 레퍼런스 하네스** (`anthropics/cwc-long-running-agents`): 기능당 1훅(track-read / verify-gate / commit-on-stop / kill-switch) + "short, readable hooks… assemble a harness tuned to your project". 단 이는 상태 없는 프리미티브 컬렉션이며, plan-gate 처럼 상태 머신을 공유하는 로직은 쪼개도 결국 공용 lib 에 남는다.
- **Claude Code 훅 시스템 자체가 policy-combination 시맨틱 제공** (hooks-guide): 여러 훅의 PreToolUse 판정을 most-restrictive(deny > defer > ask > allow)로 병합 — 기능별로 쪼개도 조합 비용이 낮게 설계됨. 단 **같은 입력을 수정하는 훅 다중화는 비결정적**(병렬 실행, last-finish-wins)이라 금지 조건.
- **OPA(정책 엔진) 패턴**: 독립 정책 모듈 → 단일 결정/집행점. "검사 로직은 모듈로, enforcement 는 한 지점"이 표준.
- **반대 근거**: 공식 문서 "Chasing every finding leads to over-engineering", 커뮤니티 "Three hooks that always run beat 30 pages of advisory documentation", "훅은 hot path — 모든 툴콜에 지연 가산 + 훅 수가 늘면 디버깅이 어려워진다". 반대론은 전부 **"훅 개수·상시 실행 비용을 늘리지 말라"**이지 "한 파일에 합쳐라"가 아님.
- **"Every harness component assumes the model can't do something; those assumptions expire"** (Anthropic harness design) — 이 저장소의 Subtraction-First 원칙과 동일.

### 1.3 진단

업데이트할 가치는 있으나, **보고가 암시하는 방향(기능별 시스템 분리)은 틀렸다.**

- 기능들을 별도 훅/별도 시스템으로 쪼개면: 등록 훅 수 증가(hot path 비용), 공유 상태는 여전히 공유(회귀 위험 그대로), 이벤트당 프로세스 수 증가(C4 류 경합 확대). 얻는 것은 파일 배치의 미관뿐.
- 실제 회귀는 "책임이 한 파일에 있어서"가 아니라 **C1~C3 같은 암묵적 결합이 계약 없이 존재해서** 발생했다. 파일을 옮겨도 결합은 남는다.
- 사용자 인식 문제(모든 차단이 plan-gate 하나로 보임)는 분해가 아니라 **에러 코드 태깅**으로 푸는 것이 맞다.

### 1.4 권장 업데이트 안 (표적 보수 — 우선순위순)

**P1-A. 차단 메시지 안정 에러 코드 도입 (진단성 — 사용자 체감 최대, 위험 최소)**

모든 차단/환기 헤더에 기계·사람 공용 코드를 태깅: 예 `PG-TRIGGER`(승인 필요), `PG-D1`(검증 ❌ 잠금), `PG-THRASH`, `PG-SCOPE-L1`, `PG-SCOPE-L2`, `PG-DNT`(do-not-touch), `FL-LOOP`(failure-loop, plan-gate 아님을 명시). 감사 로그 action 과 1:1 매핑하고, `/plan-gate-help` 와 MANUAL FAQ 를 코드 기준으로 재편. → "어느 가드가 왜 막았나"가 한 줄로 판별되어 보고서의 디버깅 문제를 직접 해소. 메시지 포맷터가 이미 순수 함수라 수정 범위가 좁다.

**P1-B. 상태 전이 쓰기 경로 수렴 (C3 절단)**

verified/done/rolled_back 진입 직접 대입 3곳을 `transition()` 계열 헬퍼(`enter_verified(gate, verdict)` 등)로 수렴. lib:635-641 의 "단일 `transition(gate, to_state)` 통합 금지 — 전이마다 리셋 집합이 다름" 설계 결정과 충돌하지 않게, 만능 함수가 아니라 **전이별 명명 헬퍼**로. 모든 상태 쓰기가 한 파일 한 계층을 지나면 audit 누락·필드 리셋 누락 회귀가 구조적으로 줄어든다.

**P1-C. 결합 계약의 smoke_test 고정 (C1·C2)**

C1·C2 는 절단하면 오히려 기능이 깨지는 의도된 결합이다(green-bash 리셋은 설계 철학, 스냅샷 없는 enforce 강등은 안전 장치). 절단 대신 **계약을 테스트로 고정**한다: "`_VERIFY_RE` 확장 시 자동 롤오버 시점이 변하면 fail", "`checkpoint_commit` 부재 시 enforce 강등 + 강등 사실 환기 출력 검증" 같은 행위 테스트를 smoke_test 에 추가하고, 두 지점에 상호 참조 주석을 명시.

**P1-D. lib 내부 패키지 분해 (선택 — 여유 있을 때)**

훅 등록·이벤트 배선은 그대로 두고 `plan_gate_lib.py` 만 내부 모듈로 분해: `messages.py`(순수 포맷터 ~400줄), `checkpoint.py`(이미 5-함수 인터페이스), `manifest.py`(파싱+글롭 매처), `history.py`(패치 이력/hot-file). 절단면이 이미 깨끗해 기계적 이동 가능. 단 `plan_gate.py` main 의 13단계 순서 의존 파이프라인과 단일 save 시점은 **분해 금지**(순서 회귀 이력 있음). C4(UserPromptSubmit 경합)는 실측 사고가 없으므로 문서화만 하고 관망.

> 비권장: 기능별 훅 분리, 다중 상태 파일 분할, enforcement 지점 다중화 — 외부 근거(훅 수 증가 비용, 입력 수정 훅의 비결정 병합)와 Subtraction-First 원칙 모두에 반한다.

---

## 2. 문제점 2 — verifier 단일화

### 2.1 보고 검증 결과

**사실인 부분:**

- `verifier.md` 는 단일 절차(대상 파악→정적→동적→보고)와 단일 보고 템플릿(정상/경계/에러 3항목 고정)을 모든 작업에 적용한다. **작업 유형(task type)에 따른 분기는 단 한 곳도 없다.**
- 실패 원인의 구조화 분류가 없다: 스키마의 실패 정보는 `verdict`(✅/❌ 이진)와 `issues[]`(자유 문자열)뿐. "테스트 미수행 ≠ 실패" 같은 구분이 프로즈로는 있으나 JSON 필드·게이트 상태로 전달되지 않는다.
- 하류 처리가 무차별: ❌ 면 원인 불문 동일한 D1 lock(plan_gate.py:219) + 동일한 4택 advisory(/retry·/skip·/done·/rollback, update_docs.py:359-392). 구현 결함과 환경상 실행 불가가 같은 선택지를 받는다.
- 과소/과잉 중 **과잉검증 방향이 특히 사실**: 지시 문서 변경에도 실행 검증을 동일 강도로 요구(verifier.md:23)하고, "문서만 바꿨으니 경량 검증" 프로파일은 없으며 유일한 배출구가 사용자 수동 `/skip-verify` 다.

**교정할 부분:**

- "완전 무차별"은 아니다. 이미 존재하는 부분 장치: `method` 필드(static/mocked/isolated_exec/production_exec)로 "어떻게 검증했나" 명시, 전-static ✅ 자동 ❌ 강등(update_docs.py:133-153 기계 강제), 프론트/백엔드 도메인별 명령 선택, LLM 산출물 eval 강화 규칙, TBD·실행 불가 면제 마커. 즉 **프로파일의 씨앗은 있으나 "파일 도메인" 축이지 "작업 성격" 축이 아니고, 전부 강화 방향 예외지 완화 프로파일이 없다.**

### 2.2 외부 근거

- **Anthropic 공식이 유형별 검증을 전제**: best-practices "Give Claude a check it can run: tests, a build, a screenshot to compare"(검증 신호는 산출물 유형에 따라 다름), Agent SDK 3계층 검증(rules-based → visual → LLM-judge 는 규칙으로 안 되는 것만), sub-agents "each subagent should excel at one specific task".
- **작업자·채점자 분리는 이미 준수 중**: 3-에이전트 하네스의 독립 evaluator("no Write/Edit tools… context window that never saw the build") — 현 verifier 구조와 일치. 이 축은 고칠 것 없음.
- **LLM-judge 문헌**: 일반 기준(generic rubric)은 태스크 고유 실패를 못 잡는다 → 태스크별 rubric + **실패 모드별 binary 판정**(Hamel Husain: 1-5 단일 점수는 "noisy data no one can act on") 권장.
- **애자일 관행**: 이슈 타입(스토리/버그/스파이크)별 Definition of Done 분리가 확립된 관행 — 스파이크(산출물=문서)에 기능 DoD 를 적용하지 않음.
- **CI 실패 분류(taxonomy)**: Actual Bug / Flaky / Environment 버킷 분류 자동 triage 가 확립된 관행이며, **카테고리마다 대응 전략이 달라서 분류 자체가 가치**라는 것이 공통 결론.
- **주의 — 다중 verifier 앙상블은 근거가 엇갈림**: PoLL(소형 3-judge 패널 우세) vs correlated-errors 연구(judge 간 오류 상관으로 패널 실효 무력화, 복잡 판정은 단일 대형 judge 우세). 개인 하네스에는 **에이전트 수를 늘리지 않고 단일 verifier 에 프로파일을 주입**하는 쪽이 근거상 안전하고 Subtraction-First 에도 부합.

### 2.3 진단

**업데이트 권장 — 두 문제 중 가치/위험 비가 더 좋다.** 이미 있는 장치(method·grounding 강등·도메인 분기)가 하위호환의 발판이 되고, update_docs.py 가 미지 필드를 방어적으로 무시하므로 스키마 확장이 안전하다. 방향은 "verifier 를 여러 개로 쪼개기"가 아니라 **단일 verifier + 작업 유형별 검증 프로파일 + 실패 사유 taxonomy**.

### 2.4 권장 업데이트 안 (3단계 도입)

**P2-1단계. verifier.md 스펙에 프로파일 표 도입 (스펙만, 훅 무수정)**

"1. 대상 파악" 단계에 작업 유형 판별을 추가하고 유형별 합격 기준을 표로:

| 유형 | 필수 검증 | ✅ 최소 조건 |
|------|----------|--------------|
| 버그 수정 | **수정 전 재현 → 수정 후 소멸** 확인 | 재현 시나리오 실행 1회 이상 |
| 신규 기능 | 정상 + 경계 + 에러 (현행 3항목) | 실행 grounding (현행) |
| 리팩터링 | **행위 불변** — 기존 테스트 스위트 green + diff 에 행위 변경 부재 확인 | 기존 테스트 실행 |
| 문서/설정 | 경량 — 참조 무결성·문법 검증(lint/parse). 단 **행동 지시 문서는 현행대로 행위 검증**(CLAUDE.md 원칙 유지) | 검증 도구 실행 또는 실행-불가 사유 명시 |
| 인프라 | 빌드/plan 급 dry-run (`docker build`, `terraform plan` 등) | dry-run 실행 |
| 보안 | 차단·거부 경로의 **부정 테스트**(뚫리지 않음 확인) 필수 | 부정 케이스 실행 |
| LLM 프롬프트 | eval 필수 (현행 24-25행 유지) | eval 실행 |

**P2-2단계. 스키마 확장 + update_docs.py 분기 (실질 효과의 핵심)**

- `.verifier_result.json` 에 `task_type`(위 enum)과 `failure_category` 추가: `implementation_defect`(구현 결함) / `test_gap`(테스트 부족·미수행) / `verification_limit`(검증 정책·자산 한계, 예: eval 부재) / `environment_constraint`(실행 환경 제약). — 보고서가 지적한 4분류와 1:1.
- update_docs.py 수정 2곳: ① grounding 강등(133-153)을 프로파일 조건부로(문서/설정 프로파일은 lint/parse 실행도 grounding 으로 인정), ② ❌ advisory(359-392)를 `failure_category` 별 권장 액션으로 분기 — `implementation_defect`→`/retry` 우선 안내, `environment_constraint`/`verification_limit`→"구현 문제가 아님"을 명시하고 `/skip` 계열 안내, `test_gap`→테스트 보강 후 재검증 안내. **D1 lock 등 `verifier_status` 소비자는 verdict 이진 유지로 무수정.**
- **오분류 안전장치(중요)**: verifier 의 `task_type` 자가선언을 그대로 믿으면 경량 프로파일로 빠져나가는 구멍이 된다. update_docs.py 가 `git diff --name-only` 기반으로 교차 검증: **diff 에 프로덕트 코드가 포함되면 문서/설정 프로파일 ✅ 를 인정하지 않고 grounding 강등 규칙을 그대로 적용**(기존 전-static 강등과 같은 방식의 기계 강제).

**P2-3단계 (선택). gate 에 task_type 저장 → 환기 강도 조절**

`make_gate` 에 필드 추가 + `/approve-plan` 시 todo.md 매니페스트에서 유형 선언을 파싱해 세팅 → 문서 작업 게이트는 `verifier_remind`/`stop_alert` 를 억제. 효과 대비 수정 범위(게이트 스키마+훅 3개)가 커서 1·2단계 운용 후 과잉 환기가 실측될 때만.

**공통 유의사항:**

- verifier.md 는 템플릿이라 기존 프로젝트는 `/harness-update` 전까지 미반영 — 릴리스 노트에 명시 필요.
- `docs/.verifier_result.json` 경로는 control-plane allowlist 에 있으므로 경로 변경 금지.
- 동반 갱신: 템플릿 workflow.md 검증 규칙, MANUAL §6 스키마 표, smoke_test(강등 조건부·advisory 분기 행위 검증 — grounding 강제는 이미 스모크 대상이라 케이스 추가).
- CLAUDE.md 원칙대로 에이전트 스펙 변경은 **행위 검증**(변경된 verifier 지시를 따르는 서브에이전트를 실제 띄워 프로파일 분기 확인) 없이 통과 판정 금지.

---

## 3. 실행 순서 제안 (종합)

| 순서 | 항목 | 근거 |
|------|------|------|
| 1 | P2-1·2 (verifier 프로파일 + failure taxonomy + advisory 분기) | 두 보고 중 실사용 가치 최대(과잉검증 완화 + 실패 원인 즉시 판별), 하위호환 안전 |
| 2 | P1-A (에러 코드 태깅) | 문제점 1 의 사용자 체감(디버깅 어려움)을 최소 위험으로 직접 해소 |
| 3 | P1-B·C (전이 쓰기 수렴 + 결합 계약 테스트) | 회귀 위험의 실제 원천을 구조적으로 축소 |
| 4 | P1-D·P2-3 (lib 패키지 분해 / task_type 게이트 저장) | 선택 — 위 운용 후 필요가 실측될 때만 (Subtraction-First) |

**하지 말 것**: 기능별 훅/시스템 분리, 다중 verifier 에이전트, 상태 파일 분할 — 외부 근거·훅 시스템 특성(비결정 병합, hot-path 비용)·이 저장소의 Simplicity First 원칙 모두에 반한다.

---

### 부록 — 참조 출처

- Anthropic 공식: code.claude.com/docs/en/best-practices · sub-agents · hooks-guide, github.com/anthropics/cwc-long-running-agents, "Harness design for long-running application development"(3-에이전트 분리, InfoQ 요약 경유)
- 커뮤니티 훅 컬렉션: disler/claude-code-hooks-mastery, karanb192/claude-code-hooks (모두 관심사당 1스크립트 + 공용 lib)
- LLM 검증: PoLL(arXiv:2404.18796) vs correlated-errors(arXiv:2605.29800), Hamel Husain evals FAQ(실패 모드별 binary 판정), Label Studio·Galtea rubric 가이드
- 일반론: OPA 정책 분리 패턴, CI 실패 분류 연구(arXiv:2501.04976, arXiv:2302.10594), Atlassian/Scrum.org 이슈 타입별 DoD
- 내부 근거: complit/20260511(phantom deletion), complit/20260703(spurious /done), complit/260616(guard divergence), plan_gate.py:253-257 순서 회귀 주석
