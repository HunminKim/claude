# B-2 핸드오프 — plan-gate 체크포인트 dirty/비-git 대응 (별도 세션 대기)

> 출처: harness-check 260612 2차 리포트 B-2. 위험도 높은 대수술이라 즉흥 처리하지
> 않고 별도 정밀 세션으로 분리. 이 문서는 그 세션의 입력(설계 입력 + 위험 + 검증 계획).
>
> **상태**: 부분 해소(v1.38.0). B-1(파일별 임계 오버라이드)·B-4(diff 기준점)는 v1.37.0.
>
> ### v1.38.0 처리 — 비-git 루트 cp 스냅샷 백엔드 (접근 B만 채택)
> - **한 것**: 루트가 git repo 가 아닐 때만(`not has_git`) 편집 직전 파일 원본을
>   `.claude/state/checkpoints/<gate_id>/` 에 1회 복사(`cp_snapshot_file`). `/rollback` 이
>   `cp_rollback` 으로 원본 복원·신규 파일 삭제. `/done` 시 `cp_cleanup` 으로 정리.
>   매니페스트는 `gate["cp_snapshot"]={relpath: 편집전존재여부}`. 비-git 환경의 "롤백 불가"
>   기능 부재를 메움. 행위 검증: `smoke_test.py` `t_cp_rollback_nongit`(복원+신규삭제+회귀가드).
> - **의도적으로 안 한 것 (접근 A — dirty git stash-create)**: git 경로의 `reset --hard`→
>   `stash apply` 시맨틱 변경은 **전 사용자 회귀 위험** 대비 이득이 작아 보류. 현재 git 경로는
>   dirty 면 tag 스킵(안전 측 실패) + `stash_dirty` 로 tracked 변경 보존 중이라 데이터 손상
>   없음. git 경로는 이번 변경에서 **일절 미수정**(회귀 0, smoke 로 cp_snapshot None 유지 확인).
> - **남은 것**: dirty *git* tree 에서 체크포인트 생성(접근 A)은 여전히 미해소. 실제 통증 발생 시 재검토.

## 문제 (실재)

plan-gate 체크포인트가 **clean git tree 를 암묵 전제**한다:
- `plan_gate.py:84` 근처 — `working_tree_clean()` 가 False(dirty)면 tag 생성 스킵 → 체크포인트 없음.
- 롤백 `plan_gate_lib.py` `reset_to_tag` → `git reset --hard <tag>` — **dirty 공용 tree 에서 미커밋 변경(패치)을 파괴**.
- `/workspace` 가 비-git 이고 실제 코드가 하위 repo(fmf 등)이며 uncommitted 패치로 상시 dirty인 환경(adas-workspace)에서는:
  - `has_git(root)` = False → 어떤 git 체크포인트도 불가 → `/rollback` 항상 "체크포인트 tag 없음"으로 거부.

## 리포트 제안 (B-2)

- `snapshot_tree(root)` = `git stash create`(commit/ref 미생성 — 공용 git 안전)로 트리 SHA 캡처 → `gate["checkpoint_tree_snapshot"]` 저장. `working_tree_clean()` 전제 제거.
- 롤백: `reset --hard` 대신 `git stash apply <sha>` / `git checkout-index`(apply-not-reset). dirty 였으면 사용자 확인 필수.
- **이 환경엔 무효**: 루트 비-git → git stash create 자체 불가. 비-git 해결책은 **cp 파일 스냅샷**(B-4 와 동일 계열).

## 왜 별도 세션인가 (위험)

1. **롤백 동작 변경 = 전 사용자 영향**. `reset --hard`(현재) → `stash apply`(제안)는 의미가 다르다. 기존 clean-git 프로젝트의 롤백 동작이 바뀌면 회귀.
2. **체크포인트 생성/복원 경로 전반 재작성** — `do_checkpoint`(tag+stash), `reset_to_tag`, `find_stash_for_gate`, `cmd_rollback` 모두 연동. 한 곳만 고치면 상태 불일치.
3. **비-git 루트는 git 해법으로 못 풂** — cp 스냅샷이라는 별도 메커니즘(파일 단위 백업 디렉토리)이 필요. 이건 plan-gate 체크포인트의 *2번째 백엔드* 추가에 가깝다.
4. 현재 동작은 **데이터를 파괴하진 않는다**(dirty면 tag 스킵 = 안전 측 실패). 즉 긴급 버그는 아니다 — 기능 부재(롤백 불가)이지 손상은 아님.

## 별도 세션 설계 입력

### 접근 (택1 또는 혼합 — 세션에서 결정)
- **A. git stash create 백엔드** (clean 전제 제거): clean/dirty 무관 트리 스냅샷. 롤백은 apply. **단 git repo 필요**.
- **B. cp 스냅샷 백엔드** (비-git/하위-repo 대응): 체크포인트 시 영향 파일을 `.claude/state/checkpoints/<gate_id>/` 에 복사. 롤백은 복원. git 무관, 어떤 디렉토리에도 동작. 단 "영향 파일" 범위를 알아야(todo.md 영향 파일 섹션 활용).
- **C. 백엔드 추상화**: `checkpoint_backend(root)` 가 git 가능하면 A, 아니면 B 선택. gate 에 backend 종류 기록.

### 핵심 결정 포인트
- 롤백 시맨틱 변경(reset→apply)을 기존 프로젝트에 적용할지, 아니면 dirty/비-git 일 때만 새 경로로 분기할지 (후자가 회귀 위험 낮음 — **권장 출발점**).
- cp 스냅샷의 범위: 전체 tree(느림/큼) vs todo.md 영향 파일만(범위 밖 변경 미보호). 후자 + 경고.
- `has_git` False 일 때 현재 "체크포인트 없음" 거부 메시지를 cp 백엔드 안내로 교체.

### 영향 파일 (수정 대상)
- `hooks/plan_gate_lib.py` — `do_checkpoint`/`reset_to_tag`/`working_tree_clean` 전제, 새 백엔드 함수
- `hooks/plan_gate.py` — 체크포인트 생성 호출부(:84 근처)
- `hooks/plan_gate_cli.py` — `cmd_rollback`(:273 근처) 복원 경로
- 템플릿 CLAUDE.md — "plan-gate stash 동작" 알려진 버그 섹션 갱신

### 검증 계획 (smoke 신규)
- clean git: 기존 tag+reset 경로 회귀 없음 (현 t_plan_gate 유지)
- dirty git: 체크포인트 생성됨(tag 스킵 안 함) + 롤백이 패치 보존(apply, reset 아님)
- 비-git 루트: cp 백엔드로 체크포인트 + 롤백 동작, 하위 파일 복원 확인
- 각각 행위 테스트 (가짜 프로젝트 + dirty 상태 조성)

## 즉시 우회 (코드 수정 전, 그 환경)
- 다회편집 작업: `/plan-gate-off` → 작업 → `@verifier` → `/plan-gate-on`
- 롤백 필요 시: 하위 repo(fmf)에서 직접 `git` 으로 관리 (루트 plan-gate 체크포인트 의존 안 함)
