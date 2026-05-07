---
description: 현재 프로젝트의 prompt-log 동의 여부와 글로벌 수집 통계(레코드 수, 파일 크기)를 출력한다.
allowed-tools: Bash
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/prompt_log_cli.py" status
```

이 프로젝트가 prompt-log 수집에 동의되어 있는지, 그리고 전체 저장 현황(월별 파일 수, 누적 레코드, 데이터 크기)을 확인한다.
