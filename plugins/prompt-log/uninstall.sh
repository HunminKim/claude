#!/bin/bash
# [prompt-log] removable plugin — uninstall script
#
# 동작 (⚠️ 순서 중요 — whitelist 를 지우기 전에 읽어야 타 프로젝트 잔존물을 찾는다):
#   1. whitelist(projects-allowed.json)를 먼저 읽어 각 동의 프로젝트의
#      marker/active/lock 정리
#   2. ~/.claude/prompt-log/ 글로벌 데이터 디렉토리 삭제
#   3. 현재 디렉토리 하위 잔여 marker/active 폴백 정리
#   4. 외부 통합 흔적 grep 안내 (자동 제거는 안전을 위해 안 함)
#   5. 플러그인 자체 제거 안내 (claude plugins uninstall prompt-log)
#
# 대화형(목록 확인 후 삭제)으로 하려면 /prompt-log-uninstall 스킬을 사용.
#
# 모든 prompt-log 추가 코드는 헤더에 `# [prompt-log]` 또는 마크업 마커
# `>>> [prompt-log] integration begin` ~ `<<< [prompt-log] integration end`
# 로 표시되어 있어 grep 으로 한 번에 찾을 수 있다.

set -e

GLOBAL_DIR="$HOME/.claude/prompt-log"
# 저장소 루트 — 스크립트 위치에서 자동 산출 (clone 경로 하드코딩 금지)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "[prompt-log] uninstall 시작"
echo

# 1. 동의 프로젝트 목록을 whitelist 에서 **삭제 전에** 읽어 각 프로젝트의
#    marker/active/lock 을 정리한다 — 순서 중요: 글로벌 디렉토리를 먼저 지우면
#    목록이 사라져 다른 위치 프로젝트에 수집 파일(프롬프트 본문 포함)이 잔존한다.
ALLOWED_JSON="$GLOBAL_DIR/projects-allowed.json"
if [ -f "$ALLOWED_JSON" ] && command -v python3 &>/dev/null; then
  echo "  whitelist 등록 프로젝트 정리..."
  python3 - "$ALLOWED_JSON" <<'PYEOF'
import json, sys
from pathlib import Path
try:
    allowed = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8", errors="ignore"))
except Exception:
    allowed = []
for entry in allowed:
    root = Path(entry.get("abs_path") or "")
    if not root.is_dir():
        print(f"  - 없음(스킵): {root}")
        continue
    for rel in (".claude/prompt-log-consent",
                ".claude/state/prompt-log-active.json",
                ".claude/state/prompt-log-active.tmp",
                ".claude/state/.active.lock"):
        f = root / rel
        if f.exists():
            try:
                f.unlink()
                print(f"  ✓ 삭제: {f}")
            except OSError as e:
                print(f"  ! 삭제 실패: {f} ({e})")
PYEOF
fi

# 2. 글로벌 데이터 삭제 (whitelist 순회 후)
if [ -d "$GLOBAL_DIR" ]; then
  rm -rf "$GLOBAL_DIR"
  echo "  ✓ 글로벌 데이터 삭제: $GLOBAL_DIR"
else
  echo "  - 글로벌 데이터 없음 (이미 삭제되었거나 활성화된 적 없음)"
fi

# 3. 현재 디렉토리 하위 잔여 marker 삭제 (whitelist 밖 수동 생성분 폴백)
echo
echo "  현재 디렉토리($(pwd)) 하위에서 'prompt-log-consent' marker 검색·삭제..."
removed=$(find . -type f -name "prompt-log-consent" -print -delete 2>/dev/null | wc -l | tr -d ' ')
echo "  ✓ 프로젝트 marker $removed개 삭제"

# active state 파일도 정리 (있으면)
echo
echo "  active state 파일 정리..."
active_removed=$(find . -type f \( -name "prompt-log-active.json" -o -name ".active.lock" \) -print -delete 2>/dev/null | wc -l | tr -d ' ')
echo "  ✓ active state $active_removed개 삭제"

# 4. 외부 통합 마커 안내
echo
echo "[prompt-log] 외부 통합 흔적 (수동 제거 권장):"
echo "  다음 명령으로 모든 흔적 위치 확인:"
echo "      grep -rn '\\[prompt-log\\]' \"$REPO_ROOT\" 2>/dev/null"
echo
echo "  마크업 마커로 감싸진 부분 자동 제거 (위험할 수 있어 수동 권장):"
echo "      sed -i.bak '/>>> \\[prompt-log\\] integration begin/,/<<< \\[prompt-log\\] integration end/d' \\"
echo "        \"$REPO_ROOT\"/README.md \"$REPO_ROOT\"/install.sh \\"
echo "        \"$REPO_ROOT\"/.claude-plugin/marketplace.json \\"
echo "        \"$REPO_ROOT\"/plugins/project-init/skills/project-init/SKILL.md"

# 5. 플러그인 자체 제거 안내
echo
echo "[prompt-log] 플러그인 제거 명령:"
echo "      claude plugins uninstall prompt-log"
echo
echo "[prompt-log] 완료. 잔여물 확인:"
echo "      grep -rn '\\[prompt-log\\]' \"$REPO_ROOT\""
