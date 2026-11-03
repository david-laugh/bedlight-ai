# tests/mock-device

> **상태: PLACEHOLDER** — 컨테이너는 안내 메시지만 출력하고 종료한다. MQTT 에 아무것도 발행하지 않는다.

Compose profile `test` 에서만 실행된다:

```bash
docker compose --profile test up --build mock-device
```

## 역할 (향후 구현)

ESP32 없이 전체 파이프라인을 시험하기 위해 XIAO ESP32S3 와 **동일한 계약**의 MQTT 이벤트를 생성한다.

- `source` 는 항상 `"mock"` — 실제 디바이스(`"device"`)와 DB/로그에서 구분된다.
- `device_id` 는 `mock-` prefix 사용 권장 (예: `mock-xiao-01`).
- `model` 필드에는 실제 모델 메타데이터 대신 `name: "mock-..."` 를 사용한다.

## Output 계약

| 채널 | Topic | 페이로드 |
|---|---|---|
| MQTT publish QoS1 | `bedlight/v1/devices/{device_id}/events` | `event-envelope.schema.json` |
| MQTT publish QoS1 retained + LWT | `bedlight/v1/devices/{device_id}/status` | `device-status.schema.json` |

## 예정 시나리오 (docs/test-plan.md 와 연동)

| ID | 시나리오 | 파라미터 |
|---|---|---|
| S1 | 정상 단일 명령: LYING_DETECTED → LISTENING_STARTED → VOICE_KEYWORD_DETECTED → LIGHT_OFF_REQUESTED | 1회 |
| S2 | 연속 명령 | N회, 간격 지정 |
| S3–S5 | 부하 | 1 / 5 / 20 events/s, 지속시간 |
| S8 | 중복 수신 | 동일 `event_id` 재발행 |
| — | 잘못된 이벤트 | 스키마 위반 페이로드 (검증 경로 시험) |

예정 CLI: `python -m mock_device --scenario S1 --rate 5 --duration 60`
