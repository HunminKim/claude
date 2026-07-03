# 버전 관리 · 커밋 · 릴리스 (상세)

> `CLAUDE.md`의 포인터에서 참조. **버전 번프·커밋·태그·푸시**를 수행할 때 이 문서를 따른다.

## 커밋 / PR 포맷

```
type(scope?): English title
- 한국어 변경 내용 / 변경 이유
```

- type: `feat` `fix` `refactor` `docs` `chore`
- scope: `plan-gate`, `prompt-log`, `project-init` 등. 루트 영향 시 생략 가능.

## 플러그인 버전 관리

버전은 자동으로 올리지 않는다. **사용자가 명시적으로 버전 번프를 요청할 때만** 올린다.
요청을 받으면 변경 내용(diff)을 보고 semver 등급을 판단해 **근거와 함께 제안**하고, 확정 후 올린다.
(과거 "변경할 때마다 무조건 번프" 규칙은 버전 세분화를 유발해 폐지했다.)

번프할 때는 해당 플러그인의 `plugin.json` 버전과 `.claude-plugin/marketplace.json` description 버전을 함께 동기화한다:

- `plugins/project-init/` 변경 → `plugins/project-init/.claude-plugin/plugin.json` + `.claude-plugin/marketplace.json` description
- `plugins/harness-check/` 변경 → `plugins/harness-check/.claude-plugin/plugin.json` + `.claude-plugin/marketplace.json` description
- `plugins/prompt-log/` 변경 → `plugins/prompt-log/.claude-plugin/plugin.json` + `.claude-plugin/marketplace.json` description
- 버전 형식: semver — 변경 규모로 판단:
  - `patch` (x.y.Z): 버그픽스, 오탈자, 메시지 수정 등 작은 수정
  - `minor` (x.Y.0): 기능 추가, 훅 개선, 새 파일 추가 등 하위 호환 변경
  - `major` (X.0.0): 하네스 구조 변경, 기존 동작 파괴적 변경

> 참고: 버전이 같으면 사용자 캐시가 갱신되지 않으므로, 사용자에게 실제 배포가 필요한 변경이면 번프를 함께 제안한다.

## 릴리스 (태그 필수)

버전 번프를 동반한 변경을 푸시할 때는 그 버전의 SemVer 태그를 함께 단다.
**번프된 릴리스를 태그 없이 푸시하면 마켓플레이스가 정식 릴리스로 인식하지 못한다.**
(번프가 없는 일반 커밋은 태그를 달지 않는다. git 태그는 `plugin.json` 버전과 1:1로 유지한다.)

```bash
git tag -a vX.Y.Z -m "한 줄 요약"          # project-init (주 플러그인 — 무접두 v* 대역 소유)
git tag -a prompt-log-vX.Y.Z -m "한 줄 요약"  # 그 외 플러그인은 <plugin>-v* 접두
git push origin main --tags
```

- `git push origin main` 만 하면 태그가 올라가지 않는다 → 반드시 `--tags` 포함
- 태그 버전은 변경된 플러그인의 `plugin.json` 버전과 일치시킨다
- **태그 네임스페이스**: 무접두 `v*` 대역은 project-init 이 소유한다(v1.x 는 이미
  project-init 구버전 시리즈가 선점). harness-check·prompt-log 는
  `harness-check-vX.Y.Z`·`prompt-log-vX.Y.Z` 접두 태그를 쓴다 — 단일 `v*` 대역을
  세 플러그인이 공유하면 버전이 충돌해 1:1 규칙이 구조적으로 깨진다
- Annotated 태그(`-a`)를 사용한다 (태거·날짜·메시지 보존)
