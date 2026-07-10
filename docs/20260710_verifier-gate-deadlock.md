# verifier ✅ 인데 `/done` 이 영구 거부되는 데드락

- **발견일**: 2026-07-10 (KST)
- **버전**: plugin `2.17.4` (`plugins/project-init/.claude-plugin/plugin.json`)
  — 캐시된 `2.17.3` 과 `hooks/update_docs.py`·`hooks/plan_gate_cli.py` 는 **바이트 동일**
- **영향 파일**: `plugins/project-init/hooks/update_docs.py`, `plugins/project-init/hooks/plan_gate_cli.py`
- **심각도**: 중 — 정상 경로로 게이트를 닫을 수 없고, 유일한 출구(`/skip-verify`)가 **사실과 다른 기록**을 남긴다

---

## 요약

`update_docs.py` 는 `docs/.verifier_result.json` 을 읽어 문서를 갱신하고 plan-gate 를 `verified` 로 올린 뒤,
**`finally` 로 결과 파일을 무조건 삭제**한다. 그런데 gate 갱신은 두 가지 조건에서 조용히 건너뛰어진다.

`plan_gate_cli.py done` 은 gate 가 갱신되지 않았을 때 그 결과 파일을 다시 읽어 복구하려 한다
(`_recover_verifier_from_file`). **하지만 파일은 이미 지워졌다.** 복구는 항상 실패한다.

결과: verifier 가 실제로 ✅ 를 냈고 문서에도 그렇게 기록됐는데, `/done` 은 "verifier 미검증 — 완료 불가" 로
영구 거부한다. 사용자는 `/skip-verify` 밖에 쓸 수 없고, gate 에는 `verifier_status=⏭️`(검증 생략)이 남는다.
**검증은 수행됐는데 기록은 "생략"이 된다.**

에러도 경고도 출력되지 않는다. 문서는 정상 갱신되므로 사용자는 성공한 줄 안다.

---

## 실제 증상 (2026-07-10, daesung 프로젝트)

1. verifier 서브에이전트가 검증을 수행하고 `docs/.verifier_result.json` 을 `Write` 툴로 생성 — **판정 ✅**
2. `docs/completion_report.md` 가 정상 갱신됨 (09:23:28, 검증 명령 12개와 출력 전문 포함)
3. `docs/.verifier_result.json` 은 사라짐
4. `.claude/state/plan_gate.json` 의 gate 는 `state=approved`, `verifier_status=None` — **갱신 안 됨**
5. `/done` → `[plan-gate done] verifier 미검증 — 완료 불가.`
6. verifier 를 재개시켜 결과 파일을 다시 쓰게 했으나 훅이 또 삭제 → 동일 실패
7. `/skip-verify` 로만 마감 가능. gate 기록은 `verifier_status=⏭️`

---

## 원인 (코드)

### 결함 1 — gate 갱신 실패해도 결과 파일을 삭제한다

`update_docs.py:87-100`

```python
    try:
        return _process(result_path.parent, result)
    except Exception as e:
        _log(f"[update_docs] ⚠️ 결과 처리 실패(스키마 이탈?): {e} — 문서 자동화 건너뜀")
        return 0
    finally:
        try:
            result_path.unlink()          # ← gate 갱신 성공 여부와 무관하게 무조건 삭제
            _log("[update_docs] .verifier_result.json 삭제 완료")
        except OSError:
            pass
```

`_process()` 안에서 gate 갱신이 **조건 미달로 건너뛰어져도** (예외가 아니므로) `finally` 는 그대로 파일을 지운다.

### 결함 2 — 복구 경로가 삭제된 파일에 의존한다

`plan_gate_cli.py:167-174`

```python
def _recover_verifier_from_file(root, gate, state) -> bool:
    """update_docs.py가 gate 업데이트를 놓쳤을 때 verifier_result.json에서 직접 복구."""
    result_path = _Path(root) / "docs" / ".verifier_result.json"
    if not result_path.exists():
        return False                       # ← 결함 1 때문에 항상 여기로 온다
```

`cmd_done` (`plan_gate_cli.py:235-244`) 은 이 복구가 실패하면 곧장 "verifier 미검증" 으로 거부한다.
즉 **결함 1 이 결함 2 의 유일한 안전망을 파괴한다.**

### 결함 3 — gate 갱신이 조용히 건너뛰어지는 두 조건

`update_docs.py:425-435`

```python
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import plan_gate_lib as _pglib

        _root = _pglib.find_project_root()
        if _root and _pglib.is_project_init_managed(_root) and verdict in ("✅", "❌"):
            _state = _pglib.load_state(_root)
            _gate = _pglib.current_gate(_state)
            if _gate and _gate["state"] in ("approved", "verified"):
                _pglib.enter_verified(_gate, verdict)
                _pglib.save_state(_root, _state)
```

**조건 (a) `verdict in ("✅", "❌")`** — `verdict` 는 `update_docs.py:216` 에서 정규화 없이 읽는다:

```python
    verdict = result.get("verdict", "❓")
```

verifier 는 LLM 이다. `"✅ 통과"`, `"✅ PASS"` 처럼 한 글자라도 덧붙이면 이 조건이 깨진다.
**문서 갱신 로직은 `verdict == "✅"` 비교를 쓰지 않는 경로가 있어 정상 동작하므로**, 문서만 갱신되고 gate 는 누락된다.

**조건 (b) `_root = _pglib.find_project_root()`** — `plan_gate_lib.py:74-83`

```python
def find_project_root() -> Path | None:
    """CLAUDE_PROJECT_DIR 우선, 없으면 cwd 상위에서 .claude/를 찾는다."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".claude").exists():
            return parent
    return None
```

문서 갱신은 `_process(result_path.parent, ...)` — **결과 파일의 절대경로**를 쓴다.
gate 갱신은 `find_project_root()` — **환경변수 또는 cwd**를 쓴다.
두 경로가 다르다. `CLAUDE_PROJECT_DIR` 이 없고 훅 프로세스의 cwd 가 프로젝트 밖이면
`_root` 가 `None` 이 되어 gate 갱신만 건너뛰어진다. 서브에이전트가 verifier 를 수행하는 경우 특히 위험하다.

### 결함 4 — 실패가 무음이다

위 두 조건 중 무엇으로 건너뛰든 `_log` 호출이 없다. stdout·stderr 어디에도 흔적이 없다.
문서는 갱신되므로 사용자와 Claude 모두 성공한 줄 안다. `/done` 을 눌러야 비로소 드러난다.

---

## 재현

프로젝트를 오염시키지 않는 격리 재현이다. `/tmp` 아래에 가짜 프로젝트를 만든다.

```bash
R=/tmp/hookrepro; rm -rf "$R"; mkdir -p "$R/proj/.claude/agents" "$R/proj/.claude/state" "$R/proj/docs"
H=<repo>/plugins/project-init/hooks
PROJ="$R/proj"

echo "stub" > "$PROJ/.claude/agents/verifier.md"      # is_project_init_managed 통과용
touch "$PROJ/.claude/plan_gate_enabled"
printf '# 완료 보고서\n\n' > "$PROJ/docs/completion_report.md"

python3 - "$PROJ" <<'PY'
import json, sys, pathlib
gid = "plan-gate_test_0001"
state = {"current_gate_id": gid, "gates": {gid: {
    "id": gid, "state": "approved", "created_at": "2026-07-10T00:00:00+00:00",
    "approved_at": "2026-07-10T00:10:00+00:00", "verified_at": None,
    "verifier_status": None, "edit_count": 3}}}
(pathlib.Path(sys.argv[1])/".claude/state/plan_gate.json").write_text(json.dumps(state, indent=2))
PY
```

결과 파일을 쓰고 훅을 호출한다. `verdict` 값과 `CLAUDE_PROJECT_DIR` 유무만 바꿔 세 번 돌린다.

```bash
write_result() {  # $1=verdict
  python3 - "$PROJ" "$1" <<'PY'
import json, sys, pathlib
res = {"feature_name": "repro", "timestamp": "2026-07-10 09:40 KST",
       "task_type": "config", "verdict": sys.argv[2], "failure_category": None,
       "test_items": [{"item": "버전", "result": "✅", "method": "production_exec"}],
       "issues": [], "code_smells": [], "critical_constraints": [],
       "side_effects": {"created_paths": [], "cleanup_status": "none", "production_writes": False},
       "evidence": "e", "implementation": {"description": "d", "logic": "l", "files": []}}
(pathlib.Path(sys.argv[1])/"docs/.verifier_result.json").write_text(json.dumps(res, ensure_ascii=False))
PY
}
HOOK_IN='{"tool_name":"Write","tool_input":{"file_path":"'"$PROJ"'/docs/.verifier_result.json"}}'

# A) 정상
write_result "✅";     echo "$HOOK_IN" | (cd "$PROJ" && CLAUDE_PROJECT_DIR="$PROJ" python3 "$H/update_docs.py")
# B) verdict 문자열 이탈
write_result "✅ 통과"; echo "$HOOK_IN" | (cd "$PROJ" && CLAUDE_PROJECT_DIR="$PROJ" python3 "$H/update_docs.py")
# C) root 탐지 실패
write_result "✅";     echo "$HOOK_IN" | (cd /tmp && env -u CLAUDE_PROJECT_DIR python3 "$H/update_docs.py")
```

각 실행 후 `jq '.gates[.current_gate_id] | {state, verifier_status}' "$PROJ/.claude/state/plan_gate.json"` 로 확인.

### 결과

| 케이스 | `verdict` | `CLAUDE_PROJECT_DIR` | 문서 갱신 | gate 상태 | 결과 파일 | stderr 경고 |
|--------|-----------|----------------------|-----------|-----------|-----------|-------------|
| **A** | `"✅"` | 있음 | ✅ 갱신됨 | `verified` / `✅` | 삭제 | 없음 |
| **B** | `"✅ 통과"` | 있음 | ✅ 갱신됨 | **`approved` / `None`** | **삭제** | **없음** |
| **C** | `"✅"` | 없음, cwd 밖 | ✅ 갱신됨 | **`approved` / `None`** | **삭제** | **없음** |

B 와 C 는 **서로 독립된 원인인데 증상이 완전히 같다.** 두 경우 모두 문서는 갱신되고, gate 만 조용히 누락되며,
복구용 파일은 지워진다. 이후 `/done` 은 어떤 방법으로도 통과할 수 없다.

> 실제 사고에서 어느 쪽이 원인이었는지는 **확정하지 못했다.** 결과 파일이 이미 삭제되어
> `verdict` 원문을 확인할 수 없다. 증상은 B·C 양쪽과 일치한다.

---

## 제안 수정

### 1. gate 갱신에 실패하면 결과 파일을 남긴다 (최소 수정, 데드락 해소)

`update_docs.py` 의 `finally` 를 조건부로 바꾼다. `_process` 가 gate 갱신 성공 여부를 반환하게 하고,
실패했으면 파일을 `.verifier_result.json` 그대로 두거나 `.verifier_result.stale.json` 으로 옮긴다.
그러면 `plan_gate_cli.py:_recover_verifier_from_file` 이 제 역할을 한다.

이 한 가지만 고쳐도 `/done` 이 정상 통과한다 — 복구 경로가 이미 존재하기 때문이다.

### 2. `verdict` 를 정규화한다

```python
    _raw_verdict = str(result.get("verdict", "❓")).strip()
    verdict = "✅" if _raw_verdict.startswith("✅") else ("❌" if _raw_verdict.startswith("❌") else "❓")
```

verifier 는 LLM 이라 스키마 이탈이 **정상 시나리오**다 — 이 사실은 `update_docs.py:88-89` 의 주석에도
이미 적혀 있다(`"verifier 는 LLM 이라 스키마 이탈이 정상 시나리오다"`). 같은 방어를 `verdict` 에도 적용한다.

### 3. gate 갱신을 건너뛸 때 경고를 출력한다

```python
        if not _root:
            _log("[update_docs] ⚠️ 프로젝트 루트 탐지 실패 — plan-gate 갱신 건너뜀 (CLAUDE_PROJECT_DIR 미설정?)")
        elif verdict not in ("✅", "❌"):
            _log(f"[update_docs] ⚠️ verdict 파싱 불가({verdict!r}) — plan-gate 갱신 건너뜀")
```

무음 실패가 진단을 가장 어렵게 만들었다. 문서가 갱신되므로 성공으로 오인된다.

### 4. (선택) gate 갱신 경로를 문서 갱신 경로와 일치시킨다

문서는 `result_path.parent`(절대경로), gate 는 `find_project_root()`(env/cwd) 를 쓴다.
결과 파일이 `<root>/docs/.verifier_result.json` 인 것이 전제이므로, `result_path.parent.parent` 를
루트 후보로 먼저 시도하면 결함 3(b) 가 구조적으로 사라진다.

---

## 부수적으로 드러난 것

`/skip-verify` 는 "verifier 판정 전 검증을 생략하고 마감" 하는 커맨드다. 이번 사고에서는
**검증이 실제로 수행되고 ✅ 였는데도** 이것 말고는 출구가 없었다. gate 기록에 `⏭️` 가 남아
사후에 이력을 보면 "검증하지 않고 마감했다" 로 읽힌다. 감사 로그로서 사실과 다르다.

결함 1 만 고쳐도 이 오염은 발생하지 않는다.

---

## 확인한 것 / 확인하지 못한 것

**확인함**
- 세 케이스 모두 격리 환경에서 재현 (위 표)
- 캐시(`2.17.3`)와 저장소(`2.17.4`)의 `update_docs.py`·`plan_gate_cli.py` 는 `diff` 상 동일
- `_recover_verifier_from_file` 은 파일이 있으면 정상 복구한다 (결함 1 이 없으면 데드락도 없다)

- **`CLAUDE_PROJECT_DIR` 은 이 환경에서 설정되지 않는다.** 사고가 난 세션에서 `env | grep '^CLAUDE'` 를 찍어
  확인했다. 설정된 것은 `CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_ENTRYPOINT` 등이고
  `CLAUDE_PROJECT_DIR` 은 없다. 따라서 `find_project_root()` 는 **전적으로 훅 프로세스의 cwd 에 의존**한다.
  결함 3(b) 는 이론적 위험이 아니라 상시 노출된 경로다.

**확인하지 못함**
- 실제 사고의 원인이 B(`verdict` 문자열)인지 C(cwd 기반 root 탐지)인지 — 결과 파일이 삭제되어 원문 확인 불가.
  증상은 양쪽과 일치한다.
- 서브에이전트(`Task`/`Agent`)가 `Write` 를 호출할 때 그 PostToolUse 훅 프로세스의 cwd 가 무엇인지.
  메인 세션의 cwd 와 같다면 C 는 배제되고 B 만 남는다. Claude Code 하네스 내부 동작이라 소스로 확인하지 못했다.
  → **훅 진입부에 `_log(f"cwd={Path.cwd()} root={_root}")` 한 줄을 임시로 넣고 재현하면 즉시 확정된다.**

---

# 정정 및 보강 (2026-07-10, 수정 착수 시점)

> 위 본문은 **발견 시점의 기록**이다. 수정에 들어가며 세 갈래 서브에이전트 토론과 실측으로
> 검증한 결과, 본문의 처방 하나가 틀렸고 결함 둘이 누락돼 있었다. 아래가 실제 채택된 설계다.
> 본문은 이력 보존을 위해 고치지 않았다.

## 정정 1 — 처방 1번만으로는 케이스 B 가 안 풀린다

본문 199-205 행("이 한 가지만 고쳐도 `/done` 이 정상 통과한다 — 복구 경로가 이미 존재하기 때문이다")은
**케이스 B 에 대해 거짓**이다. 복구 함수 `plan_gate_cli.py:179` 가 `verdict not in ("✅","❌")` 라는
**동일한 엄격 비교**를 쓰기 때문이다. 결과 파일을 보존한 채 `/done` 을 돌린 실측:

| 결과 파일의 `verdict` | `/done` |
|---|---|
| `"✅"` | 상태 복구 → rc=0 |
| `"✅ 통과"` | verifier 미검증 → **rc=1** |
| `"✅ PASS"` | verifier 미검증 → **rc=1** |

즉 처방 1번은 **케이스 C 만** 해소한다. 본문은 `_recover_verifier_from_file` 을 "정상 안전망"으로
분류했으나, 이 함수 자체가 결함(결함 5)이다. 정규화는 `update_docs.py` 와 이 복구 함수 **양쪽**에
적용해야 한다.

## 정정 2 — 처방 1번은 그대로 적용하면 데드락보다 나쁜 버그를 연다

`_recover_verifier_from_file` 에는 **gate id·timestamp 대조가 없다.** 낡은 gate A 의 `✅` 결과 파일을
남겨둔 채, 검증한 적이 한 번도 없는 새 gate B 에서 `/done` 을 친 실측:

```
[plan-gate done] verifier_result.json에서 상태 복구: ✅
[plan-gate done] 작업 완료. 체크포인트 정리됨: plan-gate_gateB_9999
→ gateB.state='done'  verifier_status='✅'
→ 복구 후 결과 파일 잔존? True        ← 다음 gate 도 또 소비 가능
```

**검증하지 않은 작업이 남의 판정으로 완료 처리된다.** 파일 보존에는 (a) 복구 성공 시 폐기,
(b) gate 승인 시각보다 오래된 파일 거부, (c) gate 닫힘 시 폐기가 함께 와야 한다.

또한 본문 202 행의 `.verifier_result.stale.json` **리네임 변형은 기각**한다. ① 복구 함수가 파일명을
하드코딩(`plan_gate_cli.py:172`)해 복구가 영영 죽고, ② 템플릿 `.gitignore` 패턴(`docs/.verifier_result.json`)에
매칭되지 않아 커밋 대상이 되며, ③ 훅 매처(`endswith(".verifier_result.json")`)에도 안 걸린다.

## 누락 1 — verdict 이탈은 **실행 grounding 강등을 통째로 우회**한다 (심각도 상향)

본문은 이 결함을 데드락(가용성)으로만 다뤘다. 그러나 `update_docs.py:237` 의 `if verdict == "✅":` 는
"전 항목이 `static` 이면 ❌ 로 강등"하는 **'읽고 통과시키기' 방어선**이다. 같은 `== "✅"` 비교라
문자열이 어긋나면 **강등 자체가 실행되지 않는다.** 전 항목 `method: static`, 면제 사유 없음으로 실측:

| `verdict` | grounding 강등 | 보고서에 기록된 판정 |
|---|---|---|
| `"✅"` | 강등됨 | `❌` |
| `"✅ 통과"` | **우회** | **`✅`** |

gate 가 안 올라가므로 `/done` 은 막히지만, 유일한 출구인 `/skip-verify` 는 `cmd_skip_verify`
(`plan_gate_cli.py:298-333`)에서 `verifier_status` 만 바꿀 뿐 `completion_report.md` 를 **정정하지 않는다.**
재검증도 섹션 키로 append 하므로 오염된 `✅` 섹션은 남는다. 즉 **검증하지 않은 작업이 ✅ 로
감사 문서에 영구히 남는다.**

본문 "부수적으로 드러난 것"(`⏭️` 오염)은 **보수적 방향**(실제보다 적게 기록)이라 덜 위험하다.
진짜 피해는 이 **관대한 방향**의 무음 오염이다. → 심각도 **중 → 상**.

## 누락 2 — 상류 원인은 `verifier.md` 스펙 자신이다

verifier 는 LLM 인데, 스펙이 두 표기를 동시에 제시한다:

```
verifier.md:136   ### 판정: ✅ 통과 / ❌ 실패      ← 산문 형식이 "✅ 통과" 를 정식 문구로 씀
verifier.md:193   "verdict": "✅",                 ← JSON 예시는 맨몸 한 글자
```

방금 쓴 보고서 헤더의 문구를 JSON 으로 옮기는 것은 실수가 아니라 스펙이 유도한 결과다.
케이스 B 는 희귀 사고가 아니라 개연성 높은 경로다.

## 누락 3 — 훅 입력의 `cwd` 계약 필드를 아무도 안 읽는다

본문 결함 3(b)의 해법으로 `result_path.parent.parent` 를 제안했으나, verifier 는 `docs/.verifier_result.json` 을
**상대 경로**로 쓰도록 지시받는다(`verifier.md:180`). 상대 경로면 `parent.parent` 가 `Path('.')` 로 무너져
결함이 그대로 남는다. 실제 해법은 **훅 입력 JSON 의 `cwd` 필드**(모든 훅 이벤트에 제공되는 계약 필드)로
경로를 절대화한 뒤 루트를 유도하는 것이다. `update_docs.py:60-77` 은 이 필드를 읽지 않고 있었다.

## 채택된 설계

1. **`verifier.md` 스펙 정정** — 산문 헤더의 `✅ 통과` 제거 + "verdict 는 정확히 한 글자" 명시.
   ⚠️ 서브에이전트 A/B 로 행위 검증했으나 **드리프트를 재현하지 못했다(n=1, 양쪽 모두 `"✅"`)**.
   근거 있는 방어이되 **효과는 미입증**이다. 실제 방어는 아래 2번이다.
2. **`plan_gate_lib.normalize_verdict()` 단일 출처** — `update_docs` 와 복구 함수가 공유.
   조건 없는 통과(`✅`, `✅ 통과`, `✅ PASS`)만 `✅`, 실패는 관대하게 `❌`,
   조건부·혼재(`✅ (일부 항목 실패)`, `✅/❌ 혼합`)는 `❓` 로 **전이 금지**.
   → `startswith("✅")` 단순 정규화는 **기각**했다. 조건부 통과를 무음 승격시키는 fail-open 이다
   (실측: 세 문자열 전부 `verified/✅` 로 올라갔다). 검증 게이트에서 모호함은 자동 통과도 자동 실패도 아니다.
3. **소비자 소유 삭제** — `_process` 가 `delete_ok` 를 반환. gate 가 판정을 소비했거나 소비할 gate 자체가
   없을 때만 삭제. 소비 가능한 gate 가 있는데 전이하지 못했으면 보존한다.
   보존된 파일은 `/done` 복구가 소비하며 폐기하고, gate 가 닫히면 `cleanup_checkpoint`(= `do_gate_done`·
   `rollback` 의 공통 지점)가 폐기한다. `/skip-verify`·`/rollback` 도 한 지점으로 덮인다.
4. **stale 가드** — 결과 파일 mtime 이 gate 승인보다 오래되면 복구를 거부한다 (정정 2).
5. **결정론 root** — 훅 입력 `cwd` 로 절대화 → `docs_dir.parent`(+`is_project_init_managed` 가드)
   → 실패 시 `find_project_root()` 폴백. 문서 갱신과 gate 갱신이 같은 루트를 공유한다.
6. **무음 실패 제거** — 루트 미탐지·gate 부재·verdict 파싱 불가를 각각 stderr 로 경고한다.

## 아직 남은 것

- **verifier 의 `tools` 에 `Bash` 가 있다**(`verifier.md:5`). heredoc 으로 `.verifier_result.json` 을 쓰면
  `Write` 매처가 안 걸려 훅이 **아예 돌지 않는다.** 문서도 gate 도 미갱신이라 이번 사고와는 구별
  가능(증상 #2 는 문서가 갱신됐다)하지만, 별개의 잠복 갭이다.
- 실제 사고의 원인이 B 인지 C 인지는 여전히 확정하지 못했다. 다만 이제 **둘 다 막힌다.**

회귀 방지: `tests/smoke_test.py` `[52] verifier gate 데드락` (어서션 23개).
