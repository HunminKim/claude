# 하네스 체크 리포트 — harness-check v1.0.2 (2026-07-08 KST)

> 프로젝트: **moduops-platform**
> 스킬: `harness-check` v1.0.2 (`/home/win11/.claude/plugins/cache/hunminkim/harness-check/1.0.2/`)
> 실행 시각: 2026-07-08 19:04 KST
> 진단 주체: harness-inspector 서브에이전트(독립 진단, 발견만·수정 안 함)
> 호출 사유: plan-gate **approve 평문 입력의 비결정적 등록 실패**(동일 `approve-plan` 1차 실패→2차 성공) 원인 파악

---

## 판정: ✅ 하네스 정상

harness-inspector 기준 ❌/⚠️ 트리거 없음. approve 비결정성은 **하네스 결함이 아니라 의도된 rearm 설계 + 사용 패턴**에서 비롯한 실사용 함정으로 확인됨. 판정 ✅이므로 upstream 수정 레포트(스킬 2단계)는 생성하지 않음.

### 점검 항목

| 항목 | 상태 | 비고 |
|---|---|---|
| `.claude/agents/verifier.md` | ✅ | 존재 |
| PostToolUse 훅 wiring | ✅ | 플러그인 `hooks.json` PostToolUse:Write → `update_docs.py` + `plan_summary_request.py` |
| verifier 결과 반영 wiring | ✅ | `${CLAUDE_PLUGIN_ROOT}/hooks/update_docs.py`(플러그인 내부 상주 — 프로젝트 경로에 없는 게 정상) |
| `docs/.verifier_result.json` 잔존 | ✅ 없음 | 훅 정상 소진(스턱 없음) |
| verifier.md 스키마 `checklist_phase`/`checklist_row` | ✅ | 163-164행 정의 존재 |
| `docs/checklist.md` / `completion_report.md` / `technical_doc.md` | ✅ | 모두 존재 |
| CLAUDE.md `알려진 버그 / 제약` 섹션 | ✅ | 존재 |
| completion_report ↔ checklist 정합 | ✅ | 완료 9 vs 리포트 ~10 (차이 <2) |

### checklist 현황
- Phase 1(ingest→EAV) 8/8 ✅ · Phase 2(스냅샷 API) 1/1 ✅ · Phase 3(마무리/배포) 0/3 미완
- 전체 12행 / 완료 9 / 미완 3 (미완은 전부 Phase 3)

---

## ★ approve 평문 "1차 실패 → 2차 성공" 비결정성 — 원인 분석

### 결론
비결정성의 근원은 토큰 이름(`approve` vs `approve-plan`)이 **아니다** — 둘은 코드상 완전히 동일 경로다. 진짜 원인은 **`cmd_approve`의 "todo.md 해시 불일치 시 실패시키되 저장 해시를 현재값으로 재무장(rearm)"** 로직이다. gate 발동 후 todo.md가 편집되면 첫 approve는 실패하고, 그 실패가 다음 시도를 위해 스스로 해시를 갱신해 두 번째 동일 approve가 통과한다.

### 코드 근거 — `plan_gate_cli.py` `cmd_approve` (117-133행)
```python
expected = gate.get("todo_md_sha256")
current_sha, current_mtime = lib.hash_todo_md(root)
if expected and current_sha != expected:
    _err("[plan-gate approve] tasks/todo.md가 gate 발동 후 변경됨. ... 다시 /approve-plan 을 입력하면 통과됩니다.")
    gate["todo_md_sha256"] = current_sha   # ← 실패하면서 새 해시로 갱신(rearm)
    gate["todo_md_mtime"]  = current_mtime
    lib.save_state(root, state)
    return 1                                # ← 1차: 실패 (gate created 유지)
lib.transition(gate, "approve_manual")      # ← 2차: 해시 일치 → approved
```
주석(129행)이 명시: *"새 해시로 갱신해 두 번째 /approve-plan에서는 통과시킨다(사용자 의도 추정)."*

### state 실증 (진단 시점 활성 gate)
- `created_at` = gate 발동 시 todo.md 해시 캡처
- `todo_md_mtime` = **gate 발동 후 재편집 시각** (발동보다 나중)
- `approved_at` = 편집 직후 통과
- `todo_md_sha256` = **현재 라이브 todo.md 해시와 정확히 일치**(= 편집 후 해시)
→ 저장 해시가 "발동 시점"이 아니라 "편집 후"로 남아 있음 = rearm이 일어난 전형적 흔적. 사용자 보고(created 유지 후 2차 성공)와 정확히 부합.

### approve 실패/통과 조건 (요약)
- gate 없음/`done`/`rolled_back` → 선승인(즉시 approved, 해시검사 없음)
- gate `approved` → idempotent 통과
- gate `created` + 저장해시 有 + **현재 todo.md 해시 ≠ 저장해시** → **실패 + 해시 rearm**
- gate `created` + 해시 일치(또는 저장해시 無) → 통과

### 평문 fallback (`plan_approval.py` `_dispatch_token`)
- `_ACTION_TOKENS`에 `approve-plan`·`approve` 둘 다 매핑 → **기능 동일**
- 감지: `prompt.split()[0]`을 `strip_command_prefix`로 정규화 → 토큰 정확일치 + **인자 없을 때만** 실행. "approve the plan" 등 문장형은 무발화
- **CLI 실패(exit 1) 안내가 stderr(`_err`)로만 나감** → UserPromptSubmit stderr는 사용자·모델 컨텍스트에 잘 안 뜸 → **원격/모바일에서 "왜 실패했는지 안내가 안 보이고 다시 치니 됨"** 으로 체감

### 슬래시 vs 평문 — 구조적 차이는 "가시성"이지 "성패"가 아님
- `commands/approve.md`·`approve-plan.md` 둘 다 `plan_gate_cli.py approve` 실행(`disable-model-invocation`, 사용자 전용)
- 슬래시는 CLI stdout/stderr 노출 → 실패 사유가 보여 자연히 재입력 → "확실히 된다"고 체감
- 평문은 stderr가 묻힘. **성패 판정은 양쪽 모두 동일한 `cmd_approve` 해시검사에 종속** — 슬래시라도 todo.md가 발동 후 바뀌면 1차 실패는 동일

### "approve=실패, approve-plan=성공" 비대칭의 실제 설명
두 토큰은 코드상 완전 동일 → 코드로 재현 불가. 가장 개연성 높은 설명: `approve` 먼저 시도(1차 실패+rearm) → 이어 `approve-plan` 시도(이미 rearm되어 통과). **토큰 이름은 상관변수(confound)**, 독립변수는 "몇 번째 시도인가 / todo.md가 발동 후 바뀌었는가".

---

## 재발 방지

### 즉시(이 프로젝트 워크플로우 — 적용됨, lessons.md 기록)
- approve **요청 전에 todo.md를 확정**하고, 요청~승인 사이 todo.md를 **절대 편집하지 않는다** → 첫 approve가 바로 통과
- 부득이 편집했으면 "todo가 바뀌어 첫 approve는 실패하니 한 번 더 눌러달라"고 **미리 고지**
- `/approve-plan`이 안 먹으면 rearm된 상태이므로 **동일 입력 재시도**

### upstream 후보 (project-init 플러그인 — 발견만, 미구현)
1. **1차 실패 안내를 가시 채널로 승격** — `_err`(stderr) → `hookSpecificOutput.additionalContext`(stdout JSON)로도 emit. 모바일에서 "왜 실패, 재입력하면 됨"이 즉시 보이게
2. **rearm을 명시적 카운트다운으로** — "todo가 바뀌었습니다. 승인하려면 한 번 더 approve 하세요 (1/2)" (동작 변경이므로 사용자 결정 사항)
3. **평문 감지 강건화** — 문장형 무발화 시 "감지 안 됨, `/approve-plan` 입력" 안내를 UserPromptSubmit context로
4. **원격 승인 확인 루프** — approve 직후 gate state 자동 회신(성공/실패 즉시 확인)

> 1·4는 가시성 보강이라 부작용 낮음. 2는 승인 동작 자체 변경이라 사용자 결정 필요.

---

## 참고 파일 (절대경로)
- CLI 로직: `/home/win11/.claude/plugins/cache/hunminkim/project-init/2.17.0/hooks/plan_gate_cli.py` (`cmd_approve` 102-146행, rearm 117-133행)
- 평문 fallback: `.../hooks/plan_approval.py` (`_dispatch_token` 117-157행, `_ACTION_TOKENS` 43-53행)
- 해시/정규화 SSOT: `.../hooks/plan_gate_lib.py` (`hash_todo_md`, `strip_command_prefix`)
- 훅 wiring: `.../hooks/hooks.json` (UserPromptSubmit, PostToolUse:Write)
- 슬래시 정의: `.../commands/approve.md`, `.../commands/approve-plan.md`
- 실증 state: `moduops-platform/.claude/state/plan_gate.json`, `.../plan_gate_audit.log`
