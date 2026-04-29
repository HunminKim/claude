# prompt-log V2 TODO

V1 에서 의도적으로 제외한 항목을 정리해둔 문서. 검증 후 단계적으로 도입.

## 자동 보존 / 삭제 (GDPR Article 17 친화)

- [ ] 90일 후 jsonl 자동 압축 (`prompts-YYYY-MM.jsonl.gz`)
- [ ] 365일 후 자동 삭제
- [ ] 동의 철회 후 90일 grace period 자동 삭제
- [ ] `meta/deleted-projects.log` audit trail (철회 시점·대상 기록)
- [ ] 자동 삭제 트리거: SessionEnd 훅에서 주기 체크 (`last_gc_ts` state)

## 사용자 인터페이스

- [ ] `/prompt-log-status` 슬래시 커맨드 — 현재 프로젝트 동의 여부, 글로벌 통계
- [ ] `/prompt-log-cleanup` — 30일 이상 오래된 데이터 압축
- [ ] `/prompt-log-delete-now` — 즉시 삭제 (현재 프로젝트 또는 전체)
- [ ] `/prompt-log-export` — 특정 프로젝트 데이터 export (csv/json)

## 분석 도구

- [ ] `prompt_log_cli.py analyze` — 통계, 빈도, plan-gate trigger rate, 토큰 시퀀스
- [ ] **시뮬레이션 replay** — 저장된 raw 데이터에 새 휴리스틱 적용 → "이 휴리스틱이라면 어떻게 됐을까" 비교
  - 핵심 가치: 휴리스틱 임계값 변경 전 false positive/negative 정량 측정
- [ ] CSV / Parquet export
- [ ] 시간대별 사용 패턴 통계

## 보안 / 프라이버시

- [ ] 사용자 정의 sanitize 규칙 `~/.claude/prompt-log/sanitize_rules.yaml`
- [ ] 환경변수 강제 비활성 (`PROMPT_LOG=0` 또는 `PL_DISABLED=1`)
- [ ] 파일 권한 0600 강제 (`chmod 0600` 자동 적용)
- [ ] Optional GPG 암호화 (~/.claude/prompt-log/key.gpg)
- [ ] DPA (Data Processing Agreement) 문서 제공
- [ ] 익명화 export 모드 (`abs_path` 해시화 등)

## 신호 통합 (다른 훅과 연동)

- [ ] `detect_user_correction.py` 결과 → `record.signals.correction_detected` (level: STRONG/MEDIUM)
- [ ] `detect_failure_loop.py` 결과 → `record.signals.failure_loop_detected`
- [ ] `update_docs.py` verifier 결과 → `record.plan_gate.verifier_status` 자동 동기화
- [ ] 파일 확장자 통계 → `record.files.extensions: {".py": 3, ".md": 1}`
- [ ] 언어 감지 → `record.prompt.lang_hint: "ko|en|mixed"`

## 데이터 모델 확장

- [ ] git diff 요약 포함 옵션 (사용자 토글)
- [ ] subagent 호출 내역 (`Task` 도구의 description, subagent_type 등)
- [ ] 사용자 메타 라벨 (작업 카테고리: feature/bugfix/refactor)
- [ ] /compact 발생 여부 + ts

## 동시성 / 안정성

- [ ] `fcntl.flock` 으로 jsonl append 시 동시 쓰기 방지
- [ ] active state 파일 lock (여러 PreToolUse 동시 실행 시)
- [ ] 부분 쓰기 실패 시 active state 복구 로직

## 플랫폼 / 호환성

- [ ] XDG_DATA_HOME 표준 따르기 (`$XDG_DATA_HOME/claude/prompt-log/`)
- [ ] Windows path 호환 (`expanduser` 외 추가 처리)
- [ ] Symlink 처리 (프로젝트 root가 symlink일 때)

## UX / 운영

- [ ] 첫 prompt 시 "이 프로젝트 동의 안 됨, /project-init 또는 수동 활성화" 안내 (1회만)
- [ ] 디스크 사용량 임계값 알림 (예: 100MB 초과 시)
- [ ] 월별 요약 자동 생성 (`prompts-YYYY-MM-summary.json`)

## 테스트 / 검증

- [ ] 단위 테스트 (sanitize 정규식, consent 검사, finalize 흐름)
- [ ] 시뮬레이션 시나리오 (50+ prompt 가상 입력 → 예상 record 비교)
- [ ] 동시성 stress test
- [ ] secret leak fuzzing (다양한 형태의 API key가 섞인 prompt)
