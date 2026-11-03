# contracts/v1 — 이벤트 계약 (JSON Schema Draft 2020-12)

| 파일 | 대상 |
|---|---|
| `event-envelope.schema.json` | 모든 이벤트 (`.../devices/{id}/events`, `.../simulator/{room}/events`, Controller↔Simulator WebSocket) |
| `device-status.schema.json` | `.../devices/{id}/status` (retained, LWT) |
| `topics.json` | Topic ↔ Schema ↔ QoS ↔ 발행/구독자 매핑 |
| `examples/valid/*.json` | 반드시 통과해야 하는 예시 |
| `examples/invalid/*.json` | 반드시 거부되어야 하는 예시 (계약 회귀 방지) |

예시 파일명 규칙: `event-*.json` → envelope 스키마, `status-*.json` → status 스키마.

검증:

```bash
pip install -r scripts/requirements-ci.txt
python scripts/validate_contracts.py            # 스키마 자체 유효성 + 예시 검증
```

## 버전 정책

- `schema_version` 은 `1.MINOR.PATCH`. 필드 **추가(선택 필드)** 는 MINOR 증가.
- 필수 필드 추가/삭제, 타입 변경, enum 값 삭제는 **v2** 디렉터리를 새로 만들고 topic 도 `bedlight/v2/...` 로 분리.
- 모든 소비자(controller, db-writer, simulator)는 알 수 없는 MINOR 버전도 수용하되 `additionalProperties: false` 로 인해
  새 필드가 있으면 거부되므로, MINOR 변경 시 스키마를 먼저 배포한 뒤 발행자를 업데이트한다.

## 이벤트별 규칙 요약

| event_type | 허용 source | model | data 필수 필드 |
|---|---|---|---|
| LYING_DETECTED | device, mock | 필수 | label=`lying`, confidence, threshold |
| LYING_LOST | device, mock | 필수 | label, confidence |
| LISTENING_STARTED | device, mock | null | reason, window_ms |
| VOICE_KEYWORD_DETECTED | device, mock | 필수 | keyword=`off`, confidence, threshold |
| LIGHT_OFF_REQUESTED | device, mock | null | room_id, light_id, cause_event_ids |
| LIGHT_OFF_APPLIED | simulator | null | room_id, light_id, request_event_id, result |
| SYSTEM_ERROR | 모두 | null | component, code, message, recoverable |
