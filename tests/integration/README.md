# tests/integration

> **상태: 테스트 없음.** controller / db-writer / mock-device 구현 후 작성.

예정: `docker compose --profile test up` 환경에서 mock-device 가 시나리오를 발행하고,
pytest 가 MQTT 구독·WebSocket 클라이언트로 결과(LIGHT_OFF_APPLIED, DB 저장 여부)를 검증한다.

- 테스트 파일: `tests/integration/test_*.py`
- 파일이 생기면 `services-ci.yml` 의 `integration` job 이 자동 활성화된다.
- DB 검증은 실제 Supabase 대신 **별도 테스트 프로젝트** 또는 db-writer 의 dry-run 모드를 사용 (CI 에 운영 키 금지).

시나리오 정의: [`docs/test-plan.md`](../../docs/test-plan.md)
