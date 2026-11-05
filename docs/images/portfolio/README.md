# 포트폴리오 이미지 출처

2026-11-05에 사용자가 제공한 [Backend Developer Portfolio](https://app.notion.com/p/400dedc0d781803fb123c5aafc50e8b4)의 MagNavi 영역에서 가져왔다. 원본 이미지 내용은 수정하지 않고 파일 이름만 README의 설명에 맞췄다. Notion의 다운로드 링크는 만료되므로 저장소의 상대 경로로 참조한다.

| 파일 | 포트폴리오의 위치 | 설명 |
|---|---|---|
| [architecture.png](architecture.png) | MagNavi / 아키텍처 구조 | Spring·Python·DB·외부 검색·배포 구성도. 앱·운영 모델의 실제 전환 상태는 루트 README에서 별도 설명 |
| [app-demo.png](app-demo.png) | 자기장 보정 / 문제 상황 | 기존 앱의 [시연 영상](https://www.youtube.com/watch?v=kqR17mF1d1U) 캡처. 새 Spring WebSocket v2 앱의 시연이 아님 |
| [magnetic-calibration.png](magnetic-calibration.png) | 01. 자기장 보정 알고리즘 적용 | 보정 원리와 기존 프로젝트 실험의 정확도 기록 |
| [realtime-reconnection.png](realtime-reconnection.png) | 02. WebSocket·gRPC 기반 실시간 통신 | 측정별 스트림·재접속·늦은 결과 차단·자원 제한 |
| [model-call-interval.png](model-call-interval.png) | 03. 모델 서버 호출 횟수 50% 감소 | 기존 HTTP 예측 요청 주기 조정. 현재 스트리밍 서버의 입력 주기 설정이 아님 |

[프로젝트 README로 돌아가기](../../../README.md)
