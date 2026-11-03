# models/voice/deploy

Edge Impulse **Linux (x86_64)** 배포 파일(`*.eim`)을 두는 위치. voice-service 컨테이너의 `/models` 로 read-only 마운트된다.
모델 파일은 git 에 커밋하지 않는다. 현재 비어 있음 → voice-service `/api/v1/recognize` 는 503.

배포 절차와 확인 방법: [docs/light-gated-pipeline.md §3](../../../docs/light-gated-pipeline.md). `off` 클래스의 실제 소리는 박수 두 번이다.
