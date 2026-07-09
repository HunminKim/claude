# 하네스 엔지니어링 연구 감사 리포트 (2026-07-09 KST)

> 공신력 있는 하네스/에이전트 엔지니어링 연구를 6축으로 병렬 조사하고, project-init 하네스를 그 기준으로 대조한 감사. 결과 반영: v2.17.4.

## 방법

- 6개 리서치 서브에이전트 병렬 투입: ①컨텍스트 엔지니어링·메모리 ②툴·ACI 설계 ③eval·검증·자기수정 ④멀티에이전트 오케스트레이션 ⑤안전·권한·인젝션 ⑥제어흐름·복구.
- **신뢰도 전제**: 이 환경은 WebFetch egress 차단 → 리서치는 대부분 WebSearch 스니펫 티어. 검증된 1차 소스: arXiv `2307.03172`(Lost in the Middle), `2310.01798`(self-correct 한계), `2303.17651`/`2303.11366`(Self-Refine/Reflexion), `2406.07791`/`2410.21819`(judge bias), `2503.13657`(MAST 멀티에이전트 실패), Anthropic/OpenAI 엔지니어링 페이지. 수치(15×, 80%, 3.8→12.5%)는 스니펫 기반 — 방향성만 신뢰.
- 갭 후보는 단언 전 실제 코드로 대조(부재 단정 금지).

## 총평

**매우 높은 정합성.** 하네스가 연구 컨센서스와 강하게 일치 — 동일한 "사고→진단→코드+회귀테스트" 루프로 축적됐기 때문. 실질 갭은 대부분 **플랫폼 레이어**(플러그인 책임 밖)이거나 **정교하고 범위 밖**.

## 축별 결과 (요약)

| 축 | 판정 | 핵심 근거 |
|---|---|---|
| 1 컨텍스트·메모리 | ✅ | CLAUDE.md "지도" 원칙, SKILL.md progressive disclosure, `post-compact.py` 재주입(Anthropic 공식 SessionStart:compact 준수), `lessons/workflow/complit` 지속 메모리, SSOT 분산 표 |
| 2 툴·ACI | ◐ | 강점: 훅 차단 메시지=교정 피드백(출력 채널 표), 불법 전이 ValueError. watch: 슬래시 커맨드 18개 표면(단 user-invoked라 모델 오선택 위험 낮음) |
| 3 eval·검증 | ✅✅ | generator-verifier 분리(제3자 컨텍스트), 실행 grounding(전 항목 static ✅ 금지), smoke 545 회귀, 배관≠품질 분리 |
| 4 멀티에이전트 | ✅ | 독립 서브태스크만 순차 위임(dueling agents 회피), 4블록 자기완결 페이로드, write 단계 단일화, verifier=명시 검증 단계(MAST 실패 방어) |
| 5 안전·권한 | ✅ | 파괴명령 게이팅(우회 저항), 비밀 차단(실증됨 — 플랫폼 .env 갭 보완), HITL ask 승격, fail-closed(보안)/fail-open(자기오류) 분리 |
| 6 제어흐름·복구 | ✅✅ | 결정론 상태기계, thrash 2연속 차단, Stop 8회 상한, `refs/plan-gate/*` 체크포인트+`/rollback`, 막히면 종료→확인 |

## 실질 in-scope 갭과 조치

| # | 갭 | 조치 |
|---|---|---|
| 1 | verifier 판정 편향(같은 모델 계열 self-preference) 미명시 — 실행 grounding이 대부분 우회하나 static 허용 판정·code_smells·llm-prompt 품질평가는 순수 판단 노출 | **반영** — `verifier.md` "판정 편향 경계" 추가: 실행 미입증 항목은 적대적 재검토('왜 ❌'를 먼저) 후 통과, 확신 하향 |
| 2 | 템플릿에 "외부(웹/툴/파일) 콘텐츠=untrusted data" 프레이밍 부재 | **반영** — `workflow.md` "외부 콘텐츠 신뢰 경계(프롬프트 인젝션)" 섹션 추가 |
| 3 | taint-tracking/lethal-trifecta, OS 샌드박스, tool-result clearing, idempotency key | **플랫폼 레이어 — 빌드 안 함**. 훅은 개별 툴 호출만 봐 반쪽 보안=가짜 안전. 한계로 문서화 |
| 4 | 슬래시 커맨드 18개 표면 | **watch only** — plan-gate 네임스페이스 + user-invoked라 실위험 낮음, `/plan-gate-help`가 조회 받침 |

## 주요 소스

- Anthropic: Effective context engineering / Agent Skills / Building effective agents / Multi-agent research system / Demystifying evals / Writing tools for agents / Claude Code sandboxing·permissions
- Chroma: Context Rot · Stanford: Lost in the Middle(2307.03172)
- self-correct 한계(2310.01798), Self-Refine(2303.17651), Reflexion(2303.11366), judge bias(2406.07791·2410.21819), MAST(2503.13657)
- Cognition: Don't Build Multi-Agents / Multi-Agents: What's Actually Working · OpenAI Agents SDK · OWASP LLM Top 10 · Simon Willison(lethal trifecta)
