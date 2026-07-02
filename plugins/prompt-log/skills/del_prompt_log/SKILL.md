---
name: del_prompt_log
description: prompt-log 가 수집한 로그를 현재 프로젝트의 ./prompt_log/ 로 옮기고(원본 삭제), 이후 수집 동의를 유지할지 철회할지 다시 묻는다. "/del_prompt_log", "프롬프트 로그 지워줘", "수집된 프롬프트 내보내고 삭제", "프롬프트 로그 정리" 등의 요청에 이 스킬을 사용한다.
disable-model-invocation: true
---

# Del Prompt Log Skill

수집된 prompt 로그를 **현재 프로젝트의 `./prompt_log/` 로 이동**(원본은 삭제)하고,
이동이 끝나면 **수집 동의를 계속 유지할지 철회할지** 사용자에게 다시 묻는다.

삭제가 아니라 이동이므로 데이터는 사용자 손에 남는다 — 검토·보관·직접 삭제는 사용자 몫.

## 실행 순서

### 1단계: 이동 대상 수집 (아직 아무것도 옮기지 않는다)

**⚠️ 순서 중요**: whitelist(`~/.claude/prompt-log/projects-allowed.json`)는 마지막
동의 결정 단계까지 필요하다 — 먼저 지우거나 옮기지 않는다.

1. `~/.claude/prompt-log/` 에서 **수집 데이터**를 파악한다:
   - `prompts-*.jsonl` (월별 레코드 — 파일 수·레코드 수·총 크기)
   - `failed-flush.jsonl` (있으면)
2. 현재 프로젝트의 진행 중 레코드: `<project>/.claude/state/prompt-log-active.json` (있으면)
3. `projects-allowed.json` 을 읽어 동의 프로젝트 목록 확보 (5단계 질문에 사용)

> `sanitize_rules.yaml`(사용자 작성 마스킹 규칙)과 `projects-allowed.json` 은
> **수집 데이터가 아니므로 옮기지 않는다** — 동의 유지 시 계속 필요하다.

### 2단계: 이동 계획 보고

```
## prompt-log 이동 예정 목록  →  ./prompt_log/

- prompts-2026-06.jsonl  (N records, X KB)
- prompts-2026-07.jsonl  (M records, Y KB)
- prompt-log-active.json (진행 중 레코드 1건)

이동 후 원본(~/.claude/prompt-log/ 의 위 파일들)은 삭제됩니다.
```

### 3단계: 이동 실행

1. 프로젝트 루트에 `prompt_log/` 디렉토리 생성
2. 1단계에서 파악한 **수집 데이터 파일들**(1·2항 — whitelist 는 제외)을 `prompt_log/` 로
   **이동**(`mv` — 복사 후 삭제와 동일 효과)
3. **`.gitignore` 에 `prompt_log/` 가 없으면 append** — 프롬프트 원문이 담긴
   데이터가 실수로 커밋·푸시되는 것을 차단 (이동 직후, 질문 전에 먼저 처리)

### 4단계: 이동 결과 보고

옮긴 파일 목록·건수·위치를 보고한다.

### 5단계: 수집 동의 재확인

**AskUserQuestion 툴**로 묻는다:
- 질문: "로그를 비웠습니다. 이 프로젝트의 prompt 수집을 계속할까요?"
- 옵션:
  - `["동의 유지 — 계속 수집 (Recommended)", "이 프로젝트만 철회", "모든 프로젝트 철회"]`
  - whitelist 에 다른 프로젝트가 없으면 세 번째 옵션은 생략한다

선택 처리:
- **동의 유지**: 아무것도 바꾸지 않는다 (marker·whitelist 그대로 → 다음 프롬프트부터 새로 수집)
- **이 프로젝트만 철회**: `<project>/.claude/prompt-log-consent` 삭제 +
  `projects-allowed.json` 에서 이 프로젝트 항목 제거
- **모든 프로젝트 철회**: whitelist 의 **각 프로젝트**에서
  `<프로젝트>/.claude/prompt-log-consent`·`<프로젝트>/.claude/state/prompt-log-active.json`
  삭제 후, `projects-allowed.json` 자체 삭제
  (다른 프로젝트의 active 파일은 옮기지 않고 삭제 — 원하면 해당 프로젝트에서
  /del_prompt_log 를 먼저 실행하라고 안내)

### 6단계: 마무리 안내

```
✅ 로그 이동 완료: ./prompt_log/ (원본 삭제됨)
동의 상태: <유지 / 이 프로젝트 철회 / 전체 철회>

플러그인 자체를 제거하려면:
    bash plugins/prompt-log/uninstall.sh   # 잔여 데이터 일괄 정리
    claude plugins uninstall prompt-log
```

## 주의사항

- 이동 전 목록 보고(2단계)를 건너뛰지 않는다
- `./prompt_log/` 에 같은 이름 파일이 이미 있으면 덮어쓰지 말고
  `prompts-2026-06.1.jsonl` 식으로 suffix 를 붙인다
- 전역 jsonl 에는 **여러 프로젝트의 레코드가 섞여** 있다 — 이동은 전체 파일 단위이며
  프로젝트별 분리는 하지 않는다 (필요하면 record 의 `project.abs_path` 로 필터 안내)
