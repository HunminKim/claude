# 하네스 수정 레포트 — plan-gate 게이팅 불일치 + 무가드 플래그 생성

- **대상**: claude (하네스 본체) — `plugins/project-init/hooks/`
- **점검일**: 2026-06-16 (KST)
- **판정**: ❌ 구조적 버그 (every install 재현)
- **트리거**: 비-project-init 디렉토리(홈)에서 plan-gate가 "켜졌지만 `/done`으로 못 닫는" 데드락 관측
- **진단**: harness-check 위임 — general-purpose 2종 병렬 (가드 조건 대조 + 플래그 생성 경로 추적), 코드 file:line 확정

---

## 발견된 문제 요약

| # | 문제 | 심각도 | 유형 |
|---|------|--------|------|
| 1 | **게이팅 조건 불일치** — 강제는 `plan_gate_enabled`, CLI는 `verifier.md` 요구 → 켜졌지만 못 닫는 데드락 (in-band 복구 불가) | ❌ | 구조적 |
| 2 | **무가드 플래그 생성** — project-init 스킬/post-compact가 cwd 상대경로로 `plan_gate_enabled`를 가드 없이 생성 → 엉뚱한 디렉토리에 활성화 | ⚠️ | 구조적 |

---

## 문제 1 — 게이팅 조건 불일치 (데드락)

`plan_gate_lib.py`에 **독립된 두 술어**가 있고, 강제/관리가 서로 다른 걸 호출한다:
```python
# plan_gate_lib.py:78-80
def is_project_init_managed(root): return (root/".claude/agents/verifier.md").exists()
# plan_gate_lib.py:83-87
def is_plan_gate_enabled(root):     return (root/".claude/plan_gate_enabled").exists()  # "verifier.md와 독립적으로 on/off 가능"
```

| 진입점 | 활성 조건 | file:line |
|--------|-----------|-----------|
| plan_gate.py (강제, Edit/Write) | `is_plan_gate_enabled` | plan_gate.py:108 |
| plan_gate_bash.py (강제, Bash) | `is_plan_gate_enabled` | plan_gate_bash.py:39 |
| plan_gate_session_start.py | `is_plan_gate_enabled` | :33 |
| plan_gate_stop_alert.py | `is_plan_gate_enabled` | :91 |
| **plan_gate_cli.py (관리: done/off/rollback…)** | **`is_project_init_managed`** | **plan_gate_cli.py:565-569** |

**데드락 (총체적)**: CLI 가드는 `main()` 의 서브커맨드 디스패치(:572) **앞**에 있어서 `done`·`rollback`·**`off` 전부 차단**. 특히 플래그를 지울 `cmd_off`(:375-381)조차 가드 뒤라 도달 불가 → **in-band 복구 경로 0**. 유일한 탈출은 수동 `rm .claude/plan_gate_enabled`.

### Upstream 수정 (권장: (b) 통일)
**영향 파일**: `plugins/project-init/hooks/plan_gate_cli.py` + `plan_gate_lib.py`

- `plan_gate_lib.py`에 단일 활성 술어 추가 → 5개 진입점이 전부 이걸 호출 (재발 방지):
  ```python
  def plan_gate_active(root): return is_plan_gate_enabled(root)
  ```
- `plan_gate_cli.py:565-569`의 `is_project_init_managed` 가드를 `is_plan_gate_enabled`(=`plan_gate_active`) 기준으로 교체. → 강제와 관리가 같은 조건으로 통일, 켜진 게이트는 항상 닫힌다.
- verifier.md 의존이 꼭 필요한 곳은 `cmd_done`의 verifier 핸드셰이크(:195-204)뿐 — 거긴 이미 `/skip-verify`로 부재 처리. 최상위 가드에서 verifier.md 요구는 anomaly이므로 제거.

**근거**: 1파일 변경, 문서화된 설계 의도(lib:85 "verifier.md와 독립")와 일치, in-band 복구 회복. (a)강제측에 verifier.md 요구는 설계 모순+4파일 변경이라 기각.

---

## 문제 2 — 무가드 플래그 생성 (엉뚱한 디렉토리 활성화)

`plan_gate_enabled` 생성 사이트 중 **가드 없는 cwd-상대경로**가 위험:

| 사이트 | 가드 | 경로 |
|--------|------|------|
| `plan_gate_cli.py:366-372` cmd_on (`/plan-gate-on`) | ✅ `is_project_init_managed` | 앵커됨 — **안전** |
| **SKILL.md:179** (project-init step 8) | ❌ 없음 | **상대 `.claude/plan_gate_enabled`** — cwd 의존 |
| **post-compact.py:67** (template, restore_plan_gate) | ❌ 없음 (off 마커만 확인) | compact마다 자동 재생성 |
| harness-update SKILL.md:199 | ❌ 없음 | 상대 touch |

**핵심**: `/plan-gate-on` *커맨드*는 제대로 가드되지만, *스킬*은 파일을 직접 써서 그 가드를 우회한다. 홈 디렉토리에 플래그가 생긴 건 project-init 스킬이 `CLAUDE_PROJECT_DIR=홈`인 상태로 (또는 step 8이) cwd 상대경로로 생성했기 때문 (history.jsonl에 `/project-init:done` @홈 흔적).

### Upstream 수정
- **SKILL.md step 8**: 플래그 생성 전 **대상 절대 루트를 확인/에코**하고, `$HOME`·비빈 시스템 디렉토리 초기화는 사용자 확인 없이 거부. 상대경로 대신 확인된 절대 루트에 생성.
- **post-compact.py:55-70 restore_plan_gate**: `enabled.touch()` 전에 `(root/".claude/agents/verifier.md").exists()` 가드 추가 (CLI:565 패턴 미러). 안 하면 verifier.md 잃은 프로젝트가 compact마다 플래그 부활.

---

## 테스트 갭 (동반 추가 필수)

`tests/smoke_test.py`: 표준 픽스처 `make_project()`(:74-83)는 `plan_gate_enabled`만 만들고 verifier.md는 안 만든다. 그런데 **모든 CLI 테스트가 verifier.md를 일부러 추가**(:554, 622, 1110 "CLI is_project_init_managed 통과용")해서 **불일치가 테스트에 안 보인다.**

→ 추가할 테스트: `plan_gate_enabled` 有 + `verifier.md` 無 디렉토리에서 게이트 생성 후 `/done`(또는 `/plan-gate-off`)가 **exit 0 로 게이트를 닫는지** assert (= 켜진 게이트는 항상 in-band로 닫힌다).

---

## 즉시 조치 (이 세션)

- [x] 홈의 stray `plan_gate_enabled` + 게이트 상태 수동 제거 (완료)
- [ ] 문제 1·2 upstream 수정 + 테스트 추가 → project-init 버전 번프 → smoke_test → 커밋·태그
- [ ] (선택) compose 스캐폴드 작업과 함께 묶어 하나의 project-init 개선으로 릴리스
