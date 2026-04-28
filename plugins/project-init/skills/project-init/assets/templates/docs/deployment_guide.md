# 배포 가이드 (Deployment Guide)

- **프로젝트명**: {{PROJECT_NAME}}
- **기술 스택**: {{TECH_STACK}}
- **최초 작성**: {{DATE}} (KST)

> 개발 중 환경 세팅 정보를 여기에 누적한다. 최종 완료 후 정리한다.

---

## 환경 요구사항

| 항목 | 버전/사양 |
|------|---------|
| OS | |
| Runtime | |
| 기타 의존성 | |

---

## 환경 변수

```env
# 필수
KEY=설명

# 선택
KEY=설명
```

---

## 로컬 실행

```bash
# 1. 의존성 설치
# TBD

# 2. 환경 변수 설정
cp .env.example .env

# 3. 실행
# TBD
```

---

## Docker 배포

```dockerfile
# Dockerfile 내용
# TBD
```

```bash
# 빌드
docker build -t {{PROJECT_NAME}} .

# 실행
docker run -p 8000:8000 --env-file .env {{PROJECT_NAME}}
```

---

## 개발 중 환경 세팅 메모

> 개발하면서 발견한 환경 관련 주의사항을 여기에 기록한다.

- {{DATE}}: 초기 세팅
