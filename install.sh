#!/bin/bash
# 새 환경에서 Claude Code 플러그인 일괄 설치
# 사용법: bash install.sh
#
# 설계 원칙: 실패를 삼키지 않는다.
# - 각 명령의 stderr 를 캡처해 "이미 설치됨"과 "진짜 에러"를 구분한다
# - 마지막에 claude plugin list 로 실제 설치 여부를 자가 검증한다

set -u

# 스크립트 자신의 위치 — 로컬 클론을 마켓플레이스 소스로 우선 사용 (fork·오프라인 대응)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FAIL=0

run_step() {
  # $1: 표시 라벨, 이후: 실행할 명령
  # 참고: 현행 CLI 는 이미 설치/등록된 경우에도 exit 0 (실측) — 별도 분기 불필요
  local label="$1"
  shift
  local out
  if out=$("$@" 2>&1); then
    echo "      ✔ ${label}"
  else
    echo "      ✘ ${label} 실패:"
    echo "$out" | sed 's/^/        /'
    FAIL=1
  fi
}

echo "=== Claude Code 플러그인 설치 ==="

# 1. 마켓플레이스 등록
#    이름은 .claude-plugin/marketplace.json 의 name(hunminkim)에서 자동 파생된다.
#    (claude-plugins-official 은 CLI 내장 마켓플레이스라 등록 불필요)
#    소스: 스크립트 옆에 marketplace.json 이 있으면 로컬 클론을 등록한다
#    (fork·미러·오프라인·로컬 수정본 대응). 없으면 GitHub 원격으로 fallback.
echo "[1/3] 마켓플레이스 등록 중..."
if [ -f "$SCRIPT_DIR/.claude-plugin/marketplace.json" ]; then
  MARKET_SOURCE="$SCRIPT_DIR"
else
  MARKET_SOURCE="HunminKim/claude"
fi
run_step "marketplace hunminkim (${MARKET_SOURCE})" \
  claude plugin marketplace add "$MARKET_SOURCE"

# 2. 플러그인 설치
echo "[2/3] 플러그인 설치 중..."

# 공식 플러그인
OFFICIAL=(
  "code-review"
  "code-simplifier"
  "skill-creator"
  "hookify"
)
for plugin in "${OFFICIAL[@]}"; do
  run_step "${plugin}@claude-plugins-official" \
    claude plugin install "${plugin}@claude-plugins-official"
done

# 개인 플러그인
run_step "project-init@hunminkim" claude plugin install project-init@hunminkim
run_step "harness-check@hunminkim" claude plugin install harness-check@hunminkim

# >>> [prompt-log] integration begin
run_step "prompt-log@hunminkim" claude plugin install prompt-log@hunminkim
# <<< [prompt-log] integration end

# 3. 자가 검증 — 설치 목록에 실제로 존재하는지 확인 (무음 실패 방지)
echo "[3/3] 설치 검증 중..."
EXPECTED=(
  "code-review" "code-simplifier" "skill-creator" "hookify"
  "project-init" "harness-check"
  "prompt-log"
)
INSTALLED=$(claude plugin list 2>/dev/null || true)
for plugin in "${EXPECTED[@]}"; do
  if echo "$INSTALLED" | grep -qw "$plugin"; then
    echo "      ✔ $plugin"
  else
    echo "      ✘ $plugin — 설치 확인 실패"
    FAIL=1
  fi
done

echo ""
if [ "$FAIL" -ne 0 ]; then
  echo "=== 일부 단계가 실패했습니다. 위 에러 메시지를 확인하세요. ==="
  exit 1
fi
echo "=== 완료! Claude Code를 재시작하거나 /plugin 으로 확인하세요. ==="
