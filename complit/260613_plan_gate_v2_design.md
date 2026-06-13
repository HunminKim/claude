# plan-gate v2 상세 설계서 — 스코프 강제 계약 모델 (rev.2)

> **상태**: 설계 확정 (구현 전). 비판 리뷰 2회차(260613) 반영 개정판.
> **작성 근거**: harness-check 비판 리뷰(260612~260613) → 구조 결함 식별 → claude-code-guide 토론 2회 + 하네스 엔지니어링 연구 5각(SOTA 가드레일 / 경쟁 도구 / 자기승인 효과성 / 중첩 분해 / 스펙 주도) → 사용자 설계 결정 → **설계서 자체 적대적 재검증(연구 인용 / 플랫폼 메커니즘 / 설계 레드팀) → rev.2 개정**.
> **브랜치**: `claude/critical-code-review-5gcym9`
> **버전 목표**: v1.40.x(토대) → v2.0.0(평면 스코프 + 종료 verifier 리뷰). 중첩·fcntl 은 보류.
> **이 문서가 다루지 않는 것**: 코드. git 명령·함수명은 *설계 의도를 고정*하기 위한 의사코드 수준이며, 구현 세션에서 행위 검증과 함께 확정한다.

---

## rev.2 변경 이력 (비판 리뷰가 바꾼 것)

설계서를 그대로 믿지 않고 근거를 적대적으로 재검증한 결과, 다음이 바뀌었다:

- **[D4 재설계]** "훅/CLI 가 verifier 서브에이전트를 동기 호출해 detour 를 강제"는 **구현 불가**로 확정(훅은 subprocess 라 서브에이전트를 못 부름). → verifier 를 **v1 방식(Claude 가 호출, 결과는 파일, 훅이 읽음)** 으로 되살려 **게이트 종료(/done) 시 완성품 리뷰**에 쓴다. detour 자체는 verifier 없이 매니페스트 수정으로 처리.
- **[§1 정직화]** 매니페스트를 Claude 가 쓰므로 이 설계가 강제하는 것은 "anti-hallucination 0"이 아니라 **"매니페스트-편집 일관성 + 계획 외 변경의 명시·검토·되돌림"**. 목표 문구 수정.
- **[D3 강제층 보정]** PreToolUse `deny`/exit 2 는 Edit/Write 를 **안정적으로 막지 못함**(열린 이슈 #13744/#37210) + Bash 우회(#29709/#31292). → 1층은 "빠른 차단·advisory 강도", **2층 PostToolUse(Bash) 스윕+롤백을 실제 강제 경계로 승격**. Stop 은 경고만(파일 변경 금지).
- **[D6 글롭 가드]** 초기 매니페스트의 넓은 글롭(`**` 등)이 전체 우회 탈출구 → 넓은 글롭은 자동승인 비활성, 사람 승인 강제.
- **[D7 롤백 교정]** `read-tree`+`checkout-index` 와 `git diff` 둘 다 untracked 신규 파일을 못 지움(실측). → 롤백 대상 *집합*은 **touched-file 매니페스트**(`{경로: 편집전존재여부}`, 기존 cp 방식)로 구동, git 스냅샷은 *내용 복원*에만. git/비-git 백엔드 통합.
- **[중첩·fcntl 보류]** 중첩 스택은 실증된 필요 없음 → v2 범위 제외. fcntl 은 단일 에이전트엔 과함 → 보류(atomic-rename 유지, 멀티세션 실증 시 재도입).
- **[인용 수정]** CTRL "+106%" → 검증 가능한 "~+49% pass@1(7.88%→11.76%)". "Scope Guard 35%→4%" 삭제(확인 불가). agent-guardrails 플래그명·PRM 귀속 약화. `arXiv:2605.10039`(준수율 연구)는 미검증 프리프린트라 "최근 프리프린트가 시사" 수준으로 격하. Google/MIT 스케일링은 arXiv:2512.08296 으로 ID 확정.

---

## 0. 이 설계가 필요한 이유 (배경)

운영 중 다수의 버그를 처리했으나 **구조적 결함은 증상 패치로 해소되지 않았다.** 비판 리뷰에서 행위 검증으로 확정한 치명 결함:

- **C1. git 모드 체크포인트가 출시 이후 한 번도 작동한 적 없음.** `TAG_PREFIX = ".claude/gate/"` (plan_gate_lib.py:65) 는 git refname 규칙 위반(경로 컴포넌트가 `.`으로 시작 불가 — git-check-ref-format 으로 재확인) → `git tag` rc=128 영구 실패 → git 프로젝트에서 `/rollback` 전면 불능인데 안내문은 "안전하게 되돌릴 수 있습니다"라고 약속.
- **C2. `/done`·`/skip` 의 stash pop 실패 시 drop → 사용자 편집 무음 영구 유실.** `do_gate_done` (plan_gate_lib.py:830-838). `git fsck` dangling 으로만 복구. 재현 완료.
- **C3. 검증 체계가 기본 경로를 안 봄.** 스모크 184개가 git tag 경로를 행위 검증하지 않아 C1·C2가 모든 릴리스를 통과. signing 환경에선 suite 전체가 0개 검증으로 즉사.

근본 원인은 **트리거 축이 잘못 선택된 것**이다. 현행 plan-gate 는 편집 *양*(같은 파일 5회 반복, `TRIGGER_REPEAT_RATIO`)으로 작동한다 — "계획되지 않은 파일 수정"이라는 실제 통증을 간접 프록시로만 잡아 오탐·dead 분기·카운터 오염을 양산했다.

**v2 의 전환**: 트리거 축을 *편집량* → *편집범위*로. 계획이 건드릴 파일 집합(매니페스트)을 선언하고, 그 밖의 변경을 기계적으로 차단·탐지·되돌린다.

---

## 1. 설계 목표 / 비목표

### 목표 (rev.2 정직화)
1. **계획되지 않은 파일의 *조용한* 수정을 막는다.** 스코프 밖 변경은 (a) 차단되거나 (b) 차단 우회 시 사후 탐지·롤백되어, **명시·검토·되돌림 없이는 살아남지 못한다.**
2. **계획을 advisory(조언)에서 enforced(기계 강제) 계약으로 승격한다** — 단, 강제 대상은 "매니페스트와 실제 변경의 일관성"이다.
3. **체크포인트·롤백을 실제로 작동시킨다** (C1/C2 근본 해소).
4. **버그 표면을 줄인다** — 퍼지 휴리스틱을 crisp한 멤버십 검사로 대체(Subtraction-First).

### 명시적 비목표 / 한계 (정직)
- **"할루시네이션 0"이 아니다.** 매니페스트는 Claude 가 쓴다 → Claude 가 d.py 를 매니페스트에 넣고 진행하면 막을 수 없다. 이 설계가 보장하는 건 "선언되지 않은 변경이 *조용히* 통과하지 못함"이지 "선언이 옳음"이 아니다. **선언의 옳음은 사람(/done 비준)과 종료 verifier 리뷰가 검증한다.**
- OS 수준 완전 샌드박스(자기 가드 변조·임의 subprocess는 부분 완화만).
- 병렬 멀티에이전트 편집(연구상 코딩에 부적합 — §2 D5).
- **중첩 게이트는 v2 범위가 아니다**(보류 — §2 D5).

---

## 2. 핵심 설계 결정 + 선정 이유

### D1. 트리거 축: 편집범위(스코프) — 편집량 아님

- **결정**: 매니페스트 선언 파일 집합 밖의 변경을 차단/탐지한다. 편집 횟수 카운팅 폐기.
- **대안**: (a) 현행 편집량 유지·보정, (b) 양·범위 혼합.
- **선정 이유**:
  - 실제 통증("계획 외 파일 수정")은 *양*이 아니라 *범위* 문제다(비판 리뷰 B-1, ST-2).
  - 범위 모델은 `TRIGGER_REPEAT_RATIO`·`_max_code_repeat`·scope-creep 산식·soft-hint·오버라이드 마커·`is_doc_path`(절대경로 dead 버그)를 *삭제*하고 단일 멤버십 검사로 대체 — 버그 표면 축소.
  - 업계 검증: 진짜 강제 도구는 전부 범위 기반 — **RooCode `fileRegex`(런타임 차단, FileRestrictionError — CONFIRMED)**, Aider `/add` 편집집합. 양 기반 강제 도구는 없음.

### D2. 강제 위치: 도구 계층 훅 — 프롬프트/모드플래그 아님

- **결정**: 매니페스트 검사를 PreToolUse/PostToolUse 훅에서 한다.
- **대안**: (a) CLAUDE.md/매니페스트 컨텍스트 주입(advisory), (b) 네이티브 Plan Mode.
- **선정 이유**:
  - 지시 파일 준수는 신뢰 불가 — 공식 문서 "권한은 모델이 아니라 하네스가 강제", dev.to 분석 "Plan Mode 는 시스템 프롬프트 문자열 하나로 사소하게 우회"(CONFIRMED). (최근 프리프린트 arXiv:2605.10039 도 구조 조정으로 준수 개선 없음을 *시사* — 단 미검증 단일저자 프리프린트라 보조 근거로만.)
  - 네이티브 Plan Mode 는 승인 계획을 훅에 노출하지 않고, 모든 편집을 막아 너무 거침 → 매니페스트 대체 불가.
  - 모드 플래그는 누수 — Cline Plan 모드 편집 버그 다수.
  - **단, "PreToolUse 가 곧 하드 차단"은 아니다 — D3 참조.**

### D3. 계층형 강제: 1층 빠른 차단 + 2층 사후 스윕·롤백(진짜 강제) + 3층 체크포인트

- **결정**: ①PreToolUse(Edit|Write|MultiEdit) deny — 빠른 차단·advisory 강도. ②**PostToolUse(Bash + Edit|Write) git-status 스윕 + 롤백 — 실제 강제 경계.** ③프라이빗 ref 체크포인트 — 롤백 내용 출처. Stop 은 경고만(파일 변경 금지).
- **대안**: PreToolUse 단일 차단 의존(rev.1 안).
- **선정 이유 (rev.2 핵심 보정)**:
  - PreToolUse `deny`/exit 2 가 **Edit/Write 를 안정적으로 막지 못함**: 열린 이슈 #13744(exit 2 무시), #37210(deny 무시) — 둘 다 OPEN. Bash 쓰기(`echo>`, `sed -i`, 스크립트)는 Edit 매처를 아예 안 탐(#29709/#31292, 공식 문서 "Read/Edit 규칙은 임의 subprocess 미적용").
  - 따라서 1층을 "보안 경계"로 둘 수 없다. **2층(턴 종료/도구 직후 working tree 전체를 매니페스트와 대조 → 스코프 밖 변경 롤백)이 모든 쓰기 경로를 잡는 실제 강제.**
  - 훅은 차단은 못 해도 **사후 롤백은 가능**(프로세스라 `git checkout`/`rm` 직접 실행 가능). 이게 2층을 성립시킨다.
  - **Stop 이 아니라 PostToolUse(Bash) 에서 롤백**: Stop 시점엔 턴이 끝나가 Claude 의 파일 인식을 갱신 못 함 → 다음 턴 desync. PostToolUse 는 같은 턴 즉시 → 롤백 후 환기를 Claude 가 인지(레드팀 H-D 반영).
  - 빌드 게이트: **타깃 CLI 버전에서 1층 효능을 반드시 실측**. 작동하면 빠른 차단 보너스, 안 해도 2층이 안전 보장.

### D4. 스코프 확장 검증: detour=매니페스트 수정(자율) + /done 시 v1식 verifier 리뷰 + 사람 일괄 비준

- **결정**:
  - **작업 중 detour(계획 외 파일 필요)**: verifier 안 부름. Claude 가 매니페스트에 "파일+이유" 추가 → 자율 진행(1/2층이 일관성 강제). 모든 detour 는 `gate["expansions"]` 에 기록.
  - **게이트 종료(/done)**: Claude 가 **v1 패턴 그대로** `@verifier` 호출 → verifier(별개 Claude, **새 컨텍스트**)가 완성된 *전체 변경*을 검토(diff↔매니페스트 일치, detour 정당성, 작동 여부) → 결과를 `docs/.verifier_result.json` 기록 → 훅이 읽어 ❌면 /done 차단(v1 검증 메커니즘).
  - **최종**: 사람이 모든 expansions + diff 를 /done 시 일괄 비준(최종 ground truth).
- **대안**: (a) 훅/CLI 가 detour 마다 verifier 동기 호출(rev.1 안), (b) Claude 자기승인, (c) 매 detour 사람 승인.
- **선정 이유 (rev.2 재설계)**:
  - **(a) 구현 불가**: 훅은 subprocess — Agent 도구(서브에이전트)를 호출·블록할 수 없다. 서브에이전트는 *메인 모델*만 부른다. "코드로 강제"는 거짓이었다(레드팀 C-A).
  - **(b) 학술적으로 무효**: 같은 컨텍스트 자기교정은 맹점 64.5%(Self-Correction Bench, 비추론모델 14종)·자기선호(NeurIPS'24)·아첨(FlipFlop 46%/17%)·"오라클 없이 불가"(Huang ICLR'24)·"프롬프트-LLM 피드백 성공 사례 없음"(Kamoi TACL'24).
  - **v1 verifier 가 (b) 와 다른 이유 + 채택 근거**: v1 verifier 는 *자기 컨텍스트 재독*이 아니라 **새 컨텍스트의 별개 인스턴스가 결과물(diff)을 외부 관점으로 검토**한다. 위 연구의 "외부가 지적하면 고친다"(맹점 89% 감소) 케이스에 해당하며, v1 에서 *실제로 실행·검사해 버그를 잡은* 실적이 있다. 별도 critic·PRM 이 자기비평을 능가한다는 근거(CTRL ~+49% pass@1 7.88%→11.76%; PRM SWE-bench 40.0→50.6%)와도 방향 일치.
  - **단 정직하게**: verifier 는 동족 모델이라 완벽한 오라클은 아니다. 그래서 **"투기적 사전승인(detour 마다 '이 파일 필요?')"엔 안 쓰고**(근거 빈약 + 고무도장 위험), **"완성품 리뷰"라는 강점에만** 쓴다. 강제력은 "❌면 /done 차단"(실권 있음), 최종 방어는 사람.
  - **(c) 기각**: detour 빈도가 높아(20파일 작업에 8회 등) 자율성 파괴. 단 사람 비준을 *종료 시 배치*로 유지해 최종 검증은 보존.

### D5. 중첩: v2 범위 제외(보류) — 단일 평면 게이트, 단일 에이전트 원칙 유지

- **결정**: 중첩 게이트 스택은 v2 에 넣지 않는다. 단일 평면 게이트로 시작. (단일 에이전트·단일 컨텍스트 원칙은 유지.)
- **대안**: rev.1 의 v2.1 중첩 스택.
- **선정 이유**:
  - 중첩의 실증된 필요가 없다 — 코딩은 단일 스레드가 유리(Anthropic "코딩 멀티에이전트 부적합", Google/MIT arXiv:2512.08296 순차작업 39~70% 저하, Cognition 단일 스레드). "중첩 스코프 상속"은 아무도 깔끔히 못 푼 영역(opencode #6527/Cline 탈출 버그).
  - 레드팀: 중첩 스택은 "고복잡·저실증 부품" — 80% 설계에서 컷.
  - **재도입 조건**: 평면 스코프 운영에서 "큰 작업 안의 독립 detour 를 별도 롤백해야 하는" 실제 통증이 반복 관측되면 그때 스택을 설계한다(사용자 원안의 가치는 보존하되 증거 기반으로).

### D6. 매니페스트 강도: 명시 화이트리스트 + 글롭 허용 + 넓은 글롭 가드

- **결정**: 파일 명시 나열 + `src/auth/**` 글롭 허용, 화이트리스트 밖은 default-deny. **넓은 글롭(`**`, 최상위 단일 글롭 등 넓이 임계 초과)은 자동승인 비활성 → 사람 `/approve-plan` 강제.**
- **대안**: (a) 엄격(글롭 금지), (b) 관대(디렉토리 단위), (c) rev.1(글롭 허용, 넓이 무제한).
- **선정 이유**:
  - (a) 리팩터/리네임에서 detour 폭증, (b) 차단 느슨 → 둘 다 기각.
  - **(c) 의 구멍(레드팀 H-B)**: `src/**` 를 선언하면 `validate_todo_quality`(넓이 미검사) 통과 후 자동승인 → 전체 우회. detour 마찰이 오히려 넓은 매니페스트를 합리적 선택으로 만듦. → **넓은 글롭은 자동승인에서 배제**해 사람 눈을 강제(메인 게이트는 어차피 사용자 승인 대상).
  - detour 단독 글롭은 여전히 금지(자기 전체스코프 부여 차단).
  - 매니페스트는 버전관리·런 중 sha 고정.

### D7. 체크포인트: 프라이빗 ref 스냅샷(내용) + touched-file 매니페스트(롤백 집합)

- **결정**: 게이트 열 때 `commit-tree` 로 `refs/plan-gate/<gate_id>/checkpoint-0` 에 비파괴 스냅샷(내용 출처). **롤백 대상 파일 집합은 게이트별 touched-file 매니페스트 `{경로: 편집전존재여부}` 가 구동**: 편집전 존재→스냅샷에서 내용 복원, 부재→`rm`. 비-git 은 기존 cp 디렉토리. **git/비-git 백엔드를 하나의 롤백 모델로 통합.**
- **대안**: (a) tag+stash refname 만 수정, (b) `read-tree`+`checkout-index`(rev.1 §3.2), (c) worktree.
- **선정 이유**:
  - (a) refname 고쳐도 stash pop 충돌→유실(C2)·dirty diff 순서 역전 잔존.
  - **(b) 실측 결함 확정**: `read-tree`+`checkout-index` 는 tracked 만 복원, 스냅샷 *이후* 생성된 파일을 안 지움 → 할루시네이션 파일 잔존. `git diff <snap>` 도 untracked 신규를 못 봄. **git 의 index 기반 연산은 "untracked 신규 생성"을 근본적으로 못 추적** → 명시적 touched-file 매니페스트가 필수(기존 cp 백엔드가 이미 올바르게 하는 방식).
  - **`git clean -fd` 금지**: 무관한 untracked(사용자 scratch 파일)까지 삭제 → 너무 공격적. touched-file 매니페스트만 surgical 하게 처리(실측: 무관 untracked 보존 확인).
  - (c) worktree 는 병렬용 — 단일 working tree 순차엔 과중.
  - **미해결 주의(구현 시 처리)**: rename(A→B), symlink, 파일 모드 변경, 디렉토리↔파일 전환은 단순 `{경로:존재}` 로 부족 → touched 매니페스트에 타입/모드도 기록하거나 git status 의 rename 탐지 병용.

### D8. 동시성: 보류 (atomic-rename 유지, fcntl 은 멀티세션 실증 시)

- **결정**: fcntl 락을 v2 에 넣지 않는다. 현행 atomic-rename(plan_gate_lib.py:160-162) 유지. 상태 조작을 단일 `transition()` 으로 모으는 것만 한다.
- **대안**: rev.1 의 fcntl(+msvcrt) 락.
- **선정 이유**:
  - 이 설계는 "단일 에이전트·단일 working tree"가 전제(D5) — 락이 막는 동시 쓰기는 *동시 세션*에서만 발생하는 narrow 케이스. atomic-rename 가 파일 깨짐은 이미 막음(lost-update 만 잔존).
  - fcntl 은 **NFS/SMB 에서 무효/위험**(flock↔fcntl 변환, SMB 미지원) — 추가 함정.
  - 레드팀 M-C: "단일 에이전트 thesis 의 설계에 다중사용자 락 추가는 복잡도 낭비." → 보류, 멀티세션 lost-update 가 실제 관측되면 재도입.

---

## 3. 아키텍처 상세

### 3.1 매니페스트 (계약 아티팩트)

위치: `tasks/todo.md` 내 기계가독 블록. 승인 시 1회 파싱 → `gate["scope"]`·`gate["do_not_touch"]` 저장 + 원문 sha256 고정(TOCTOU).

```
<!-- plan-gate: scope -->
src/auth/**
src/models/user.py
tests/test_auth.py
<!-- plan-gate: do-not-touch -->
src/payment/**
```

규칙:
- 매칭은 **루트 상대경로 정규화** 기준 `fnmatch`(절대경로 미스매치 버그 반복 금지 — 기존 `is_doc_path`/`verifier_remind` 가 이 실수).
- `do-not-touch` > `scope`(deny-first). do-not-touch 는 detour 로도 못 품.
- `.plan-gateignore`(생성물·락파일)는 스코프 검사 우회.
- 매니페스트(`tasks/todo.md`)·`.claude/state/`·훅 디렉토리도 스코프 검사 대상(자기 변조 부분 차단, #11226). **단 한계 명시**: 매니페스트가 `todo.md` 안에 있어 그 파일은 편집 가능해야 함 → 이 보호는 완전하지 않음(레드팀 M-B). 매니페스트 수정은 재승인을 유발(sha 변경)하도록 처리.
- **넓은 글롭 가드(D6)**: 스코프 블록의 글롭 넓이가 임계 초과면 자동승인 비활성 → 사람 `/approve-plan` 필요.

### 3.2 강제 3층 (상세, rev.2)

**1층 — PreToolUse(Edit|Write|MultiEdit) — 빠른 차단(advisory 강도)**
- target 정규화 → do_not_touch 매칭 deny → scope 매칭 allow → 아니면 deny.
- deny 메시지(RooCode UX): 허용 경로 명시 + detour CLI 한 줄 복붙 가능하게. `permissionDecision:deny`(JSON) 사용.
- **이 층은 신뢰 경계가 아님**(#13744/#37210). Bash 쓰기는 애초에 안 탐. → 빠른 1차·모델 유도용.

**2층 — PostToolUse(Bash + Edit|Write) — 사후 스윕·롤백(실제 강제)**
- `git status --porcelain`(untracked `??` 포함)으로 working tree 전체를 매니페스트와 대조.
- 스코프 밖 변경(Edit 누수든 Bash 든) 발견 시: **touched-file 매니페스트 기반으로 해당 파일만 롤백**(D7) + Claude 에 환기("<파일> 스코프 밖이라 되돌림").
- **PostToolUse 라 같은 턴 즉시** → Claude 가 롤백을 인지하고 다음 행동 보정(Stop desync 회피).

**3층 — 체크포인트(프라이빗 ref + touched 매니페스트)**
- 열기: `git add -A` 후 `write-tree`→`commit-tree -p HEAD`→`update-ref refs/plan-gate/<id>/checkpoint-0`. dirty 도 그대로 캡처(C2/dirty-diff 해소).
- 롤백: touched-file 매니페스트 순회 — 존재했던 파일 `git checkout <snap> -- <path>`(또는 cp 복원), 신규 파일 `rm`. 무관 untracked 보존.
- 닫기: `update-ref -d`(커밋 객체는 reflog/fsck 로 잔존).
- 비-git: 기존 `cp_snapshot_file`/`cp_rollback` 재사용(동일 매니페스트 모델).

### 3.3 상태 모델 / 상태 기계 (평면)

```
state = {
  "schema_version": 2,
  "current_gate_id": "<id>",          # 평면 — 스택 아님
  "gates": {
    "<id>": {
      "id", "state",                   # created|approved|verified|done|rolled_back
      "scope": [...], "do_not_touch": [...],
      "manifest_sha256",
      "checkpoint_commit",             # refs/plan-gate/<id>/checkpoint-0 SHA
      "touched": {"<relpath>": existed_before_bool},  # 롤백 구동(D7)
      "expansions": [{"file","reason","ts"}],          # detour 기록(/done 시 사람 비준)
      "verifier_status",               # /done 시 v1 verifier 결과
      "created_at","approved_at","closed_at"
    }
  }
}
```

- 합법 전이는 `plan_gate_lib.transition(gate, to_state)` **단일 함수**로 강제(필드 리셋 일괄, 불법 전이 거부). 5곳 분산 조작 폐기.
- **상태기계 갭 처리(레드팀 M-A)**: ①verifier ❌ 시 — 전체 게이트 락 vs 파일 단위? → **명시: /done 단계의 ❌는 v1 처럼 사용자 토큰(/retry·/skip·/done) 대기**(detour 마다가 아님 — detour 엔 verifier 없음). ②매니페스트 sha 순환 — todo.md 수정 시 재승인 필요(의도된 동작). ③세션 횡단 잔류 — 기존 advisory 유지. ④GC된 체크포인트 — /rollback 시 "체크포인트 없음" 명시 거부 + cp 폴백 시도(조용한 no-op 금지).

### 3.4 detour 흐름 (rev.2 — verifier 없는 자율 경로)

```
[1층 차단] Claude 가 스코프 밖 d.py 편집 시도 → deny + "subplan 으로 추가하라" 안내
   ↓
[신청] Claude: Bash 로 plan_gate_cli.py subplan d.py "이유"
   ↓
[가드] CLI: 글롭단독 거부 / do-not-touch 침범 거부 / 루트밖·".." 거부
   ↓
[기록] gate["expansions"] 에 {d.py, 이유} 추가, scope 에 d.py 편입 (verifier 호출 없음)
   ↓
[재시도] Claude 가 d.py 편집 → 1층 통과 (2층이 일관성 계속 감시)
   ↓
[/done] Claude 가 @verifier 호출 → 완성품 전체 리뷰 → ❌면 /done 차단
   ↓
[사람] 모든 expansions + diff 일괄 비준 (최종 ground truth)
```

- 메인 토큰(approve/replan/done): `disable-model-invocation:true`(사용자 전용).
- subplan: Claude 가 Bash CLI 호출 가능. **자기승인 위험은 "verifier 가 막는다"가 아니라 "①2층이 일관성 강제 ②/done verifier 가 완성품 리뷰 ③사람이 일괄 비준"의 3중 사후 검증으로 관리**(정직: detour 시점엔 강한 외부 게이트가 없음 — 빈도·자율성 때문에 의도적 선택, 사후 검증으로 상쇄).

### 3.5 삭제 대상 (Subtraction-First)

제거: `TRIGGER_REPEAT_RATIO`·`MAX_EDIT_OVERRIDE`·`_OVERRIDE_RE`·`parse_gate_overrides`·`_threshold_for`·`post_approval_limit_exceeded`·`post_approval_stats`·`_max_code_repeat`·`_unique_code_files`·`trigger_threshold_exceeded`·`format_soft_hint`·`format_scope_creep_message`·`is_doc_path`+`_DOC_*`·오버라이드 마커 안내. 관련 스모크 → 스코프 멤버십 테스트로 교체.
유지: hot-file 패치이력(범위와 직교) — 단 절대경로 버그 수정.

---

## 4. 단계적 릴리스 계획

### v1.40.x — 토대 (major 아님, 즉시)
1. 체크포인트 tag → 프라이빗 ref + **touched-file 매니페스트 롤백**(D7) — git 롤백 *처음으로* 작동, 신규파일 삭제 포함.
2. stash drop 폴백 제거(C2).
3. 스모크 **git 백엔드 행위 테스트 신설**(트리거→체크포인트→rollback 복원[신규파일 삭제 포함]→done 정리) + `GIT_CONFIG_GLOBAL=/dev/null` 격리(C3).
4. 보안 우회 차단: `rm -rf /*`/`~`/`-fr ~/`/`bash -c '...'`, Grep 디렉토리·glob, 두 훅 시크릿 정책 lib 통합.
5. **누락 태그 푸시**: v1.38.0·v1.39.0.

### v2.0.0 — 평면 스코프 + 종료 verifier 리뷰
1. 매니페스트 파싱·sha 고정·넓은 글롭 가드(3.1).
2. 강제 3층(3.2) — 1층 deny + **2층 PostToolUse(Bash) 스윕·롤백(실제 강제)**.
3. detour = 매니페스트 수정(3.4), /done 시 v1식 verifier 리뷰 + 사람 일괄 비준.
4. 상태 전이 중앙화(`transition`) + 상태기계 갭 처리(3.3).
5. 편집량 휴리스틱 일괄 삭제(3.5).
6. **빌드 게이트: 타깃 CLI 버전에서 1층 deny 효능 실측.**
7. 행위 검증: 스코프 밖 차단/2층 롤백(Bash 우회 포함)/넓은 글롭 자동승인 차단/매니페스트 sha 가드/verifier ❌ /done 차단.

### (보류) 중첩 스택 / fcntl
- 평면 운영에서 실제 통증이 반복 관측되면 증거 기반으로 재설계(D5/D8).

---

## 5. 남은 위험 & 빌드 시 검증 항목

- **★1층 deny 효능을 타깃 CLI 버전에서 실측**(빌드 게이트). #13744/#37210 OPEN — 1층이 조용히 실패하면 2층이 유일 강제. 2층 git 스윕·롤백에 버그 없도록 행위 테스트 필수.
- **§1 한계 직시**: 매니페스트를 Claude 가 쓰므로 "선언의 옳음"은 사람·종료 verifier 가 검증. "할루시네이션 0" 주장 금지.
- **자기 가드 변조(#11226)**: 매니페스트가 todo.md 안 → 보호 불완전. Bash `sed -i` 로 state/훅 수정 가능. OS 샌드박스(비목표) 없이는 부분 완화.
- **detour 빈도**: 잦으면 매니페스트 부실 신호 → 계획 단계 보강. 넓은 글롭으로 회피하려는 유인은 D6 가드로 차단.
- **D7 엣지**: rename/symlink/mode 변경/디렉토리↔파일 — touched 매니페스트에 타입 기록 또는 git rename 탐지 병용.
- **verifier 한계**: 동족 모델 — 완벽 오라클 아님. 완성품 리뷰에만 쓰고 사람 비준 병행.
- **fcntl NFS/SMB**: 보류했으나 재도입 시 네트워크 FS 함정 주의.

---

## 부록 A. 연구 근거 요약 (rev.2 — 재검증 반영)

| 주제 | 핵심 결과 | 출처 | 검증 | 반영 |
|---|---|---|---|---|
| 자기교정 맹점 | 64.5%(비추론모델 14종) | Self-Correction Bench, arXiv 2507.02778 | CONFIRMED | D4 |
| 자기교정 불가 | 오라클 없이 불가 | Huang ICLR'24, 2310.01798 | CONFIRMED | D4 |
| 아첨/뒤집기 | 46% flip / 17%↓ | FlipFlop, 2311.08596 | CONFIRMED | D4 |
| 자기선호 | self-recognition 비례 | NeurIPS'24, 2404.13076 | CONFIRMED | D4 |
| 프롬프트-LLM 피드백 무효 | 성공사례 없음 | Kamoi TACL'24, 2406.01297 | CONFIRMED | D4 |
| 별도 critic 우월 | ~+49% pass@1(7.88→11.76) | CTRL, 2502.03492 | PARTIAL(+106%는 틀림→수정) | D4(verifier 강점) |
| 외부 PRM | SWE-bench 40.0→50.6% | 2509.02360 | CONFIRMED(귀속 약화) | D4 |
| 코딩 멀티에이전트 부적합 | 순차 39~70%↓ | Google/MIT, 2512.08296 | CONFIRMED | D5(중첩 보류) |
| 매니페스트 강제 선례 | RooCode fileRegex 런타임 차단 | docs.roocode.com | CONFIRMED | D1/D6 |
| 지시파일 준수 한계 | 구조조정 무효 시사 | arXiv 2605.10039 | 미검증 프리프린트(격하) | D2 보조 |
| Plan Mode advisory | 프롬프트 문자열 하나 | dev.to/eyesofish | CONFIRMED | D2 |
| 강제 수단 | deny 는 harness 강제(단 Edit 불안정) | code.claude.com; #13744/#37210 | CONFIRMED | D2/D3 |

*삭제된 인용: "Scope Guard 35%→4%"(확인 불가), agent-guardrails `--intended-files` 플래그명(미확인 — "manifest 류 도구 존재"로만).*

## 부록 B. 경쟁 도구 강제 수준 (재검증)

| 도구 | 매니페스트/쓰기 화이트리스트 | 중첩 |
|---|---|---|
| RooCode | 예(fileRegex 사전차단, CONFIRMED) | 예(new_task) |
| Claude Code | 예(PreToolUse deny — Edit 효능 불안정) | 예(subagent) |
| agent-guardrails | 예(권한 deny 기반; 정확 플래그 미확인) | 아니오 |
| Aider | 부분(/add 편집집합) | 아니오 |
| Cline | denylist 만(.clineignore) | 예 |
| Cursor/Devin/Copilot/OpenHands | 아니오(계획=조언/PR경계) | 일부 |

→ 매니페스트 + (보류된)중첩 + 종료 verifier 리뷰의 결합은 미존재 — v2 의 신규성(단 v2.0 은 평면).

## 부록 C. 기존 결함 → v2 매핑

| 결함 | v2 | 단계 |
|---|---|---|
| C1 tag refname | 프라이빗 ref(D7) | v1.40.x |
| C2 stash drop 유실 | drop 제거 + touched 매니페스트 롤백 | v1.40.x |
| C3 git 경로 무테스트 | 행위 테스트 + env 격리 | v1.40.x |
| ST-2 상태 분산 | transition() 중앙화 | v2.0 |
| ST-3 락 없음 | 보류(atomic-rename), 멀티세션 실증 시 | — |
| B-1 임계 오탐 | 휴리스틱 삭제 | v2.0 |
| absolute-path dead 분기 | 정규화 일원화 | v2.0 |
| Bash 우회 | 2층 PostToolUse 스윕 | v2.0 |
| detour 자기승인 위험 | 3중 사후검증(2층+종료 verifier+사람) | v2.0 |

---

*이 설계서(rev.2)는 구현 세션의 입력이다. 코드 작성 전 §2 결정과 §5 위험을 재확인하고, CLAUDE.md 의 행위 검증(smoke_test) + plugin.json 버전 번프 + 태그 규칙을 준수한다. 특히 §5 의 "1층 deny 효능 실측"은 v2.0 의 빌드 게이트다.*
