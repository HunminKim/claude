#!/bin/bash
# 새 환경에서 Claude Code 플러그인 일괄 설치
# 사용법: bash install.sh

set -e

echo "=== Claude Code 플러그인 설치 ==="

# 1. 마켓플레이스 등록
echo "[1/2] 마켓플레이스 등록 중..."
claude plugins marketplace add HunminKim/claude --name hunminkim 2>/dev/null || \
  echo "      (이미 등록됨)"

# 2. 플러그인 설치
echo "[2/2] 플러그인 설치 중..."

# 공식 플러그인
OFFICIAL=(
  "code-review"
  "code-simplifier"
  "skill-creator"
  "hookify"
)
for plugin in "${OFFICIAL[@]}"; do
  echo "      $plugin@claude-plugins-official"
  claude plugins install "${plugin}@claude-plugins-official" --yes 2>/dev/null || \
    echo "      (이미 설치됨: $plugin)"
done

# 개인 플러그인
echo "      project-init@hunminkim"
claude plugins install project-init@hunminkim --yes 2>/dev/null || \
  echo "      (이미 설치됨: project-init)"

echo ""
echo "=== 완료! Claude Code를 재시작하거나 /reload-plugins 를 실행하세요. ==="
