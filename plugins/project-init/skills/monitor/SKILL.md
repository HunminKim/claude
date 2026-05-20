---
name: monitor
description: 학습·배치 작업 진행 상황을 자동 계산된 간격으로 표 형식 보고. "/monitor 2h", "/monitor 30m training", "모니터링 시작 2시간", "학습 모니터링 10h" 등으로 호출. 총 소요 시간을 받아 체크 간격을 자동 계산 (total/10, 하한 1분·상한 60분). 프리셋은 .claude/monitor_presets.yaml로 관리.
---

# Monitor Skill

학습·배치 작업 진행 상황을 자동 간격으로 표 형식 보고한다.

## 호출 형식

- `/monitor 2h` — 총 2시간, 자동 감지 또는 필드 없이 시간만
- `/monitor 2h training` — training 프리셋 사용
- `/monitor 30m preprocessing` — preprocessing 프리셋 사용
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

## 프리셋 파일: `.claude/monitor_presets.yaml`

CLAUDE.md는 건드리지 않는다. 프리셋은 `.claude/monitor_presets.yaml`에 별도 관리한다.

**파일 형식**:
```yaml
training:
  - label: Epoch
    cmd: "grep -oP 'Epoch \\K\\d+/\\d+' train.log | tail -1"
  - label: Loss
    cmd: "grep -oP 'loss=\\K[\\d.eE+\\-]+' train.log | tail -1"
  - label: GPU
    cmd: "nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader 2>/dev/null"
  - label: 체크포인트
    cmd: "ls -t checkpoints/*.pt 2>/dev/null | head -1 | xargs basename 2>/dev/null"

preprocessing:
  - label: 처리 파일
    cmd: "wc -l < output.jsonl 2>/dev/null"
  - label: 진행률
    cmd: "grep -c 'done' process.log 2>/dev/null"
```

파일이 없거나 프리셋 이름이 없으면 자동 감지 흐름으로 진행한다.

---

## 1단계: 최초 호출

### 1-1. 상태 확인

인자가 `__loop__` 로 시작하면 → **2단계**로 바로 이동.

### 1-2. 시간 + 프리셋 파싱

인자에서 총 소요 시간과 프리셋 이름을 파싱한다.
- 예: `2h training` → total=7200, preset="training"
- 예: `2h` → total=7200, preset=없음

인자가 없으면 **AskUserQuestion 툴**로 묻는다:
- 질문: "총 예상 소요 시간을 알려주세요. 프리셋 이름도 함께 입력할 수 있습니다. (예: 2h training)"
- 옵션: `["30분", "1시간", "2시간", "기타 (직접 입력)"]`

### 1-3. 모니터링 필드 결정

우선순위:
1. **프리셋 지정됨** → `.claude/monitor_presets.yaml`에서 해당 프리셋 로드
2. **프리셋 없음** → 자동 감지 시도:
   - `ps aux`로 실행 중인 python/bash 프로세스 확인
   - 최근 수정된 `.log` 파일 확인 (`find . -name "*.log" -newer /tmp -maxdepth 3`)
   - 감지 결과를 사용자에게 제안:
     ```
     다음 지표를 감지했습니다:
     - train.log 발견 → Epoch, Loss 추출 가능
     - nvidia-smi 사용 가능 → GPU 추출 가능
     이 필드로 진행할까요? 수정하려면 말씀해주세요.
     프리셋으로 저장하려면 이름을 알려주세요. (예: training)
     ```
3. **감지 실패** → 시간 정보만 표시

### 1-4. 프리셋 저장 (선택)

사용자가 프리셋 이름을 알려주면 `.claude/monitor_presets.yaml`에 저장한다.
파일이 없으면 새로 생성, 있으면 해당 이름 항목만 추가/덮어쓴다.

### 1-5. 첫 번째 체크

**2단계** 체크 로직을 check_num=1로 즉시 실행한다.

### 1-6. 다음 체크 예약

```python
ScheduleWakeup(
    delaySeconds=interval_sec,
    prompt=f'/monitor __loop__ {{"start":"{start_iso}","total":{total_sec},"interval":{interval_sec},"check":2,"total_checks":{total_checks},"preset":"{preset_name}"}}',
    reason=f"모니터링 #2/{total_checks} — {interval_human} 후 체크"
)
```

---

## 2단계: 체크 실행 (매 wake마다)

### 2-1. 상태 파싱

프롬프트에서 JSON 상태를 파싱한다:
```
/monitor __loop__ {"start":"...","total":7200,"interval":720,"check":3,"total_checks":10,"preset":"training"}
```

### 2-2. 시간 계산

```
now = 현재 시각 (KST)
elapsed_sec = now - start_time
remaining_sec = max(0, total_sec - elapsed_sec)
progress_pct = min(100, round(elapsed_sec / total_sec * 100))
```

### 2-3. 모니터링 필드 실행

`preset` 값이 있으면 `.claude/monitor_presets.yaml`에서 해당 프리셋 로드 후 각 cmd를 Bash로 실행.
- 결과는 첫 번째 의미 있는 줄만 추출
- 실행 실패 또는 빈 출력 시 `—` 표시

### 2-4. 표 출력

아래 형식을 **매 체크마다 동일하게** 출력한다:

```
┌─ 모니터링 #3/10 · 2026-05-11 14:32 (KST) ──────────────┐
│ 경과  1h 12m / 2h 00m  잔여 ~48m  진행 60%  간격 12m    │
├─────────────────────────────────────────────────────────┤
│ Epoch        45/100                                     │
│ Loss         0.0234                                     │
│ GPU          87%                                        │
│ 체크포인트    epoch_45.pt                                │
├─────────────────────────────────────────────────────────┤
│ 상태  ✅ 정상                                            │
└─────────────────────────────────────────────────────────┘
```

**상태 판단**:
- `✅ 정상`: 모든 필드 정상 실행
- `⚠️ [내용]`: 명령어 실패, NaN/inf 감지, 이전 체크 대비 급변

**프리셋 없을 때** (시간 정보만):
```
┌─ 모니터링 #3/10 · 2026-05-11 14:32 (KST) ──────────────┐
│ 경과  1h 12m / 2h 00m  잔여 ~48m  진행 60%  간격 12m    │
├─────────────────────────────────────────────────────────┤
│ (프리셋 없음 — /monitor 2h training 형식으로 호출하거나  │
│  .claude/monitor_presets.yaml 에 프리셋을 추가하세요)    │
├─────────────────────────────────────────────────────────┤
│ 상태  ✅ 정상                                            │
└─────────────────────────────────────────────────────────┘
```

### 2-5. 종료 또는 재예약

```
if elapsed_sec >= total_sec OR check_num >= total_checks:
    → 완료 메시지 출력 후 종료
else:
    → ScheduleWakeup으로 다음 체크 예약 (check_num+1, 동일 interval, 동일 preset)
```

**완료 메시지**:
```
✅ 모니터링 완료 — 총 {total_checks}회 체크 / {elapsed_human} 경과
```
