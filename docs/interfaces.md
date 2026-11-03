# BedLight AI — 인터페이스 계약

계약 원본: [`contracts/v1/`](../contracts/v1/) (JSON Schema Draft 2020-12). 이 문서와 스키마가 다르면 **스키마가 우선**한다.

## 1. MQTT Topic

| Topic | 스키마 | QoS | Retain | 발행 | 구독 |
|---|---|---|---|---|---|
| `bedlight/v1/devices/{device_id}/events` | event-envelope | 1 | no | ESP32, mock-device | controller, db-writer |
| `bedlight/v1/devices/{device_id}/status` | device-status | 1 | **yes** (+ LWT `offline`) | ESP32, mock-device | controller, db-writer |
| `bedlight/v1/simulator/{room_id}/events` | event-envelope | 1 | no | controller | db-writer |

- `device_id`, `room_id`: 소문자·숫자·하이픈 (`^[a-z0-9][a-z0-9-]+$`). Topic 경로 값과 페이로드 값은 일치해야 하며 불일치 시 폐기.
- 이미지·오디오 원본은 MQTT 로 보내지 않는다 (`message_size_limit 65536`).

## 2. 공통 이벤트 Envelope

| 필드 | 타입 | 설명 |
|---|---|---|
| `schema_version` | string `1.x.y` | 계약 버전 |
| `event_id` | UUID | 이벤트 고유 ID. 중복 제거 키 (DB UNIQUE) |
| `trace_id` | UUID | 명령 흐름 전체를 연결 (LYING → … → APPLIED) |
| `source` | `device` \| `mock` \| `simulator` \| `controller` | 실기기/가상 구분 |
| `device_id` | string | 흐름의 원 디바이스 (시뮬레이터 이벤트도 복사) |
| `event_type` | enum (7종) | 아래 표 |
| `sequence` | uint32 | 발행자별 단조 증가. 유실·역순 탐지 |
| `model` | object \| null | `{name, version, task, runtime}`. 추론 이벤트는 필수 |
| `data` | object | 이벤트별 페이로드 |
| `timing` | object | 구간 측정값. 각 컴포넌트는 **자기 필드만 추가** |

`timing` 필드: `device_uptime_ms`, `emitted_at`, `capture_ms`, `dsp_ms`, `inference_ms`,
`controller_received_at`, `controller_forwarded_at`, `simulator_received_at`, `simulator_applied_at`, `render_ms`.

## 3. 이벤트 종류

| event_type | 발행 | model | data |
|---|---|---|---|
| `LYING_DETECTED` | device/mock | 필수 | `label="lying"`, `confidence`, `threshold`, `consecutive_frames?` |
| `LYING_LOST` | device/mock | 필수 | `label`, `confidence`, `lying_duration_ms?` |
| `LISTENING_STARTED` | device/mock | null | `reason` (`lying_detected`\|`manual`), `window_ms` |
| `VOICE_KEYWORD_DETECTED` | device/mock | 필수 | `keyword="off"`, `confidence`, `threshold` |
| `LIGHT_OFF_REQUESTED` | device/mock | null | `room_id`, `light_id`, `cause_event_ids[]` |
| `LIGHT_OFF_APPLIED` | simulator | null | `room_id`, `light_id`, `request_event_id`, `result` (`applied`\|`already_off`\|`rejected`), `reason?` |
| `SYSTEM_ERROR` | any | null | `component`, `code`, `message`, `recoverable` |

예시: `contracts/v1/examples/valid/`.

## 4. Controller ↔ Simulator WebSocket (예정)

- Endpoint: `ws://<controller>:8000/ws/simulator/{room_id}`
- 프레임: UTF-8 JSON, 1 메시지 = 1 envelope
- Controller → Simulator: `LIGHT_OFF_REQUESTED` (+ 상태 표시용 이벤트 선택)
- Simulator → Controller: `LIGHT_OFF_APPLIED` (요청과 같은 `trace_id`, `data.request_event_id` = 요청 `event_id`)
- 재연결: 지수 백오프(1s → 30s 상한). 재연결 직후 Controller 는 현재 조명 상태를 재전송(예정).

## 5. 컨테이너별 Input / Output

| 컨테이너 | Input | Output |
|---|---|---|
| **mqtt-broker** | MQTT CONNECT/PUBLISH (TCP 1883) from ESP32, mock-device, controller | 구독자에게 메시지 중계, retained status, LWT |
| **controller** | MQTT `devices/+/events`, `devices/+/status`; WS `LIGHT_OFF_APPLIED` | WS `LIGHT_OFF_REQUESTED`; MQTT `simulator/{room}/events`; HTTP `/healthz` |
| **db-writer** | MQTT `devices/+/events`, `simulator/+/events` (`devices/+/status` 선택) | Supabase `device_events` INSERT (HTTPS); 구조화 로그 |
| **simulator-web** | HTTP(브라우저 접속); WS `LIGHT_OFF_REQUESTED` | 렌더링된 침실 화면; WS `LIGHT_OFF_APPLIED` |
| **mock-device** | CLI 인자/시나리오 파일 | MQTT `devices/{id}/events`, `devices/{id}/status` (`source="mock"`) |

## 6. 오류 처리 공통 규칙

| 상황 | 처리 |
|---|---|
| JSON 파싱/스키마 검증 실패 | 폐기, 로그(`invalid_event` 카운터), 재발행하지 않음 |
| 동일 `event_id` 재수신 | 무시 (정상 상황으로 간주, `duplicate` 카운터) |
| `sequence` 역행/점프 | 경고 로그, 처리 계속 (재부팅 시 리셋 가능) |
| 하위 시스템 장애 | 해당 컴포넌트가 `SYSTEM_ERROR` 발행 |
