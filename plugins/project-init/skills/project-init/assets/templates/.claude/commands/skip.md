---
description: verifier 검증 실패(❌) 후 현재 변경사항을 보존하며 plan-gate를 마감한다. /keep 도 동일하게 동작한다.
---

verifier 검증이 실패했지만 현재 변경사항을 되돌리지 않고 gate를 마감할 때 사용한다.
문제를 인지한 채로 진행하며, 발견된 문제는 다음 gate 주기에서 별도 처리한다.

이 커맨드는 `plan_approval.py` UserPromptSubmit 훅이 처리한다.
사용자가 `/skip` 또는 `/keep` 을 입력하면 plan-gate 상태가 자동으로 전이된다.
