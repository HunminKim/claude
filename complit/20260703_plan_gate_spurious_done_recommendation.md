# [하네스 핸드오프] created 게이트에 불필요한 `/done` 추천 — plan-gate 상태 미확인

> **이 문서 하나로 배경·증거·근본원인·수정안·검증까지 모두 파악 가능하도록 자체 완결(self-contained)로 작성했다.**
> 하네스 관리 레포 담당자는 이 프로젝트(vis_usage)를 몰라도 이 문서만으로 조치할 수 있다.

| 항목 | 값 |
|------|-----|
| 작성 시각 | 2026-07-03 (KST) |
| 대상 하네스 | `project-init` 플러그인 **v2.15.0** — plan-gate 하위 시스템 |
| 발생 프로젝트 | vis_usage (Claude Usage Widget, Python/PySide6) — *증상 관찰 현장일 뿐, 버그는 하네스/에이전트 절차에 있음* |
| 심각도 | **낮음(오작동 아님)** — 데이터 손상·차단 없음. 사용자에게 **불필요·무의미한 수동 액션(`/done` 입력)을 요구**해 혼란을 유발한 UX/절차 결함 |
| 분류 | 에이전트 절차 규칙(workflow) ↔ plan-gate 상태머신 간 **모델의 상태 미확인 과적용** |
| 재발성 | **높음** — 소편집을 `/approve-plan` 없이 반복하는 세션이면 매번 재현 가능 |

---

## 0. TL;DR (3줄)

1. 이 세션의 소편집들은 `/approve-plan` 없이 진행돼 plan-gate가 **"created"(미승인) 게이트로 자동 롤오버** 처리했다(설계대로 정상). created 게이트는 **`/done`이 필요 없다** — 다음 편집 사이클이 알아서 닫는다.
2. 그런데 에이전트(Claude)가 `workflow.md`의 **"verifier ✅ → `/done`"** 규칙을, 그 규칙의 전제조건(*게이트가 approved/verified 상태일 때만*)을 확인하지 않고 **기계적으로 적용**해, 사용자에게 불필요한 `/done` 입력을 요구했다.
3. 게다가 추천 근거였던 **세션 재개 배너는 compact 시점의 stale 스냅샷**이었고, 실제 실행 시점엔 이미 2세대 롤오버돼 `current_gate_id == null`이라 `/done`이 "활성 gate 없음"으로 무시됐다.

> **한 줄 요약:** 하네스(Stop 훅)는 created 게이트에 대해 **올바르게 침묵**했는데, 모델이 문서 규칙을 상태 확인 없이 과적용해 **하네스가 시키지도 않은 `/done`을 스스로 지어내 추천**했다.

---

## 1. 배경 — plan-gate 이해에 필요한 최소 지식 (self-contained)

### 1.1 plan-gate란
`project-init` 플러그인이 제공하는 **작업 통제 게이트**. Claude Code 훅으로 동작하며, 편집(Edit/Write)·Bash·세션 이벤트에 개입해 "계획 없이 대규모/위험 변경이 무통제로 진행되는 것"을 막는다. 상태는 프로젝트 로컬 파일에 저장된다:

- `.claude/state/plan_gate.json` — **단일 진실 원천(SSOT)**. `current_gate_id`(현재 활성 게이트) + `gates{}`(게이트별 이력).
- `.claude/state/plan_gate_audit.log` — 게이트 전이·스냅샷·경고의 append-only 감사 로그(JSONL).
- `.claude/plan_gate_enabled` — 활성화 토글 파일.

### 1.2 게이트 상태(state) 종류
| state | 의미 | 마감에 필요한 것 |
|-------|------|-----------------|
| `created` | 편집이 감지됐지만 **`/approve-plan` 미승인** 상태. "소편집·미승인" 취급 | **없음** — 다음 편집 사이클 시작 시 **자동 롤오버로 닫힘**. 명시 `/done`은 "무조건 마감"으로 받아주긴 하나 **불필요** |
| `approved` | `/approve-plan`으로 명시 승인됨. 본격 구현 진행 | verifier ✅ 후 **`/done` 필요** |
| `verified` | verifier 판정 반영 상태 | 판정 따라 `/done`·`/retry`·`/skip` |

### 1.3 "자동 롤오버(rollover)" 메커니즘
`created` 게이트가 열린 상태에서 새 작업 경계/편집 배치가 시작되면, 기존 게이트를 `state=done`으로 닫고(`closed_at` 기록) **즉시 새 게이트를 생성**한다. 즉 미승인 소편집들은 게이트가 꼬리를 물고 자동으로 넘어간다. → **사용자·에이전트가 손으로 닫을 필요가 없다.**

### 1.4 관련 액터(actor) 3종 — 누가 무엇을 말하는가
| 액터 | 파일 | 이 사건에서의 행동 |
|------|------|-------------------|
| **SessionStart 배너** | `hooks/plan_gate_session_start.py` | 세션 재개(compact 포함) 시 **그 시점 스냅샷**의 게이트 상태를 출력. created면 안내는 **`/approve-plan`** (❗`/done` 아님) |
| **Stop 훅** | `hooks/plan_gate_stop_alert.py` | 응답 종료 직전 리마인더. **`gate.state != "approved"`면 아무것도 출력 안 함** → created 게이트엔 `/done`·verifier 권고를 **하지 않음** (올바른 침묵) |
| **에이전트(Claude)** | `.claude/memory/workflow.md` 규칙 참조 | "verifier ✅ → `/done`" 규칙을 **상태 확인 없이 적용** → 문제의 진원지 |

### 1.5 관련 절차 규칙 원문 (`.claude/memory/workflow.md`)
```
### @verifier 검증 후
verifier ✅  →  /done          (체크포인트 정리 + gate 완료)
verifier ❌  →  /retry ...
```
그리고 `/done` 스킬 문서(project-init:done)에는 다음 분기가 명시돼 있다:
```
마감 4종 구분:
- created(검증 불필요)는 무조건 마감
- approved/verified는 verifier ✅ 판정이 있어야 마감
```
→ **규칙 자체엔 "created는 /done 불필요(자동 롤오버)"가 있으나, "verifier ✅ → /done" 요약표는 그 전제(approved/verified 게이트)를 표면에 드러내지 않아 오적용을 유발**한다.

---

## 2. 사건 요약 (사용자가 겪은 것)

1. compact 직후 세션 재개. 배너가 게이트 `…595e9e`를 `state=created`로 표시.
2. 에이전트가 트레이 아이콘 기능을 편집 → `@verifier` 호출 → **✅ 통과**.
3. 에이전트가 사용자에게: **"verifier ✅ 상태라 마감 가능 — `/done` 입력해 주세요."** (❗불필요한 요구)
4. 사용자: "done 처리하고 …" → 에이전트가 `plan_gate_cli.py done` 실행 →
   ```
   [plan-gate done] 활성 gate 없음 — 무시.
   ```
5. 사용자: **"왜 done을 입력하라고 했는데 정작 활성 gate가 없나?"** → 본 조사.

---

## 3. 재현 타임라인 (plan_gate.json 원본 증거)

`.claude/state/plan_gate.json`의 `gates{}` 전체를 시간순으로 정리. **`approved_at`이 null = `/approve-plan` 한 번도 안 함**에 주목.

| 게이트 ID | 파일 | `approved_at` | `verifier_status` | state | `created_at` → `closed_at` |
|-----------|------|---------------|-------------------|-------|----------------------------|
| `…605ec4` | MVP 전체 (edit 57, post-approval 23) | **2026-07-03T00:00:57** (명시 승인, `approved_auto=false`) | **✅** | done | 02-02T23:18 → 03T02:26 (정상 `/done`) |
| `…d8319e` | widget.py, app.py (5분폴링) | **null** | null | created→done | 03T02:28 → 03T03:14 (자동) |
| `…595e9e` | widget.py (90%빨강) | **null** | null | created→done | 03T03:14 → 03T03:26 (자동) **← 재개 배너에 뜬 게이트** |
| `…94357a` | **tray.py, app.py (이번 트레이 작업)** | **null** | null | created→done | 03T03:26 → 03T03:30 (자동) |
| `…67cdca` | docs/.verifier_result.json (verifier가 기록) | null | null | created→done | 03T03:30 → 03T03:34 (자동) |

**결정적 증거 — 자동 롤오버 연쇄:** 각 게이트의 `closed_at`이 **다음 게이트 `created_at`과 밀리초 단위로 일치**한다.

```
595e9e.closed_at = 2026-07-03T03:26:42.463634+00:00
94357a.created_at= 2026-07-03T03:26:42.468077+00:00   (+4.4ms)

94357a.closed_at = 2026-07-03T03:30:30.801703+00:00
67cdca.created_at= 2026-07-03T03:30:30.804716+00:00   (+3.0ms)
```
→ 한 게이트가 닫히는 즉시 다음 게이트가 생성됨. **미승인 소편집이 게이트를 꼬리물기로 자동 롤오버**하고 있었음이 확정된다.

**최종 상태:** `"current_gate_id": null` — 에이전트가 `done`을 실행한 시점(03:34 이후)엔 활성 게이트가 없었다. → "활성 gate 없음 — 무시"의 직접 원인.

`audit.log` 대응 증거(발췌):
```json
{"ts":"…03:14:15.259","action":"snapshot_created","gate_id":"…595e9e","commit":"2d4360535275"}
{"ts":"…03:26:42.676","action":"snapshot_created","gate_id":"…94357a","commit":"4692869fd239"}
{"ts":"…03:30:31.019","action":"snapshot_created","gate_id":"…67cdca","commit":"5aaa7d7a072d"}
```

### 3.1 created(미승인) 게이트의 실제 편집 규모 (측정값)

각 created 게이트가 산출한 실제 변경을 git diff·audit.log로 실측한 값이다. **판정·평가는 배제하고 측정값만 기재**한다.

| 게이트 | 작업(커밋) | 파일별 net diff (git) | `large_edit_advisory` (audit.log) | `approved_at` | 게이트 `verifier_status` |
|--------|-----------|----------------------|-----------------------------------|---------------|--------------------------|
| `…d8319e` | 폴링5분·카운트다운·⟳ (`602e8f4`) | `src/widget.py` +95/−14 (106줄 변경), `src/app.py` +2/−1 | **발화** — `added:197, files:1` @03:10:25 | null | null |
| `…595e9e` | 요일·갱신경과·90%빨강 (`103f6b2`) | `src/widget.py` +51/−34 (85줄 변경) | **발화** — `added:214, files:1` @03:14:15 | null | null |
| `…94357a` | 트레이 아이콘·더블클릭 (`6de7fcb`) | `src/tray.py` +28/−1, `src/app.py` +2/−3 (+문서 4파일 +37/−2) | (없음) | null | null |

측정 관련 사실:
- **`large_edit_advisory`의 `added` 값이 git net diff보다 큰 이유:** 이 카운트는 게이트 내부에서 발생한 **개별 Edit 조작의 삽입 라인을 누적**한 값이다. 같은 영역을 여러 번 고쳐 쓴 재작성(delete+add)이 겹치면 최종 커밋의 순증분(+95/+51)보다 커진다.
- **세 게이트 모두 `approved_at:null`** — `/approve-plan`을 거치지 않았다.
- **세 게이트 모두 게이트 레코드상 `verifier_status:null`** — verifier 판정이 해당 게이트에 링크·기록되지 않았다. (별도의 수동 점검(ruff·pytest·스모크)은 대화 로그에 존재하나, 게이트 상태에는 반영되지 않음.)
- `d8319e`·`595e9e` 두 건은 plan-gate가 `large_edit_advisory`를 발화했으나, **advisory는 환기 전용이라 승인을 강제하지 않으므로** 그대로 created 상태로 자동 롤오버됐다.

---

## 4. 근본 원인 분석

문제는 **두 개의 결함이 겹쳐** 발생했다. 어느 하나만 있었어도 사용자에게 잘못된 액션을 요구하지 않았을 것이다.

### 원인 ① (주원인) 규칙의 전제조건을 무시한 기계적 적용
- 첫 대형 작업(`605ec4`)만 `/approve-plan`으로 승인됐고, **이후 소편집(폴링·90%빨강·트레이)은 전부 미승인 = created 게이트**로 진행됐다.
- `workflow.md`의 요약표 **"verifier ✅ → `/done`"**는 원래 **approved/verified 게이트 전용**이다(§1.5). created 게이트는 `/done`이 **불필요**하다.
- 에이전트는 "verifier가 통과했다"는 사실만으로 이 요약표를 적용하고, **현재 게이트가 created인지 approved인지 확인하지 않았다.**
- **하네스는 무죄:** Stop 훅(`plan_gate_stop_alert.py`)은 `gate.state != "approved"`면 **아무 권고도 출력하지 않는다**(created엔 침묵). 즉 하네스는 `/done`을 시킨 적이 없다. **에이전트가 문서 규칙을 근거로 스스로 `/done`을 지어냈다.**

### 원인 ② (증폭) stale 재개 배너를 live 상태로 착각
- 에이전트가 근거로 삼은 재개 배너의 게이트(`595e9e, created`)는 **compact 시점(03:14)의 스냅샷**이다. `plan_gate_session_start.py`는 재개 순간의 상태를 한 번 출력할 뿐, 이후 갱신하지 않는다.
- `/done`을 안내하던 시점(03:34 무렵)엔 이미 `94357a → 67cdca`로 **2세대 롤오버**돼 있었고 `current_gate_id == null`이었다.
- 에이전트는 **안내 직전에 live 상태(`plan_gate.json`)를 조회하지 않고**, 오래된 배너 + 요약표만으로 판단했다.
- (부차: 배너의 created 안내 문구는 실제로 `/approve-plan`이었는데, 에이전트는 그것도 아닌 `/done`을 말했다 → 배너조차 정확히 반영하지 않은 이중 실수.)

### 인과 요약
```
소편집을 /approve-plan 없이 진행  →  created 게이트 (자동 롤오버, /done 불필요)
        │
        ├─(①) 에이전트가 "verifier ✅ → /done" 요약표를 상태 확인 없이 적용
        │        → 사용자에게 불필요한 /done 요구
        │
        └─(②) 근거가 stale 배너(compact 스냅샷) — live 미조회
                 → 실행 시점엔 이미 롤오버 완료, current_gate_id=null
                        → "활성 gate 없음 — 무시" (요구가 무의미했음이 사후 드러남)
```

---

## 5. 영향 / 심각도

- **데이터·코드 손상 없음.** 게이트는 정상 롤오버됐고 커밋·검증 모두 온전.
- **사용자 신뢰·주의력 비용:** 아무 효과 없는 수동 액션(`/done`)을 요구받아 "왜 시켰는데 안 먹히지?"라는 혼란 발생. 반복되면 plan-gate 안내 전반의 신뢰가 하락.
- **재발성 높음:** `/approve-plan` 없이 소편집을 반복하는 흔한 패턴에서 매번 재현.

---

## 6. 제안 수정 (하네스 레포에서 조치)

**목표:** `/done`·마감을 안내하기 전에 **live 게이트 상태를 확정**하고, created/null이면 "조치 불필요"로 안내하게 만든다. 아래 A(문서·행동 규칙)와 B(하네스 자동화)를 **함께** 적용 권장 — A는 즉효·저비용, B는 결정론적 근본 차단.

### 수정 A — 절차 규칙 문구 강화 (project-init가 생성하는 `workflow.md` 템플릿)
"verifier ✅ → `/done`" 요약표에 **전제조건과 상태 확인 단계**를 명시한다. 예:

```diff
 ### @verifier 검증 후
-verifier ✅  →  /done
+verifier ✅  →  (먼저 live 게이트 상태 확인: .claude/state/plan_gate.json 의 current_gate_id)
+   • state == approved/verified  →  /done  (정상 마감)
+   • state == created  또는  current_gate_id == null
+        →  /done 안내 금지. "자동 롤오버로 닫히니 별도 조치 불필요"로 안내
+   • 재개 배너의 상태는 스냅샷(stale)일 수 있으니 안내 직전 live 재조회
```

### 수정 B — 하네스 자동화 (권장, 결정론적)
모델의 판단에 의존하지 않도록 **하네스가 상태에 맞는 다음 액션을 직접 알려준다.** 두 지점을 손본다.

**B-1. Stop 훅 `hooks/plan_gate_stop_alert.py` — created 게이트에 긍정 신호 추가**
현재는 `gate.state != "approved"`면 무출력(침묵)이라, 모델이 신호 부재를 문서 규칙으로 메꾼다. 편집이 발생한 **created** 게이트에 한해, "마감 불필요" 힌트를 1회 emit하면 모델의 `/done` 지어내기를 선제 차단한다.
```
# 편집이 있었던 created 게이트 + (선택)docs/.verifier_result.json ✅ 존재 시:
"[plan-gate] ℹ️ 이 작업은 미승인(created) 게이트입니다.
  다음 편집 시 자동 롤오버로 닫히므로 /done 입력은 불필요합니다.
  (명시 마감을 원하면 /done 도 '무조건 마감'으로 동작하나 필수 아님.)"
```
> 주의: 기존 dedup 패턴(`verifier_advisory_seen_at_edit`, `stop_hook_active` 억제)을 그대로 따라 **편집 배치당 1회**만 출력해 무한 턴 연장·노이즈를 방지할 것.

**B-2. SessionStart 배너 `hooks/plan_gate_session_start.py` — stale 경고 명시**
재개 배너에 "이 상태는 재개 시점 스냅샷 — live 아님, 조치 전 재조회" 한 줄을 추가한다. created 분기의 안내도 `/approve-plan` 그대로 유지하되 stale 주의를 붙인다.

**B-3. (선택) `plan_gate_cli.py`에 읽기전용 `status` 서브커맨드 제공**
`plan_gate_cli.py status` → `current_gate_id`, `state`, "권장 다음 액션"을 한 줄 JSON/텍스트로 출력. 모델·훅이 안내 직전 **싸게 live 조회**할 수 있는 공식 창구를 만든다. (현재는 모델이 JSON을 직접 파싱해야 함.)

### 수정 우선순위
1. **A** (즉시, 문서 한 곳) — 오적용의 직접 차단.
2. **B-1** (핵심) — 하네스가 상태 신호를 능동 제공 → 모델 추론 의존 제거.
3. **B-2 / B-3** (보강) — stale 착각·조회 비용 완화.

---

## 7. 검증 방법 (수정 후 재현 테스트)

1. `/approve-plan` **없이** 파일 소편집 1건 → created 게이트 생성 확인 (`plan_gate.json.current_gate_id`의 state=created).
2. `@verifier` 통과.
3. **기대:** 에이전트가 `/done`을 요구하지 **않고** "자동 롤오버로 닫히니 조치 불필요"로 안내. (B-1 적용 시 Stop 훅이 해당 ℹ️ 1회 emit.)
4. 새 편집 1건 → 이전 created 게이트가 `closed_at` 기록되며 자동 롤오버, 새 게이트 생성 확인.
5. `plan_gate_cli.py done`을 임의 호출해도 created/null 상황에선 "활성 gate 없음"이 **정상**임을 문서로 합의.

**회귀 가드:** approved 게이트(정상 `/approve-plan` 경로)에서는 종전대로 verifier ✅ → `/done` 안내가 유지되는지 확인(수정이 approved 경로를 건드리지 않아야 함).

---

## 8. 부록 — 원본 데이터

### 8.1 `plan_gate.json` 핵심 필드 발췌 (문제 게이트들)
```json
"…595e9e": { "state":"done","approved_at":null,"verifier_status":null,
             "created_at":"…03:14:15.061","closed_at":"…03:26:42.463" },   // 재개 배너에 뜬 게이트
"…94357a": { "state":"done","approved_at":null,"verifier_status":null,
             "unique_files":["…\\src\\tray.py","…\\src\\app.py"],
             "created_at":"…03:26:42.468","closed_at":"…03:30:30.801" },    // 이번 트레이 작업
"current_gate_id": null
```

### 8.2 Stop 훅의 "침묵" 로직 (원본 인용, `plan_gate_stop_alert.py`)
```python
state = lib.load_state(root)
gate = lib.current_gate(state)
if gate is None or gate["state"] != "approved":
    return 0        # ← created/verified/null 게이트엔 아무것도 출력하지 않음
```
→ 하네스는 created 게이트에 `/done`을 권고하지 않았다. `/done` 추천은 전적으로 에이전트 측 과적용.

### 8.3 SessionStart 배너의 created 분기 (원본 인용, `plan_gate_session_start.py`)
```python
elif g_state == "created":
    ...
    "  아직 계획 미승인 상태입니다. tasks/todo.md 작성 후 /approve-plan."
```
→ 배너의 created 안내는 `/approve-plan`. 에이전트가 말한 `/done`은 배너에도 근거가 없다.

### 8.4 하네스 파일 맵 (v2.15.0, `…/project-init/2.15.0/hooks/`)
| 파일 | 역할 | 이 이슈에서 |
|------|------|------------|
| `plan_gate_lib.py` | 상태 로드/저장·게이트 전이·롤오버 코어 | 롤오버·current_gate 소스 |
| `plan_gate_session_start.py` | 재개 배너 | **B-2 수정 대상** (stale 경고) |
| `plan_gate_stop_alert.py` | Stop 훅 리마인더 | **B-1 수정 대상** (created 긍정 신호) |
| `plan_gate_cli.py` | `/approve-plan`·`/done`·`/replan` 등 CLI | **B-3 수정 대상** (`status` 추가) |
| `plan_gate.py` / `plan_gate_bash.py` | Edit/Bash PreToolUse 게이트 | (직접 수정 없음) |

---

### 부록 Z — 핸드오프 메모 (원 프로젝트 절차 반영용 체크박스)
- [ ] `.claude/memory/workflow.md`(수정 A) 반영 — 원 프로젝트/템플릿 양쪽
- [ ] `.claude/memory/lessons.md` "하네스 관련 패턴" 테이블에 1행 추가
- [ ] 하네스 레포에서 B-1/B-2/B-3 이슈화

---

## 9. 처리 결과 (하네스 레포 조치 완료 — 2026-07-03)

> 이 리포트는 하네스 레포(claude 마켓플레이스)에서 검토·조치 후 `complit/`으로 아카이브됨.
> 조치 시점 하네스 버전: v2.16.1 → **v2.16.2** (commit `2d47db4`, tag `v2.16.2`).

**리포트가 v2.15.0 기준이라, 그 사이 릴리스에서 일부 제안이 선반영돼 있었음:**

- **B-3 (읽기전용 `status` 서브커맨드) → 이미 구현됨.** `plan_gate_cli.py:535 cmd_status` 존재, `_NEXT_ACTION` 매핑으로 "권장 다음 액션"까지 출력(created → `tasks/todo.md 작성 → /approve-plan`). 별도 조치 불요.
- **수정 A가 지목한 템플릿 `workflow.md` → 항상 로드 레이어는 이미 프레이밍됨.** 현재 템플릿이 `trivial → approve·done 둘 다 불요(자동 롤오버)`를 상시 주입(progressive-disclosure 리팩터 `ed1902e`에서 반영). 리포트가 인용한 옛 표는 원 프로젝트의 구버전 생성본.

**실제로 조치한 것 (수정 A — 진짜 남아있던 갭):**

- **`plugins/project-init/skills/plan-gate-help/SKILL.md` "@verifier 검증 후" 표에 전제조건 추가.** progressive-disclosure로 이 표가 스킬로 옮겨졌으나 여전히 무전제였음. live 게이트 상태 확인 단계 + `created`/`current_gate_id==null`이면 `/done` 안내 금지(자동 롤오버) + stale 배너 경고를 명시. 서브에이전트 행위 검증으로 created↔approved 분기가 올바르게 갈리는 것 확인.

**보류/기각 (추가 논의 후 결정):**

- **B-1 (Stop 훅 created 긍정 신호) → 재발 시 승격 조건부 보류.** 서브에이전트 3자 토론(①보류/②배너/③Stop훅) 결과: 실패가 데이터 안전·자기교정(무시로 종료)·1회 관찰이고, **항상 로드되는 템플릿 + on-demand 수정 A로 방어가 2겹**이라 Stop 훅의 turn-extension 예산·"created 침묵" 올바른 설계를 깨는 비용이 심각도에 불비례. 결정 규칙: **재발 시 — 재개 있는 세션 주도면 ②(배너 보강), 재개 없는 장기 단일 세션 주도면 ③(Stop 훅) 재검토.**
- **B-2 (stale 배너 경고) → 수정 A에 흡수.** 별도 배너 수정은 B-1과 함께 보류.
