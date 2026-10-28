# 🚀 MagNavi

Python 서버의 모델 준비·입력 처리: [모델 준비·입력 검증·HTTP 오류 계약과 검증 안내](docs/troubleshooting/model-readiness-and-input-validation.md)(2026-10-27). 아래 프로젝트 소개의 과거 성능 수치는 이번 작업에서 재검증한 결과가 아니다.

gRPC 구현·합성 입력 검증(2026-10-28): [계약 v2·실행·검증 안내](docs/api/GRPC_CONTRACT.md). 기존 모델의 첫 샘플 예측을 유지하고 측정별 세션·자원 한도·준비 상태를 연결했다. 실제 센서·운영 TLS·배포·부하 측정은 후속이다.

문서: [프로젝트 개요](PROJECT_OVERVIEW.md) · [구현 계획](IMPLEMENTATION_PLAN.md) · [커밋 규칙](docs/convention/commit-convention.md) · [브랜치·PR 규칙](docs/convention/branch-convention.md).

게시 상태(2026-10-28): [기준 검증 PR #2](https://github.com/MagNavi-server/magnavi-positioning-service/pull/2), [추론 코어 PR #4](https://github.com/MagNavi-server/magnavi-positioning-service/pull/4), [gRPC PR #6](https://github.com/MagNavi-server/magnavi-positioning-service/pull/6)이 중앙 `develop`에 머지됐다. 대응 Spring 계약 v2는 아직 Spring 로컬 변경이며 별도 게시·전환이 필요하다. Python의 운영 배포는 수행하지 않았다.

### 지구자기장 기반 실내 통합 내비게이션 시스템

> 실내에서도 GPS처럼 길을 찾을 수 있을까?  
> → 자기장 + 딥러닝 + PDR로 해결한 프로젝트

---

## 📌 프로젝트 개요

MagNavi는 스마트폰 센서만을 활용하여  
실내에서도 정확한 위치를 제공하는 **실내 내비게이션 시스템**입니다.

기존 Wi-Fi, Beacon 기반 실내측위의 한계를 해결하기 위해  
-> **지구자기장 + 딥러닝 + PDR(보행자 추측항법)**을 결합했습니다.

<br>

총 개발 인원 : 6명, 프론트 : 3명, 백엔드 : 3명

나의 역할 : 백엔드
- 서버 및 데이터베이스 구축
- 자기장 데이터 수집 및 측정
---


## 🏗️ 시스템 아키텍처

<p align="center">
  <img src="image/마그내비 아키텍쳐.drawio.png" alt="MagNavi Architecture" width="900"/>
</p>
<p align="center">
  <img src="image/마그내비3.drawio.png" alt="MagNavi Architecture" width="900"/>
</p>


- Mobile App에서 센서 데이터 수집
- 딥러닝 모델이 위치 예측 수행
- PDR 기반 이동 추정 및 보정
- 최종 실내 위치 반환
  
<p>
<img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" />
<img src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white" />
<img src="https://img.shields.io/badge/MySQL-4479A1?style=for-the-badge&logo=mysql&logoColor=white" />
<img src="https://img.shields.io/badge/AWS-232F3E?style=for-the-badge&logo=amazonaws&logoColor=white" />
</p>
 
---
## 트러블 슈팅 
- 위치 정확도가 높지 않아 원인 분석 및 논문 참고 후, 자기장 왜곡 보정 알고리즘 적용 → 정확도 25% 향상 (72.5% → 97.3%)
- DB 구조 단순화 → 데이터 처리 효율 30% 개선
- AWS EC2 + Git 배포 자동화 → 배포 시간 30% 단축
- 모델 호출 최적화 → 운영 비용 10% 절감
---


## 🎯 개발 배경

- GPS는 실내에서 사용 불가
- Wi-Fi / Beacon 기반 측위는 인프라 비용 + 신호 간섭 문제 존재

➡️ 해결  
-> 스마트폰 센서만으로 위치 추정

---

## 💡 핵심 아이디어

- 실내 공간마다 고유한 자기장 패턴 존재
- 이를 딥러닝으로 학습 → 위치 추정

```
지자기 데이터 → 딥러닝 모델 → 위치 예측
                   +
               PDR 보정
                   ↓
             최종 위치 출력
```

---


## ⚙️ 주요 기능

### 📍 실내 위치 추정
- 지자기 Fingerprinting 기반
- 추가 인프라 없이 구현

### 🧠 딥러닝 기반 위치 예측
- MLP 모델 활용
- 자기장 + 방향 데이터 입력

### 🚶 PDR 기반 이동 추정
- 걸음 수, 보폭, 방향 계산
- 이동 경로 추적

### 🔄 위치 보정
- 지자기 + PDR 결합
- 정확도 향상
---

## 📊 성능

- 정확도: **72.5% → 97.3%**
- 목표 오차: **1m 이내 달성**

---

## 📸 Demo

[Demo 영상](https://www.youtube.com/watch?v=kqR17mF1d1U)

---


## 🏆 성과

- 🥇 캡스톤 디자인 1위  
- 🏆 MIDAS CDP 최우수상  
- 📄 KCI 학술지 게재  🔗 [논문(DBpia)](https://www.dbpia.co.kr/journal/articleDetail?nodeId=NODE12567180) | 
