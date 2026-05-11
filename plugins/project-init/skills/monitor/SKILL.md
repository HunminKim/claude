---
name: monitor
description: 학습·배치 작업 진행 상황을 자동 계산된 간격으로 표 형식 보고. "/monitor 2h", "/monitor 30m", "모니터링 시작 2시간", "학습 모니터링 10h" 등으로 호출. 총 소요 시간을 받아 체크 간격을 자동 계산 (total/10, 하한 1분·상한 60분).
---

# Monitor Skill

학습·배치 작업 진행 상황을 자동 간격으로 표 형식 보고한다.

## 호출 형식

- `/monitor 2h` — 총 2시간짜리 작업
- `/monitor 30m` — 총 30분짜리 작업
- `/monitor 1h30m` — 1시간 30분
- `/monitor 90` — 숫자만 입력 시 분 단위

## 시간 파싱 규칙

| 입력 | 초 변환 |
|------|--------|
| `2h` | 7200 |
| `30m` | 1800 |
| `1h30m` | 5400 |
| `90` | 5400 (분 단위) |
| `3600s` | 3600 |

## 간격 계산

```
interval_sec = max(60, min(3600, total_sec / 10))
total_checks = ceil(total_sec / interval_sec)
```

예시:
- 7분 → 간격 42초 → 하한 적용 → 60초 (약 7회)
- 30분 → 간격 3분 (10회)
- 2시간 → 간격 12분 (10회)
- 10시간 → 간격 60분 (10회, 상한 적용)

---

## 1단계: 최초 호출

### 1-1. 상태 확인

루프 재진입인지 확인한다. 인자가 `__loop__` 로 시작하면 → **2단계**로 바로 이동.

### 1-2. 시간 파싱

인자에서 총 소요 시간을 파싱한다. 인자가 없으면 사용자에게 질문:
```
총 예상 소요 시간을 알려주세요. (예: 2h, 30m, 1h30m)
```

### 1-3. 모니터링 필드 로드

현재 프로젝트 `CLAUDE.md`에서 `## 모니터링 필드` 섹션을 찾아 파싱한다.

**섹션 형식**:
```markdown
## 모니터링 필드
- Epoch: `grep -oP 'Epoch \K\d+/\d+' train.log | tail -1`
- Loss: `grep -oP 'loss=\K[\d.]+' train.log | tail -1`
- GPU: `nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader`
```

형식: `- 레이블: \`shell 명령어\``

섹션이 없으면 시간 정보만 표시한다.

### 1-4. 첫 번째 체크

**2단계** 체크 로직을 check_num=1로 즉시 실행한다.

### 1-5. 다음 체크 예약

```python
ScheduleWakeup(
    delaySeconds=interval_sec,
    prompt=f'/monitor __loop__ {{"start":"{start_iso}","total":{total_sec},"interval":{interval_sec},"check":2,"total_checks":{total_checks}}}',
    reason=f"모니터링 #2/{total_checks} — {interval_human} 후 체크"
)
```

---

## 2단계: 체크 실행 (매 wake마다)

### 2-1. 상태 파싱

프롬프트에서 JSON 상태를 파싱한다:
```
/monitor __loop__ {"start":"...","total":7200,"interval":720,"check":3,"total_checks":10}
```

### 2-2. 시간 계산

```
now = 현재 시각 (KST)
elapsed_sec = now - start_time
remaining_sec = max(0, total_sec - elapsed_sec)
progress_pct = min(100, round(elapsed_sec / total_sec * 100))
```

### 2-3. 모니터링 필드 실행

CLAUDE.md `## 모니터링 필드` 섹션을 다시 읽어 각 명령어를 Bash로 실행한다.
- 결과는 첫 번째 의미 있는 줄만 추출
- 실행 실패 시 `(오류)` 표시

### 2-4. 표 출력

아래 형식을 **매 체크마다 동일하게** 출력한다:

```
┌─ 모니터링 #3/10 · 2026-05-11 14:32 (KST) ──────────────┐
│ 경과  1h 12m / 2h 00m  잔여 ~48m  진행 60%  간격 12m    │
├─────────────────────────────────────────────────────────┤
│ Epoch        45/100                                     │
│ Loss         0.0234                                     │
│ GPU          87%                                        │
│ 체크포인트    checkpoints/epoch_45.pt                    │
├─────────────────────────────────────────────────────────┤
│ 상태  ✅ 정상                                            │
└─────────────────────────────────────────────────────────┘
```

**상태 판단**:
- `✅ 정상`: 모든 필드 정상 실행
- `⚠️ [내용]`: 명령어 실패, 빈 출력, NaN/inf 감지, 이전 대비 급변

**모니터링 필드가 없을 때** (시간 정보만):
```
┌─ 모니터링 #3/10 · 2026-05-11 14:32 (KST) ──────────────┐
│ 경과  1h 12m / 2h 00m  잔여 ~48m  진행 60%  간격 12m    │
├─────────────────────────────────────────────────────────┤
│ (CLAUDE.md에 ## 모니터링 필드 섹션을 추가하면           │
│  프로젝트별 지표를 여기에 표시합니다)                    │
├─────────────────────────────────────────────────────────┤
│ 상태  ✅ 정상                                            │
└─────────────────────────────────────────────────────────┘
```

### 2-5. 종료 또는 재예약

```
if elapsed_sec >= total_sec OR check_num >= total_checks:
    → 완료 메시지 출력 후 종료
else:
    → ScheduleWakeup으로 다음 체크 예약
       check_num + 1, 동일 interval
```

**완료 메시지**:
```
✅ 모니터링 완료 — 총 {total_checks}회 체크 / {elapsed_human} 경과
```

---

## CLAUDE.md 모니터링 필드 섹션 예시

사용자에게 이 섹션 추가를 안내할 때 아래 예시를 보여준다:

```markdown
## 모니터링 필드
- Epoch: `grep -oP 'Epoch \K\d+/\d+' train.log | tail -1`
- Loss: `grep -oP 'loss=\K[\d.eE+\-]+' train.log | tail -1`
- GPU: `nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader 2>/dev/null`
- 체크포인트: `ls -t checkpoints/*.pt 2>/dev/null | head -1 | xargs basename 2>/dev/null`
- 남은 시간: `grep -oP 'ETA \K[\d:hms ]+' train.log | tail -1`
```
