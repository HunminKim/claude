# plan-gate v2 상세 설계서 — 스코프 강제 계약 모델

> **상태**: 설계 확정 (구현 전). 이 문서는 별도 구현 세션의 단일 입력이다.
> **작성 근거**: harness-check 비판 리뷰(260612~260613) → 구조 결함 식별 → claude-code-guide 2차 토론 + 하네스 엔지니어링 연구 5각(SOTA 가드레일 / 경쟁 도구 / 자기승인 효과성 / 중첩 분해 / 스펙 주도) → 사용자 설계 결정 반영.
> **브랜치**: `claude/critical-code-review-5gcym9`
> **버전 목표**: v1.40.x(토대) → v2.0.0(평면 스코프) → v2.1.0(중첩 스택)
> **이 문서가 다루지 않는 것**: 코드. 함수 시그니처·git 명령은 *설계 의도를 고정*하기 위한 의사코드 수준이며, 구현 세션에서 행위 검증과 함께 확정한다.

---

## 0. 이 설계가 필요한 이유 (배경)

운영 중 다수의 버그를 처리했으나 **구조적 결함은 증상 패치로 해소되지 않았다.** 비판 리뷰에서 행위 검증으로 확정한 치명 결함:

- **C1. git 모드 체크포인트가 출시 이후 한 번도 작동한 적 없음.** `TAG_PREFIX = ".claude/gate/"` (plan_gate_lib.py:65) 는 git refname 규칙 위반(경로 컴포넌트가 `.`으로 시작 불가) → `git tag` rc=128 영구 실패 → git 프로젝트에서 `/rollback` 전면 불능인데 안내문은 "안전하게 되돌릴 수 있습니다"라고 약속.
- **C2. `/done`·`/skip` 의 stash pop 실패 시 drop → 사용자 편집 무음 영구 유실.** `do_gate_done` (plan_gate_lib.py:830-838) 가 충돌 시 `stash drop` 으로 폴백. `git fsck` dangling 으로만 복구 가능. 재현 완료.
- **C3. 검증 체계가 기본 경로를 안 봄.** 스모크 184개가 git tag 경로를 행위 검증하지 않아 C1·C2가 모든 릴리스를 통과. signing 환경에선 suite 전체가 0개 검증으로 즉사.

근본 원인은 **트리거 축이 잘못 선택된 것**이다. 현행 plan-gate 는 편집 *양*(같은 파일 5회 반복, `TRIGGER_REPEAT_RATIO`)으로 작동한다. 이는 "계획되지 않은 파일을 수정하는 할루시네이션"이라는 실제 통증을 직접 겨냥하지 못하고, 오탐(단일 C파일 다회편집)·dead 분기(`is_doc_path` 절대경로 미스매치)·카운터 오염(`/replan` 미완 리셋)을 양산했다.

**v2 의 전환**: 트리거 축을 *편집량* → *편집범위*로 바꾼다. 계획이 건드릴 파일 집합(매니페스트)을 사전 선언하고, 그 밖의 편집을 기계적으로 차단한다.

---

## 1. 설계 목표 / 비목표

### 목표
1. **계획되지 않은 파일의 *조용한* 수정을 0으로 만든다.** 스코프 밖 편집은 반드시 차단되거나, 차단을 우회해도 사후 탐지·롤백된다.
2. **계획을 advisory(조언)에서 enforced(기계 강제) 계약으로 승격한다.**
3. **체크포인트·롤백을 실제로 작동시킨다** (C1/C2 근본 해소).
4. **버그 표면을 줄인다** — 퍼지 휴리스틱을 crisp한 멤버십 검사로 대체(Subtraction-First).
5. **큰 작업을 중첩 스코프로 통제한다** — "메인 게이트 안에서 d파일 detour를 별도로 열고 닫고 본 작업 복귀" (v2.1).

### 비목표
- OS 수준 완전 샌드박스(범위 밖 — 자기 가드 변조·임의 subprocess는 부분 완화만).
- 병렬 멀티에이전트 편집(연구상 코딩에 부적합 — §3.3).
- 임의 깊이 중첩의 화려한 UX(스택은 임의 깊이 지원하되 운용은 1~2단계).

---

## 2. 핵심 설계 결정 + 선정 이유

각 결정은 **결정 / 대안 / 선정 이유(근거)** 형식으로 기록한다.

### D1. 트리거 축: 편집범위(스코프) — 편집량 아님

- **결정**: 매니페스트에 선언된 파일 집합 밖의 Edit/Write/MultiEdit 를 차단한다. 편집 횟수 카운팅은 폐기한다.
- **대안**: (a) 현행 편집량 트리거 유지·보정, (b) 양·범위 혼합.
- **선정 이유**:
  - 사용자의 실제 통증("계획 외 파일 할루시네이션")은 *양*이 아니라 *범위* 문제다. 양 트리거는 그 통증을 간접 프록시로만 잡았고 오탐·dead 분기를 낳았다(비판 리뷰 B-1, ST-2).
  - 범위 모델은 `TRIGGER_REPEAT_RATIO`·`_max_code_repeat`·scope-creep 산식·soft-hint·오버라이드 마커·`is_doc_path` 를 *삭제*하고 단일 멤버십 검사로 대체한다 — 버그 표면 축소(CLAUDE.md Subtraction-First 정합).
  - 업계 검증: 진짜 강제를 하는 도구는 전부 범위 기반이다 — RooCode `fileRegex`(런타임 차단), Aider `/add` 편집집합, agent-guardrails `--intended-files`. 양 기반 강제 도구는 조사 결과 없음.

### D2. 강제 위치: 도구 계층(PreToolUse) — 프롬프트/모드플래그 아님

- **결정**: 매니페스트 검사는 PreToolUse 훅의 `permissionDecision:deny`(또는 exit 2)로 한다.
- **대안**: (a) CLAUDE.md/매니페스트를 컨텍스트에 주입(advisory), (b) Plan Mode 같은 모드 플래그.
- **선정 이유**:
  - 지시 파일 준수율은 신뢰 불가다 — arXiv:2605.10039(16,050 관측): 파일 크기·위치·구조 조정으로도 준수 개선 없음. 실무 추정 ~80%만 준수.
  - 네이티브 Plan Mode 는 "시스템 프롬프트 문자열 하나"로 사소하게 우회되고(dev.to 코드 분석), 승인된 계획을 *훅에 노출하지 않으며*, 모든 편집을 막아 너무 거칠다 → 우리 매니페스트를 대체 불가(claude-code-guide 1차).
  - 모드 플래그는 누수한다 — Cline Plan 모드가 편집을 수행하는 다수 버그(#4387/#4848/#4687/#10497).
  - 공식 문서: "권한 규칙은 모델이 아니라 Claude Code(하네스)가 강제한다." PreToolUse 차단은 권한 규칙보다 먼저 평가되고 `--dangerously-skip-permissions` 에서도 작동 — 모델이 말로 우회 불가능한 유일한 네이티브 수단.
  - **실증**: 현행 plan-gate 가 39개 버전간 exit 2 로 편집을 *실제 차단*해 왔다(이 리뷰의 전제 자체). 따라서 이 환경에서 PreToolUse 차단은 작동한다.

### D3. 계층형 방어: 사전 차단 + 사후 스윕 + 체크포인트

- **결정**: 3층으로 강제한다. ①PreToolUse deny(주 차단) ②Stop/PostToolUse git-status 스윕(우회 구멍 메움) ③프라이빗 ref 체크포인트(롤백).
- **대안**: PreToolUse 단일 차단에만 의존.
- **선정 이유**:
  - 사전 차단은 **airtight 가 아니다**. 검증된 우회 구멍: Bash 로 파일 쓰기(`echo > d.py`, `sed -i`, 파이썬 스크립트)는 Edit|Write 매처를 안 탄다. 공식 문서도 "Read/Edit 규칙은 임의 subprocess 에 적용 안 됨"을 명시.
  - 따라서 **2층(Stop 시점 working tree 전체를 매니페스트와 대조)** 이 1층이 못 막는 모든 쓰기 경로를 잡는 catch-all 이다. 스코프 밖 변경 탐지 → 체크포인트에서 해당 파일만 롤백 + Claude 환기.
  - 연구 합의(SOTA 가드레일 #6/#9, agent-guardrails 3계층): 파일 계층 강제는 사전 차단 + 사후 탐지/복구의 defense-in-depth 여야 한다.
  - RooCode #4732(동시편집 시 게이트 우회) 교훈: **모든 쓰기 경로를 덮어야** 한다.

### D4. 스코프 확장 검증: verifier 서브에이전트 — 자기승인 절대 금지 ★

- **결정**: detour(스코프 밖 파일 추가)는 Claude 가 신청하되 **자기가 승인하지 않는다.** 별도 **verifier 서브에이전트**가 "이 파일이 목표에 정말 필요한가 + 변경이 최소·범위 내인가"를 독립 검토해 통과시킨다. 사람은 메인 게이트 종료 시 모든 확장을 일괄 비준(배치된 최종 외부 검증).
- **대안**: (a) Claude 자기승인(사용자 최초 아이디어), (b) 매 detour 사용자 승인, (c) 종료 시 일괄 사람 비준만.
- **선정 이유 (이 설계에서 가장 강한 근거)**:
  - **자기승인은 학술적으로 신뢰 불가.** 자기교정 맹점 64.5%(Self-Correction Bench, 14모델) — 동일 오류를 외부에선 고치나 자기 출력에선 못 고침. 자기선호 편향(NeurIPS 2024), FlipFlop 아첨(46% 답 뒤집기/17% 정확도 하락), "LLM 은 오라클 없이 자기교정 불가"(Huang et al. ICLR 2024), "프롬프트-LLM 피드백만으론 성공 사례 없음"(Kamoi et al. TACL 2024). 같은 모델이 제안+승인하는 건 자기선호·아첨의 최악 케이스.
  - **독립 검증은 효과 입증.** 별도 비평 모델이 자기비평을 압도(CTRL 코드 +106% 상대, ICML 2025), 외부 PRM 이 SWE 에이전트 40.0→50.6%(IBM, NeurIPS 2025 W), LLM-Modulo(sound verifier + 생성) > 자기비평(Kambhampati ICML 2024).
  - **(a) 기각**: 할루시네이션을 해결하지 못하고 "선언된 정당화"로 재배치할 뿐(theater). (b) 기각: detour 빈도가 높아 자율성 파괴(사용자 매번 끊김). (c) 채택하되 D4 의 verifier 와 **병행** — verifier 는 즉시 외부 검증, 사람은 종료 시 배치 비준. 이중 외부 검증.
  - 자기승인은 안전장치에서 **감사 기록으로 격하** — "정당화를 기록하고 외부 검증을 통과한다", "자기가 옳다고 판정한다"가 아니다.

### D5. 중첩 구현: 단일 에이전트 스코프 스택 — 서브에이전트 아님

- **결정**: 중첩 게이트는 *하나의* Claude(하나의 연속 컨텍스트)가 관리하는 스코프/상태 스택이다. detour 를 격리 서브에이전트로 띄우지 않는다.
- **대안**: detour 를 `isolation:worktree` 서브에이전트로 위임.
- **선정 이유**:
  - 코딩은 컨텍스트 공유가 필요하다 — Anthropic "대부분 코딩은 멀티에이전트 부적합", Google "순차 추론 멀티에이전트로 39~70% 저하(상태 연속성 파괴)", Cognition "단일 스레드 기본". 멀티에이전트 실패의 ~37%가 핸드오프/오정렬(MAST).
  - 격리 위임의 실패 사례가 이미 문헌에 있다 — opencode #6527/Cline: 서브에이전트가 부모 plan-mode 제약을 탈출. "중첩 스코프 상속"은 아무도 깔끔히 못 푼 영역.
  - 단일-컨텍스트 스택은 핸드오프/오정렬/false-completion 을 구조적으로 제거한다. 비용은 컨텍스트 압박 → compaction + 영속 plan/todo 파일을 백본으로(연구 #14~16).
  - **참고(향후)**: 정말 *병렬* detour 가 필요해지면, 그때의 답은 컨텍스트 공유가 아니라 git 락+머지 substrate(Anthropic C-compiler 패턴)다. 순차 흐름엔 단일-에이전트-스택이 정답.

### D6. 매니페스트 강도: 명시 화이트리스트 + 글롭 허용

- **결정**: 파일을 명시 나열하되 `src/auth/**` 같은 글롭을 허용한다. 화이트리스트 밖은 전부 차단(default-deny).
- **대안**: (a) 엄격(글롭 금지, 파일만), (b) 관대(디렉토리 단위).
- **선정 이유**:
  - (a) 기각: 리팩터/리네임 같은 다파일 작업에서 detour 폭증(매번 12개 파일 선언). 강제력은 최강이나 실용성 붕괴.
  - (b) 기각: 디렉토리 단위는 "계획 밖 파일" 차단이 느슨해져 anti-hallucination 효과 약화.
  - 글롭은 강제력과 실용성의 균형점. 단 글롭 *단독* 확장(detour 에서 `**`)은 금지(자기 전체스코프 부여 차단, §3.4).
  - 매니페스트는 **버전관리·런 중 불변**(Spec Kit "constitution" 패턴, 버전관리 allowlist 권고). 확장은 명시적·기록되는 이벤트.

### D7. 체크포인트: 프라이빗 ref 스냅샷 커밋 — tag+stash 폐기

- **결정**: 게이트 열 때 `commit-tree` 로 `refs/plan-gate/<gate_id>/checkpoint-N` 에 비파괴 스냅샷. 롤백은 `read-tree`+`checkout-index`(reset/stash 미사용). 비-git 은 게이트별 cp 디렉토리 스택.
- **대안**: (a) 현행 tag+stash 의 refname 버그만 고치고 유지, (b) `isolation:worktree`.
- **선정 이유**:
  - (a) 부분 기각: refname 을 고쳐도 stash pop 충돌→데이터 유실(C2)과 dirty tree 에서 stash→diff 순서 역전이 남는다. stash 는 단일 working tree 에서 본질적으로 침습적(작업 중 파일을 HEAD 로 되돌림).
  - 프라이빗 ref 커밋: ①사용자 stash 목록 무간섭 ②모든 스냅샷이 영구 복구 가능한 커밋 객체 ③중첩 스택을 자연히 지원 ④파괴적 `reset --hard`+`stash pop` 댄스 제거. claude-code-guide 2차가 동일 결론.
  - (b) 기각: worktree 는 병렬 격리용 — 단일 working tree 의 순차 중첩엔 과중(D5 와 동일 논리).
  - 비-git: 기존 `cp_snapshot_file`/`cp_rollback`(plan_gate_lib.py:275-337)이 게이트별 디렉토리라 스택이 그대로 성립 — 구조 변경 최소.

### D8. 동시성: stdlib fcntl 배타락

- **결정**: 상태 파일 load-modify-save 를 `fcntl.flock` 배타락으로 감싼다(POSIX; Windows `msvcrt` 분기). 외부 의존 금지(CLAUDE.md).
- **대안**: 현행 atomic-rename 만 유지, `filelock` PyPI 패키지.
- **선정 이유**:
  - atomic-rename(plan_gate_lib.py:160-162)은 파일 깨짐만 막고 lost-update 는 못 막는다. 병렬 Edit·동시 세션에서 카운터/상태 유실(비판 리뷰 ST-3, S-1).
  - `filelock` 패키지는 CLAUDE.md "표준 라이브러리만" 위반 → 기각. `fcntl` 는 stdlib.
  - 상태 조작이 5곳(plan_gate.py/cli/update_docs/detect_task_boundary/plan_approval)에 흩어진 것도 함께 정리 — 락 + 단일 `transition()` 으로 중앙화.

---

## 3. 아키텍처 상세

### 3.1 매니페스트 (계약 아티팩트)

위치: `tasks/todo.md` 내부의 기계가독 블록. 게이트 생성/승인 시 1회 파싱 후 `gate["scope"]`·`gate["do_not_touch"]` 에 저장 + 원문 sha256 을 `gate["manifest_sha256"]` 에 고정(TOCTOU 가드).

```
<!-- plan-gate: scope -->
src/auth/**
src/models/user.py
tests/test_auth.py
<!-- plan-gate: do-not-touch -->
src/payment/**
```

규칙:
- 매칭은 루트 상대경로 기준 `fnmatch`(절대경로 정규화 필수 — 현행 `is_doc_path`/`verifier_remind` 의 절대경로 미스매치 버그를 반복하지 않는다).
- `do-not-touch` 가 `scope` 보다 우선(deny-first). do-not-touch 매칭은 detour 로도 풀 수 없다.
- `.plan-gateignore`(생성물·락파일·포매터 출력)는 스코프 검사를 우회(기존 자동 추가 로직 유지).
- 매니페스트 자신(`tasks/todo.md`)·`.claude/state/`·훅 디렉토리는 **스코프 검사 대상에 포함** — 자기 가드 변조를 부분 차단(#11226 완화).
- 매니페스트가 런 중 변경되면(sha 불일치) 확장은 무효 → 재승인 요구.

### 3.2 강제 3층 (상세)

**1층 — PreToolUse (matcher: Edit|Write|MultiEdit) — 주 차단**
- target 추출 → 루트 상대 정규화 → `do_not_touch` 매칭이면 무조건 deny → `scope` 매칭이면 allow → 아니면 deny.
- deny 메시지(RooCode UX 차용): **허용 경로를 명시**하고, 확장 CLI 한 줄을 *복붙 가능하게* 제시. 예:
  ```
  🛑 d.py 는 현재 계획 스코프 밖입니다. 허용: [src/auth/**, src/models/user.py, tests/test_auth.py]
  이 파일이 목표에 필요하면 아래를 실행해 detour 를 신청하세요(verifier 검토 후 통과):
    python3 ${CLAUDE_PLUGIN_ROOT}/hooks/plan_gate_cli.py subplan d.py "이유"
  그 후 편집을 재시도하세요.
  ```
- 출력 채널: `permissionDecision:deny` + `permissionDecisionReason`(JSON). exit-2 stderr 보다 컨텍스트 주입이 안정적일 가능성 — 빌드 시 실측(§5).
- exit-2 후 Claude 멈춤 위험(#24327)은 메시지 actionability 로 완화하고, 2층이 안전을 보장하므로 *안전*은 모델 협조와 무관.

**2층 — Stop / PostToolUse — git-status 스윕 (catch-all)**
- 트리거 시점: Stop(턴 종료) 또는 PostToolUse(Bash 직후).
- 동작: `git status --porcelain`(또는 비-git 시 cp 매니페스트 대조)로 working tree 의 *모든* 변경을 매니페스트와 대조. 스코프 밖 변경 발견 시:
  - 해당 파일을 게이트 체크포인트에서 롤백(해당 파일만, 부모 변경 보존).
  - Claude 에 환기: "스코프 밖 변경 <파일> 을 되돌렸습니다. 필요하면 /subplan 으로 신청하세요."
- 이 층이 **Bash·subprocess 우회를 잡는 핵심**. 1층은 빠른 차단, 2층은 완전성.
- Stop 훅 주의: `stop_hook_active==true` 가드 필수(CLAUDE.md 8회 연장 사고 방지). 변경 없으면 무음 exit 0.

**3층 — 체크포인트 (프라이빗 ref)**
- 게이트 열 때(첫 편집 직전):
  ```
  TREE=$(git write-tree)                       # 현재 인덱스/트리 스냅샷
  COMMIT=$(git commit-tree $TREE -p HEAD -m "plan-gate checkpoint <gate_id>")
  git update-ref refs/plan-gate/<gate_id>/checkpoint-0 $COMMIT
  ```
  (dirty tree 도 `git add -A` 후 write-tree 로 그대로 캡처 — C2/dirty-diff 문제 동시 해소)
- 롤백(해당 게이트만, 부모 보존):
  ```
  git read-tree refs/plan-gate/<parent_or_self>/checkpoint-0
  git checkout-index -f -u -a
  ```
- 닫기: `git update-ref -d refs/plan-gate/<gate_id>/checkpoint-0` (커밋 객체는 reflog/fsck 로 잔존·복구 가능 — drop 같은 유실 없음).
- 비-git: 게이트별 `cp_checkpoint_dir` 스택(기존 함수 재사용).

### 3.3 상태 모델 / 상태 기계

v2.0(평면): 단일 게이트 + 스코프. v2.1(중첩): `gate_stack`.

```
state = {
  "schema_version": 2,
  "gate_stack": ["<root_gate_id>", "<sub_gate_id>"],   # leaf = 활성 (v2.0 은 항상 길이 1)
  "gates": {
    "<gate_id>": {
      "id", "state",                                    # created|approved|verified|done|rolled_back
      "parent_gate_id",                                 # v2.1; v2.0 은 null
      "scope": ["src/auth/**", ...],
      "do_not_touch": [...],
      "manifest_sha256",
      "checkpoint_commit",                              # refs/plan-gate/<id>/checkpoint-0 의 SHA
      "cp_snapshot",                                    # 비-git 매니페스트
      "expansions": [                                   # detour 기록 (감사 + 종료 시 사람 비준)
        {"file": "d.py", "reason": "...", "verifier_verdict": "✅|❌", "ts": "..."}
      ],
      "verifier_status",
      "created_at", "approved_at", "closed_at"
    }
  }
}
```

- 합법 전이는 `plan_gate_lib.transition(gate, to_state)` **단일 함수**로 강제(필드 리셋 일괄, 불법 전이 거부). 5곳 분산 조작 폐기(ST-2 해소).
- 모든 상태 I/O 는 fcntl 락 안에서(D8).

### 3.4 두 단계 권한 + verifier 게이트 (detour 흐름)

```
[1층 차단] Claude 가 스코프 밖 d.py 편집 시도 → deny
   ↓
[신청] Claude: Bash로 plan_gate_cli.py subplan d.py "이유"
   ↓
[가드 검사] CLI: 글롭단독 거부 / do-not-touch 침범 거부 / 루트밖·".." 거부 / (v2.1) 자식⊆부모
   ↓
[외부 검증] verifier 서브에이전트 호출: "d.py 가 목표 '<매니페스트 의도>' 에 정말 필요한가?
            변경이 최소·범위 내인가?" → ✅/❌
   ↓ ✅
[기록] gate["expansions"] 에 {file, reason, verdict} 추가, scope 에 d.py 임시 편입
   ↓
[재시도] Claude 가 d.py 편집 → 1층 통과
   ↓
[메인 종료] /done 시 모든 expansions 를 사용자에게 일괄 노출 → 사람 최종 비준
```

- 메인 토큰(approve/replan/done): `disable-model-invocation:true`(사용자 전용) 유지.
- subplan 토큰: Claude 가 Bash CLI 로 호출 가능하되 **verifier 통과 없이는 scope 편입 안 됨** — 자기승인 불가가 코드로 강제.
- verifier ❌ 시: 편집 차단 유지 + Claude 에 "이 파일은 목표에 불필요하다고 판정됨, 계획을 재고하거나 사용자에게 스코프 확대를 요청하라" 환기.

### 3.5 삭제 대상 (Subtraction-First)

범위 모델 도입과 함께 제거:
- `TRIGGER_REPEAT_RATIO`, `MAX_EDIT_OVERRIDE`, `_OVERRIDE_RE`, `parse_gate_overrides`, `_threshold_for`, `post_approval_limit_exceeded`, `post_approval_stats`(편집량 트리거 일체)
- `_max_code_repeat`, `_unique_code_files`, `trigger_threshold_exceeded`, `format_soft_hint`, `format_scope_creep_message`
- `is_doc_path` 및 `_DOC_*`(문서 제외 휴리스틱 — 스코프가 대체)
- todo.md 오버라이드 마커 안내(B-1)
- 관련 스모크 케이스 → 스코프 멤버십 테스트로 교체

유지/이관: 패치 이력(hot-file)은 선택적 유지(세션 간 누적 경고는 범위와 직교) — 단 절대경로 버그 수정 필수.

---

## 4. 단계적 릴리스 계획

### v1.40.x — 토대 (major 아님, 즉시 가능)
구조 변경 없이 치명 결함부터 해소. v2 가 이 위에 선다.
1. 체크포인트 백엔드를 tag → 프라이빗 ref 스냅샷으로 교체(D7) — git 롤백이 *처음으로* 작동.
2. stash drop 폴백 제거(C2) — pop 실패 시 보존 + 명시 안내.
3. fcntl 락 도입(D8).
4. 스모크에 **git 백엔드 행위 테스트 신설**(트리거→체크포인트 존재→rollback 복원→done 정리) + `GIT_CONFIG_GLOBAL=/dev/null` 격리(C3).
5. 보안 우회 차단: `rm -rf /*`/`~`/`-fr ~/`/`bash -c '...'`(dangerous_bash_check), Grep 디렉토리·glob 우회(secret_read_guard), 두 훅 시크릿 정책 lib 통합.
6. **누락 태그 푸시**: v1.38.0·v1.39.0(릴리스 규칙 위반 상태 해소).

### v2.0.0 — 평면 스코프 (사용자 결정: 평면 먼저)
1. 매니페스트 파싱·고정(3.1).
2. 강제 3층(3.2) — 평면(스택 길이 1).
3. detour = verifier 게이트 확장(3.4). 평면에선 종료 시 누적 expansions·전체 diff 를 verifier+사람이 일괄 검토.
4. 상태 전이 중앙화(`transition`).
5. 편집량 휴리스틱 일괄 삭제(3.5).
6. 행위 검증: 스코프 밖 차단/2층 스윕 롤백/detour verifier ✅·❌/매니페스트 sha 가드.

### v2.1.0 — 중첩 스택 (평면 실증 후)
1. `gate_stack` + `parent_gate_id`, 자식 scope ⊆ 부모(Goose 상속).
2. 리프-only 활성 스코프(집중 강제) vs 부모∪리프 — 구현 세션에서 행위로 결정.
3. detour 별 독립 체크포인트·롤백(부모 진행 보존).
4. UX 는 1~2단계에 맞춤(깊이 상한 경고).

---

## 5. 남은 위험 & 빌드 시 검증 항목

- **exit-2/deny 차단 효능을 타깃 CLI 버전에서 1회 실측**(빌드 게이트). 거짓이어도 2층 git 스윕이 헤지. (claude-code-guide 2차가 #13744/#37210 으로 "Edit 차단 실패" 주장했으나, 현행 plan-gate 의 39버전 실차단 + 공식 문서 + 타 연구 3곳과 모순 → 버전 한정/수정됨으로 판단, 단 실측으로 확정.)
- **자기 가드 변조(#11226)**: 매니페스트·state·훅 디렉토리를 스코프 검사 대상에 포함(부분 완화). 완전 차단은 OS 샌드박스(비목표).
- **detour 빈도**: 실작업은 파일을 진행하며 발견 → 확장 마찰을 낮게(한 줄 CLI + 빠른 verifier). 너무 잦으면 매니페스트가 부실하다는 신호 → 계획 단계 보강.
- **컨텍스트 압박(D5 비용)**: 영속 todo/plan 파일을 백본으로, compaction 은 lossy 가정(load-bearing 제약은 외부 노트에).
- **verifier 비용/지연**: detour 마다 서브에이전트 1회 → 토큰·지연. 경량 프롬프트 + 평면 v2.0 은 종료 시 배치로 완화.

---

## 부록 A. 연구 근거 요약

| 주제 | 핵심 결과 | 출처 | 설계 반영 |
|---|---|---|---|
| 스펙=조언 한계 | 지시 파일 준수 신뢰 불가, 구조 조정 무효 | arXiv:2605.10039 (16,050관측) | D2 (도구 계층 강제) |
| 강제 수단 | PreToolUse deny = 모델 우회 불가, skip-perm 작동 | code.claude.com/docs/hooks | D2/D3 |
| 우회 구멍 | Edit 규칙은 subprocess/Bash 미적용 | 공식 docs | D3 (2층 스윕) |
| 자기승인 무효 | 자기교정 맹점 64.5%, "오라클 없이 불가" | Self-Correction Bench; Huang ICLR'24; Kamoi TACL'24 | D4 (verifier) |
| 독립 비평 우월 | 별도 critic +106%; PRM 40→50.6% | CTRL ICML'25; IBM PRM NeurIPS'25 | D4 |
| 코딩 멀티에이전트 부적합 | 순차작업 39~70% 저하; 핸드오프 실패 37% | Google/MIT; Cognition; MAST | D5 (단일 스택) |
| 매니페스트 선례 | RooCode fileRegex 런타임 차단; agent-guardrails | RooCode docs; GitHub | D1/D6, UX 차용 |
| 신규성 | 매니페스트+중첩+verifier 결합은 미존재 | 경쟁 스캔 종합 | 기여점 |

## 부록 B. 경쟁 도구 강제 수준

| 도구 | 매니페스트/쓰기 화이트리스트 | 중첩 스코프 |
|---|---|---|
| RooCode | 예 (fileRegex 사전차단) | 예 (new_task) |
| Claude Code | 예 (PreToolUse deny) | 예 (subagent) |
| agent-guardrails | 예 (--intended-files) | 아니오 |
| Aider | 부분 (/add 편집집합) | 아니오 |
| Cline | denylist 만 (.clineignore) | 예 |
| Cursor/Devin/Copilot/OpenHands/SWE-agent | 아니오 (계획=조언/PR경계) | 일부 |

→ **매니페스트 + 중첩 + verifier 게이트 확장의 결합**은 어디에도 없음 = v2 의 신규 기여.

## 부록 C. 기존 결함 → v2 매핑

| 기존 결함 | v2 에서 | 단계 |
|---|---|---|
| C1 tag refname 영구 실패 | 프라이빗 ref(D7) | v1.40.x |
| C2 stash drop 유실 | drop 제거 + ref 보존 | v1.40.x |
| C3 git 경로 무테스트 | 행위 테스트 신설 + env 격리 | v1.40.x |
| ST-2 상태 분산 | transition() 중앙화 | v2.0 |
| ST-3 락 없음 | fcntl(D8) | v1.40.x |
| B-1 임계 오탐 | 휴리스틱 삭제(스코프 대체) | v2.0 |
| verifier_remind/is_doc_path 절대경로 dead | 정규화 일원화 | v2.0 |
| /replan 미완 리셋 | 카운터 폐기 | v2.0 |

---

*이 설계서는 구현 세션의 입력이다. 코드 작성 전 §2 결정과 §5 위험을 사용자와 재확인하고, CLAUDE.md 의 행위 검증(smoke_test) + plugin.json 버전 번프 + 태그 규칙을 준수한다.*
