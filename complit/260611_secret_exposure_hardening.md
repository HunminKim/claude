# 260611 비밀 파일 노출 경로 하드닝 (v1.32.0)

> 사용자 지적: "Read 시 정규식 * 처리 의도와 달라 .env 가 터미널에 노출되는 경우" —
> 가드가 예상한 형식만 막는 블랙리스트라 예상 밖 경로로 새는 위험.
> 적대적 테스트로 실제 우회 26경로 확인 → 19/26 노출 중이었음.

## 발견 (수정 전 실측)

`dangerous_bash_check` 의 비밀 읽기 가드가 `cat|head|tail|less|more|bat` 6개만 매치 →
다음이 전부 통과(노출):
grep·awk·sed·cut·sort·nl·tac·xxd·od·strings·base64·rg / `source .env`·`. .env` /
`python3 -c open('.env')` / `$(<.env)` / `cp .env /tmp/x`(복사 후 읽기) / `while read < .env`.
또한 **Grep 툴은 가드 자체가 없어** `Grep(path=.env, output_mode=content)` 완전 무방비.

## 수정 (fail-closed 다중 경로 탐지)

`_reads_secret(command)` 신설 — "읽기로 의심되면 차단":
1. 리더 명령 대폭 확장 (cat/grep/awk/sed/cut/sort/xxd/strings/base64/rg/ag/diff/... 30+종)
2. 셸 sourcing: `source .env`, `. .env`
3. 입력 리다이렉트: `cmd < .env`, `$(< .env)`
4. 복사/전송 소스: `cp|mv|scp|rsync|install|dd if= .env ...` (첫 인자=비밀 → exfil)
5. 인터프리터 인라인: `python/node/ruby/perl/php -c|-e` + 비밀 파일 토큰
secret_read_guard 에 `Grep` 추가 (matcher `Read|Grep`, Grep.path 검사).

오탐 0 보장: `.env.example`(negative lookahead), `cp .env.example .env`(dest는 2번째
인자라 미매치), `echo>>.env`(write), `chmod/ls/stat .env`(관리), `grep TODO src/`,
`python3 app.py`(인라인 플래그 없음) 전부 통과 — smoke 9건 검증.

## 정직한 한계 (사용자에게 고지)

정적 명령 검사로 100% 막을 수 없다:
- `cp .env x.txt` 차단되지만, `mv .env x.txt`(차단) 후 새 턴에서 `cat x.txt`(x.txt는
  비밀 패턴 아님) — **rename/copy 후 별도 명령 읽기는 본질적으로 사후 탐지 불가**
- 임의 스크립트(`./script.sh` 가 내부에서 .env 읽고 출력)도 명령줄에 .env 안 나타나면 불가
→ **최종 방어선은 .gitignore(v1.31.0) + OS 파일 권한(chmod 600) + "비밀은 사용자만 확인"
  원칙**. 이 훅은 "한 명령 내 직접 노출"을 광범위하게 막아 사고성 노출을 차단하는 1차선.

## 검증

- smoke [7b] 35건 (우회 차단 21 + 오탐 통과 9 + Grep/Read 4) → 156/156 통과
- 적대적 26경로 직접 실측: cat/grep/awk/sed/source/redirect/cp/interpreter 전부 🔒

- [x] tests/smoke_test.py 반영 완료 ([7b])
- [x] 한계를 complit 에 명시 (rename-후-읽기는 OS 권한이 방어선)
