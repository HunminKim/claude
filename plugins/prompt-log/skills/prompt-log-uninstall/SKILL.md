---
name: prompt-log-uninstall
description: prompt-log 가 수집한 데이터(월별 jsonl·whitelist·프로젝트 marker·active)를 목록으로 보여주고 사용자 확인 후 일괄 삭제한다. "/prompt-log-uninstall", "프롬프트 로그 삭제", "prompt-log 데이터 지워줘", "수집 데이터 삭제", "프롬프트 수집 중단하고 다 지워줘" 등의 요청에 이 스킬을 사용한다.
disable-model-invocation: true
---

# Prompt-Log Uninstall Skill

prompt-log 수집 데이터를 **먼저 목록으로 보여주고, 사용자 확인을 받은 뒤** 삭제한다.
비대화형 일괄 삭제가 필요하면 `plugins/prompt-log/uninstall.sh` 를 직접 실행해도 된다
(스크립트도 동일한 순서를 따르지만 확인 없이 지운다).

## 실행 순서

### 1단계: 삭제 대상 수집 (아직 아무것도 지우지 않는다)

**⚠️ 순서 중요**: whitelist(`~/.claude/prompt-log/projects-allowed.json`)를 **삭제 전에 읽어야**
다른 위치 프로젝트들의 잔존물을 찾을 수 있다. 글로벌 디렉토리를 먼저 지우면 목록이 사라진다.

1. `~/.claude/prompt-log/projects-allowed.json` 을 읽어 동의 프로젝트 목록 확보
2. 글로벌 데이터 파악: `~/.claude/prompt-log/` 아래 `prompts-*.jsonl`(파일 수·총 크기·레코드 수),
   `failed-flush.jsonl`, `sanitize_rules.yaml`(사용자 작성 파일 — 아래 참고)
3. 각 동의 프로젝트(abs_path)에서 잔존물 확인:
   - `<project>/.claude/prompt-log-consent`
   - `<project>/.claude/state/prompt-log-active.json` (+ `.tmp`, `.active.lock`)
   - 프로젝트 디렉토리가 이미 없으면 "(경로 없음 — 스킵)" 표기

### 2단계: 목록 보고

수집한 내용을 아래 형식으로 보여준다:

```
## prompt-log 삭제 예정 목록

### 글로벌 (~/.claude/prompt-log/)
- prompts-2026-06.jsonl  (N records, X KB)
- projects-allowed.json  (프로젝트 M개 등록)

### 프로젝트별 잔존물
- /path/to/projA — consent marker, active.json
- /path/to/projB — consent marker
- /path/to/gone  — (경로 없음 — 스킵)

⚠️ 삭제하면 복구할 수 없습니다.
```

`sanitize_rules.yaml` 이 있으면 **기본 보존**을 제안한다 (사용자가 직접 작성한 규칙 파일 —
수집 데이터가 아님). 함께 지울지는 3단계 질문에 선택지로 포함한다.

### 3단계: 사용자 확인

**AskUserQuestion 툴**로 확인한다:
- 질문: "위 목록을 모두 삭제할까요?"
- 옵션: `["전부 삭제 (Recommended)", "글로벌 데이터만 삭제 (프로젝트 marker 유지)", "취소"]`
- `sanitize_rules.yaml` 존재 시 두 번째 질문 추가: "사용자 정의 마스킹 규칙(sanitize_rules.yaml)도 삭제할까요?" — `["보존 (Recommended)", "함께 삭제"]`

### 4단계: 삭제 실행

선택에 따라 삭제한다 (**프로젝트별 잔존물 → 글로벌 디렉토리** 순서 유지):

1. 각 동의 프로젝트의 `prompt-log-consent`, `prompt-log-active.json`, `.tmp`, `.active.lock` 삭제
2. `~/.claude/prompt-log/` 삭제 (보존 선택된 `sanitize_rules.yaml` 은 임시로 빼두었다가 복원)
3. 삭제한 파일 목록을 결과로 보고한다

### 5단계: 후속 안내

```
✅ prompt-log 데이터 삭제 완료.

플러그인 자체를 제거하려면:
    claude plugins uninstall prompt-log

외부 통합 흔적(project-init SKILL.md 의 동의 단계 등)은 마커로 감싸져 있습니다:
    grep -rn '\[prompt-log\]' <마켓플레이스 저장소>
```

## 주의사항

- 이 스킬은 **데이터만** 지운다 — 플러그인 코드 제거는 `claude plugins uninstall` 안내만 한다
- whitelist 에 없는 위치에 수동 생성된 marker 는 찾을 수 없다 — 사용자가 기억하는 경로가
  있으면 알려달라고 안내한다 (`find <경로> -name prompt-log-consent`)
- 삭제 전 목록 단계를 건너뛰지 않는다 — 사용자가 "그냥 다 지워"라고 해도 목록은 한 번 보여준다
