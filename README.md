# Claude Code 플러그인 마켓플레이스

HunminKim의 개인 Claude Code 플러그인 저장소.

## 새 환경에서 사용하기

```bash
git clone https://github.com/HunminKim/claude.git ~/claude-config
cd ~/claude-config
bash install.sh
```

`install.sh` 가 하는 일:
1. `hunminkim` 마켓플레이스 등록
2. 공식 플러그인 설치 (code-review, code-simplifier, skill-creator, hookify)
3. 개인 플러그인 설치 (project-init)

설치 후 Claude Code 재시작 또는 `/reload-plugins`

---

## 플러그인 목록

### project-init

프로젝트 시작 시 개발 환경을 한 번에 초기화한다.

**호출:** `/project-init` 또는 "프로젝트 초기화해줘"

#### 생성되는 파일

| 파일 | 시점 | 작성 주체 |
|------|------|----------|
| `CLAUDE.md` | 초기화 시 | /project-init |
| `docs/development_plan.md` | 초기화 시 | 사용자 작성 |
| `docs/context_note.md` | 초기화 시 | 사용자 작성 |
| `docs/checklist.md` | 초기화 시 → 소단위마다 업데이트 | verifier (자동) |
| `docs/technical_doc.md` | 소단위 완료마다 누적 | verifier (자동) |
| `docs/completion_report.md` | 소단위 완료마다 누적 | verifier (자동) |
| `docs/deployment_guide.md` | 개발 중 누적 → 완료 후 정리 | verifier + 사용자 |
| `docs/retrospective.md` | 완료 사인 후 | Claude |
| `docs/debug/*.md` | 버그 발생 시 | Claude |
| `.claude/agents/verifier.md` | 초기화 시 | /project-init |

#### 개발 워크플로우

```
사용자: 기능 구현 요청
    ↓
Claude: 소단위로 구현
    ↓
Claude: @verifier 호출 (CLAUDE.md에 명시, 예외 없음)
    ↓
verifier: 검증 후 docs/.verifier_result.json 저장
    ↓
PostToolUse 훅 자동 감지 (컨텍스트 무관)
    ↓
checklist / completion_report / technical_doc 자동 업데이트
    ↓
.verifier_result.json 자동 삭제
```

> 기능 그룹이 완전히 끝나면 `/compact` 실행 (소단위마다 하지 않음)

---

## 플러그인 업데이트

```bash
claude plugins update project-init@hunminkim
```
