#!/bin/bash
# [prompt-log] removable plugin — uninstall script
#
# 동작:
#   1. ~/.claude/prompt-log/ 글로벌 데이터 디렉토리 삭제
#   2. 현재 디렉토리 하위 모든 .claude/prompt-log-consent marker 삭제
#   3. 외부 통합 흔적 grep 안내 (자동 제거는 안전을 위해 안 함)
#   4. 플러그인 자체 제거 안내 (claude plugins uninstall prompt-log)
#
# 모든 prompt-log 추가 코드는 헤더에 `# [prompt-log]` 또는 마크업 마커
# `>>> [prompt-log] integration begin` ~ `<<< [prompt-log] integration end`
# 로 표시되어 있어 grep 으로 한 번에 찾을 수 있다.

set -e

GLOBAL_DIR="$HOME/.claude/prompt-log"

echo "[prompt-log] uninstall 시작"
echo

# 1. 글로벌 데이터 삭제
if [ -d "$GLOBAL_DIR" ]; then
  rm -rf "$GLOBAL_DIR"
  echo "  ✓ 글로벌 데이터 삭제: $GLOBAL_DIR"
else
  echo "  - 글로벌 데이터 없음 (이미 삭제되었거나 활성화된 적 없음)"
fi

# 2. 현재 디렉토리 하위 모든 프로젝트 marker 삭제
echo
echo "  현재 디렉토리($(pwd)) 하위에서 'prompt-log-consent' marker 검색·삭제..."
removed=$(find . -type f -name "prompt-log-consent" -print -delete 2>/dev/null | wc -l | tr -d ' ')
echo "  ✓ 프로젝트 marker $removed개 삭제"

# active state 파일도 정리 (있으면)
echo
echo "  active state 파일 정리..."
active_removed=$(find . -type f -name "prompt-log-active.json" -print -delete 2>/dev/null | wc -l | tr -d ' ')
echo "  ✓ active state $active_removed개 삭제"

# 3. 외부 통합 마커 안내
echo
echo "[prompt-log] 외부 통합 흔적 (수동 제거 권장):"
echo "  다음 명령으로 모든 흔적 위치 확인:"
echo "      grep -rn '\\[prompt-log\\]' ~/.claude-config/ 2>/dev/null"
echo
echo "  마크업 마커로 감싸진 부분 자동 제거 (위험할 수 있어 수동 권장):"
echo "      sed -i.bak '/>>> \\[prompt-log\\] integration begin/,/<<< \\[prompt-log\\] integration end/d' \\"
echo "        ~/.claude-config/README.md ~/.claude-config/install.sh \\"
echo "        ~/.claude-config/.claude-plugin/marketplace.json \\"
echo "        ~/.claude-config/plugins/project-init/skills/project-init/SKILL.md"

# 4. 플러그인 자체 제거 안내
echo
echo "[prompt-log] 플러그인 제거 명령:"
echo "      claude plugins uninstall prompt-log"
echo
echo "[prompt-log] 완료. 잔여물 확인:"
echo "      grep -rn '\\[prompt-log\\]' ~/.claude-config/"
