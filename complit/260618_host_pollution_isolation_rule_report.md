# 하네스 수정 레포트 — 호스트 오염 방지 / 격리 실행 유도 규칙 부재

- **대상**: claude (하네스 본체) — `plugins/project-init/`
- **점검일**: 2026-06-18 (KST)
- **판정**: ⚠️ 구조적 갭 (규칙 자체가 없음)
- **트리거**: 메인 Claude 가 영상 합치기용 ffmpeg 를 **호스트 `~/.local/bin` 에 static 설치**하려 함 → 사용자가 "로컬 더럽히지 말고 도커/venv로" 수동 교정. 하네스에 이를 유도하는 규칙이 없어 호스트 오염 경로를 자연 선택.
- **진단**: harness-check 위임 — claude-code-guide(Claude Code best-practice) + general-purpose(하네스 전수 감사) 병렬

---

## 발견된 문제 요약

| # | 문제 | 심각도 | 유형 |
|---|------|--------|------|
| 1 | 네이티브 호스트에서 영구 설치(apt/pip/curl\|sh/~/.local/bin)를 지양하고 ephemeral 도커·venv로 유도하는 규칙이 **전 레이어에 부재**. 컨텍스트(네이티브 vs 컨테이너) 인지도 없음 | ⚠️ | 구조적 |

---

## PART A — 부재 확인 (전수 감사)

| 토픽 | 위치 | 실제 내용 | 갭 커버? |
|---|---|---|---|
| `dangerous_bash_check.py` PreToolUse(Bash) | `hooks/dangerous_bash_check.py:25-166` | `rm -rf`·fork bomb·`dd/mkfs`·비밀파일 노출만 차단. **install/apt/pip/curl\|sh/.local/bin 패턴 0** | ✗ |
| "Claude 네이티브, 호스트서 docker 제어" | `SKILL.md:192`, `templates/docker-compose.yml:6` | 멀티서비스 *스택 오케스트레이션* 한정. *도구 설치 위치*는 언급 없음 | ✗ |
| "격리" | `verifier_sandbox.py`, `agents/verifier.md` | verifier 테스트 격리(`/tmp`)만 | ✗ |
| `constraints.yaml` | `templates/docs/constraints.yaml` | 의존성 banned/allowed SSOT. 호스트설치/격리 필드 없음 | ✗ |
| workflow.md "외부 SDK·툴체인" | `templates/.claude/memory/workflow.md:298-302` | "공식 단계 먼저 나열". 설치 위치 무관 | ✗ |

→ `\.local/bin`·`host.?pollut`·`prefer.*docker`·`pip install` 설치유도 grep **0 hit**. **규칙 완전 부재 확정.** dangerous_bash_check 가 install 을 모델링하지 않아 ffmpeg `~/.local/bin` 설치가 무저항 통과한 것.

---

## PART B — 설계 (권장: 문서 규칙 + 능동 환기 훅, 둘 다 최소형)

Subtraction-First("이 훅 없으면 실제 문제 생기나?") 적용 → **답: 예** (실제 호스트 오염 발생). 단 채널을 정확히:

### 핵심 제약 — 차단 아닌 **환기** (CLAUDE.md:44-62)
호스트 오염은 데이터손실급이 아니다 → **`exit 0 + hookSpecificOutput.additionalContext`** (환기 채널). **`dangerous_bash_check.py`(exit 2 차단)에 절대 합치지 않는다** — 의도/채널 혼용은 문서화된 안티패턴. 별도 advisory 훅이 채널 정합.

### 수정 1 — 신규 훅 `plugins/project-init/hooks/host_install_advisory.py`
PreToolUse(Bash), 3.8 호환(`from __future__ import annotations`), ~50줄. 로직:
1. stdin `tool_input.command` 추출 (Bash 아니면 exit 0)
2. **컨테이너 안이면 silent exit 0** (직접 설치 허용): `/.dockerenv` OR `/proc/1/cgroup` 에 docker/containerd/kube/lxc OR `$container` env
3. **`.claude/host_install_ok` 플래그 있으면 silent exit 0** (사용자 opt-out — `PREFER_NO_GIT_FLAG`(plan_gate_lib.py:113-135) 패턴 미러)
4. **오탐 가드(패턴 검사 전)**: `docker run/exec/build/compose`·podman·nerdctl 대상이면 통과 / `.venv/bin/pip`·`source …/activate`·`uv pip|add|sync|run`·`poetry`·`pipx`·`VIRTUAL_ENV=` 면 통과
5. 호스트 오염 패턴 감지 시 additionalContext 로 ephemeral 권고 주입

**감지 패턴**: `apt|apt-get|aptitude install`, `dnf|yum|zypper install`, `brew install`, `pip install`, `npm|yarn|pnpm … -g`, `curl|wget … | sh`, `make install`, `~/.local/bin` 쓰기/다운로드.

**환기 메시지(예)**: "호스트 네이티브 환경에서 영구 설치 감지: <라벨>. docker run --rm / venv·uv / npx·pipx 로 대체 권장. 직접 설치가 의도면 `.claude/host_install_ok` 생성해 끄세요."

### 수정 2 — hooks.json 배선 (`hooks/hooks.json:5-14`)
기존 Bash PreToolUse 매처 블록에 dangerous_bash_check **다음** 2번째 훅으로 추가:
```json
{ "matcher": "Bash", "hooks": [
  { "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/dangerous_bash_check.py", "timeout": 5 },
  { "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/host_install_advisory.py", "timeout": 5 }
]}
```
순서 안전: 차단(exit2)이 먼저, 통과한 명령만 환기. PostToolUse(Bash) 2훅 공존 패턴과 동일.

### 수정 3 — 문서 규칙 (SSOT for *why*)
`templates/.claude/memory/workflow.md` 에 신규 섹션 추가:
> **## 격리 실행 (호스트 오염 방지)**
> Claude 가 호스트 네이티브로 돌 때, 시스템 도구·의존성을 호스트에 영구 설치(apt/pip --user/curl\|sh/make install/~/.local/bin)하지 않는다. 일회성 도커(`docker run --rm`)·프로젝트 venv/uv 로 격리 실행한다. Claude 가 컨테이너 내부면 직접 설치 무방. (능동 환기: `host_install_advisory` 훅)

### 컨텍스트 판정 — 런타임 감지 + 사용자 오버라이드
- 런타임: `/.dockerenv` + cgroup + `$container` (Docker/podman/k8s robust). 오탐(systemd-nspawn 등)은 드물고, 틀려도 *환기*라 피해 경미.
- 오버라이드: `.claude/host_install_ok`(끄기). claude-code-guide 는 `CLAUDE_ENVIRONMENT` env(settings.json)도 제시 — 가능하나 플래그파일이 하네스 기존 컨벤션(plan_gate 플래그)과 정합·greppable이라 우선.

---

## 일회성 vs 구조적
**구조적.** 규칙이 템플릿/훅에 없어 모든 네이티브 설치가 무저항. 문서-only는 이 사건 클래스를 이미 못 막았으므로(애초에 규칙 부재), 하네스 승격 원칙("효과 본 절차는 훅으로 승격")상 환기 훅으로 박는 게 맞다. 훅은 단일목적·stdlib·환기채널·기존 매처/플래그 재사용 → Subtraction-First 통과.

---

## ★ 비판적 검토 후 최종 결론 — doc-only (위 훅 설계 폐기)

위 "수정 1(훅)"은 적대적 검토에서 **폐기**됐다. 결정타 2가지:

1. **자기모순**: 제안한 패턴(`~/.local/bin` 쓰기·`curl|sh`·`pip install`)은 **이 호스트 자신의 정상 셋업을 오탐**한다 — uv·ruff·python3.11 심링크가 전부 `~/.local/bin` 설치이고 uv 자체가 `curl|sh` 설치([[host-install-isolation]] 메모리, claude-harness-native-setup). 훅은 "이건 정상 예외"를 표현 못 하지만(allowlist 비대화) 문서 규칙은 한 줄로 표현한다.
2. **Simplicity-First 위반**: 사건은 *메커니즘* 결함이 아니라 *판단* 결함. workflow.md는 매 세션 @-로드되는 판단 규칙 레이어 → 거기 한 문단이 정답. 컨텍스트 env감지는 fragile·redundant(자기 프로세스 환경만 봄), 매 매칭 나깅, opt-out 플래그는 N=1 조급.

**채택**: workflow.md 템플릿에 "격리 실행 / 호스트 오염 방지" 섹션 1개 (부트스트랩 예외 + 네이티브 한정 명시로 자기모순 차단). 훅은 문서가 *실증적으로* 실패할 때만 폴백(env감지·`~/.local/bin`패턴·opt-out 다 뺀 1/3 크기).

## 즉시 조치 (완료)
- [x] 영상 작업 docker(linuxserver/ffmpeg) 격리 처리 — 호스트 무오염
- [x] **workflow.md 템플릿에 "격리 실행" 섹션 추가** (미래 project-init 프로젝트 커버)
- [x] **현재 호스트용 메모리 `host-install-isolation` 추가** (홈=비-project-init이라 템플릿 미커버 → 매 세션 로드로 보완)
- [x] **동작 검증**: 규칙 로드 에이전트에 ffmpeg 시나리오 → "apt install 안 함, docker run --rm 사용" 자가 유도 확인
- [x] project-init 2.2.0→2.3.0 번프 + smoke_test + 커밋·태그
