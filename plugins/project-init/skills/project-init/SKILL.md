---
name: project-init
description: 프로젝트 초기화 스킬 — 새 프로젝트를 시작할 때 docs/ 폴더 구조 생성, CLAUDE.md 작성, 개발 원칙 세팅을 한 번에 처리한다. "/project-init", "프로젝트 초기화", "프로젝트 시작", "init project", "프로젝트 세팅해줘", "개발 환경 초기화", "프로젝트 구조 잡아줘" 등의 요청에 반드시 이 스킬을 사용한다. 단순한 파일 생성 요청이더라도 프로젝트 시작 맥락이라면 이 스킬을 사용한다.
---

# Project Init Skill

새 프로젝트를 시작할 때 필요한 문서 구조와 개발 원칙을 한 번에 세팅한다.

## 실행 순서

### 1단계: 프로젝트 파악

현재 디렉토리를 탐색해서 프로젝트 성격을 파악한다.

- 어떤 언어/프레임워크인지 (`package.json`, `requirements.txt`, `go.mod`, `Cargo.toml` 등)
- 이미 존재하는 파일 구조
- `docs/` 또는 `CLAUDE.md` 가 이미 있는지

**기존 코드가 있는 경우**: 파악한 내용을 한 줄로 요약한다. 그 다음 **기존 문서 선별 단계**를 진행한다:

1. 아래 파일이 존재하면 전부 읽는다:
   - `CLAUDE.md`
   - `docs/` 내 모든 `.md` 파일
   - `tasks/` 내 파일 (있으면)

2. 읽은 내용을 3범주로 분류한다:

   | 범주 | 기준 | 처리 |
   |------|------|------|
   | **keep** | 우리 규칙에 맞는 내용, 프로젝트 고유 정보 (버그·제약·결정 배경 등) | 새 템플릿의 올바른 섹션에 삽입 |
   | **transform** | 내용은 유효하나 형식·위치가 다른 것 | 우리 규칙에 맞게 재작성 후 삽입 |
   | **discard** | 중복·자명한 원칙·코드로 파악 가능한 내용 | 제거 |

3. 분류 결과를 사용자에게 **반드시 명시적으로 보고**한다:

   ```
   ## 기존 문서 선별 결과

   ### ✅ 유지 (keep)
   - [내용 요약] → [이동할 섹션]

   ### 🔄 변환 (transform)
   - [기존 내용] → [변환 방향]

   ### 🗑️ 제거 (discard)
   - [내용] — 제거 이유: [이유]
   ```

4. 사용자에게 확인을 받는다: "위 선별 결과로 진행할까요? 유지/제거 목록 조정이 필요하면 말씀해주세요."
5. 승인 후 새 템플릿에 선별된 내용을 반영해 파일을 생성한다.

**빈 프로젝트인 경우**: 탐색으로 알 수 없으므로 사용자에게 아래 내용을 질문한다. 한 번에 모아서 물어본다.

> 1. 프로젝트 이름이 무엇인가요?
> 2. 어떤 것을 만드는 프로젝트인가요? (목적을 한 줄로)
> 3. 사용할 언어/프레임워크/DB는 무엇인가요?
> 4. 빌드, 테스트, 린트 명령어를 알고 있다면 알려주세요. (모르면 `TBD` 로 남깁니다)
> 5. 알려진 버그, 환경 제약, 주의사항이 있나요? (예: 특정 라이브러리 버그, GPU 제약, API 제한 등)

사용자 답변을 받은 후 다음 단계로 진행한다. 빌드/테스트 명령어를 모른다고 하면 해당 항목을 `# TBD` 로 채워둔다.

### 2단계: docs/ 폴더 + 서브에이전트 생성

아래 파일들을 생성한다. 이미 파일이 존재하면 덮어쓰기 전에 사용자에게 확인한다.

```
docs/
├── development_plan.md    ← 개발 계획서           (개발 전 작성)
├── context_note.md        ← 맥락 노트             (개발 전 작성)
├── checklist.md           ← 체크리스트            (verifier가 소단위마다 업데이트)
├── code_rules.md          ← 상세 코드 규칙         (읽기 전용, CLAUDE.md에서 @참조)
├── technical_doc.md       ← 기술 문서             (verifier가 소단위마다 누적)
├── completion_report.md   ← 완료 보고서           (verifier가 소단위마다 누적)
├── deployment_guide.md    ← 배포 가이드           (개발 중 누적 → 완료 후 정리)
└── debug/                 ← 디버깅 패치 노트
    └── .gitkeep

tasks/
├── lessons.md             ← 행동 교정 패턴 누적    (사용자 교정 발생 시 업데이트)
└── todo.md                ← 세션별 작업 계획       (복잡한 작업 시작 시 작성)

.claude/agents/
└── verifier.md            ← 기능 검증 전담 서브에이전트
```

각 문서 파일 내용은 아래 **템플릿 섹션**을 참고한다. 프로젝트 이름, 날짜(KST 기준), 기술 스택을 템플릿에 채워 넣는다.

`verifier.md`는 `assets/templates/agents/verifier.md` 템플릿을 읽어 `.claude/agents/verifier.md`로 생성한다. 이 에이전트는 소단위 작업이 완료될 때마다 구현자(메인 에이전트)와 독립적으로 기능을 검증하는 역할을 한다.

### 3단계: CLAUDE.md 생성

`CLAUDE.md`는 **100줄 이내**로 유지한다. Anthropic 공식 권장 원칙을 따른다:

**포함할 것** (코드를 읽어도 알 수 없는 것만):
- 프로젝트 특화 명령어 (빌드/테스트/린트)
- 비표준 코드 스타일 (예: 한국어 주석 규칙)
- 비직관적인 워크플로우 (예: @verifier 위임, /compact 타이밍)
- 알려진 버그 / 환경 제약 / 함정 (예: 특정 라이브러리 버그, GPU 제약, 경로 분리 규칙)
- 아키텍처 결정 및 비직관적인 주의사항

**제외할 것**:
- `docs/code_rules.md`에 이미 있는 내용 (매직넘버 금지, 하드코딩 금지, 파일 크기 제한 등) — 중복 금지
- 표준 언어 관례 (단일 책임, Early Return, 예외 처리 기본 등)
- 코드를 읽으면 파악 가능한 구조 설명
- "깔끔한 코드를 작성하라" 같은 자명한 원칙

각 줄 작성 기준: "이게 없으면 Claude가 실수할까?" — 아니라면 삭제한다.

템플릿(`assets/templates/CLAUDE.md`)을 기반으로 아래 항목을 실제 값으로 채워 생성한다:
- `{{PROJECT_NAME}}` — 프로젝트명
- `{{TECH_STACK}}` — 기술 스택 (언어, 프레임워크, DB 등)
- `{{PROJECT_DESCRIPTION}}` — 프로젝트 목적 한 줄 요약
- `{{PROJECT_STRUCTURE}}` — 핵심 디렉토리 구조 (3~5줄 이내)
- `{{BUILD_COMMAND}}` — 빌드 명령어 (없으면 해당 줄 제거)
- `{{TEST_COMMAND}}` — 전체 테스트 실행 명령어
- `{{TEST_SINGLE_COMMAND}}` — 단일 테스트 실행 명령어
- `{{LINT_COMMAND}}` — 린트 명령어 (없으면 해당 줄 제거)

이미 `CLAUDE.md`가 있으면:
1. 기존 내용을 읽는다
2. 새로 작성될 내용을 생성한다 (파일에 쓰지 않고 초안만)
3. 사용자에게 변경 사항을 보고한다:
   - 기존 내용 요약 (주요 섹션 나열)
   - 새 내용과 달라지는 점 (추가/삭제/변경되는 항목)
4. 사용자에게 덮어쓰기 허가를 받는다
5. 허가하면 덮어쓴다. 거부하면 CLAUDE.md는 건드리지 않고 넘어간다

### 4단계: 완료 보고

생성된 파일 목록을 보여주고, 사용자에게 다음을 안내한다:
- `docs/development_plan.md` 에서 개발 계획을 채워달라고
- `docs/context_note.md` 에서 프로젝트 배경/맥락을 기록해달라고
- 개발 시작 전에 `docs/checklist.md` 를 함께 작성하자고

문서 업데이트 자동화 흐름도 안내한다:
- `technical_doc.md`, `completion_report.md`, `checklist.md` 는 @verifier가 소단위 완료마다 자동 업데이트
- `deployment_guide.md` 는 개발 중 환경 관련 내용을 수시로 기록, 완료 후 정리
- `retrospective.md` 는 초기화 시 생성하지 않는다 — 사용자 완료 사인 후 `assets/templates/retrospective.md` 템플릿으로 생성한다

그리고 아래 **개발 워크플로우**를 사용자에게 명시적으로 안내한다:

> **검증 워크플로우**
> 소단위 작업이 완료될 때마다 구현자가 직접 검증하지 않는다.
> 반드시 `@verifier` 서브에이전트에게 검증을 위임한다.
>
> 흐름: 구현 완료 → `@verifier` 호출 → 검증 보고서 확인 → `docs/checklist.md` 업데이트
>
> verifier는 독립적인 시각으로 기능을 검증하고 문제를 보고하는 역할이며,
> 수정은 하지 않는다. 수정은 메인 에이전트(구현자)의 몫이다.

---

## 템플릿 섹션

### development_plan.md 템플릿

`assets/templates/development_plan.md` 파일을 읽어 사용한다.

### context_note.md 템플릿

`assets/templates/context_note.md` 파일을 읽어 사용한다.

### checklist.md 템플릿

`assets/templates/checklist.md` 파일을 읽어 사용한다.

### verifier.md 템플릿

`assets/templates/agents/verifier.md` 파일을 읽어 사용한다.

### code_rules.md 템플릿

`assets/templates/docs/code_rules.md` 파일을 읽어 사용한다.

### technical_doc.md 템플릿

`assets/templates/docs/technical_doc.md` 파일을 읽어 사용한다.

### completion_report.md 템플릿

`assets/templates/docs/completion_report.md` 파일을 읽어 사용한다.

### deployment_guide.md 템플릿

`assets/templates/docs/deployment_guide.md` 파일을 읽어 사용한다.

### retrospective.md 템플릿

`assets/templates/docs/retrospective.md` 파일을 읽어 사용한다.

### lessons.md 템플릿

`assets/templates/tasks/lessons.md` 파일을 읽어 사용한다.

### todo.md 템플릿

`assets/templates/tasks/todo.md` 파일을 읽어 사용한다.

### CLAUDE.md 템플릿

`assets/templates/CLAUDE.md` 파일을 읽어 사용한다.

---

## 주의사항

- 모든 날짜는 KST(한국 표준시) 기준으로 표기한다
- 이미 존재하는 파일은 사용자 확인 없이 덮어쓰지 않는다
- CLAUDE.md는 반드시 100줄 이내로 유지한다
- 템플릿의 `{{PROJECT_NAME}}`, `{{DATE}}`, `{{TECH_STACK}}` 등의 자리표시자는 실제 값으로 교체한다
