# services/controller

> **상태: PLACEHOLDER** — 컨테이너는 빌드·실행되지만 대기 프로세스만 동작한다. 이벤트를 처리하지 않는다.

## 역할 (향후 구현)

FastAPI 기반. MQTT 디바이스 이벤트를 받아 웹 시뮬레이터에 WebSocket 으로 조명 제어 명령을 전달하고,
시뮬레이터의 적용 결과를 MQTT 로 다시 발행한다.

## Input / Output 계약

| 방향 | 채널 | 대상 | 페이로드 |
|---|---|---|---|
| IN  | MQTT subscribe | `bedlight/v1/devices/+/events` | `contracts/v1/event-envelope.schema.json` |
| IN  | MQTT subscribe | `bedlight/v1/devices/+/status` | `contracts/v1/device-status.schema.json` |
| OUT | WebSocket (server) | `ws://controller:8000/ws/simulator/{room_id}` | `LIGHT_OFF_REQUESTED` envelope (timing 에 controller 시각 추가) |
| IN  | WebSocket | 같은 연결 | `LIGHT_OFF_APPLIED` envelope (source=`simulator`) |
| OUT | MQTT publish | `bedlight/v1/simulator/{room_id}/events` | 시뮬레이터 결과 envelope (DB 저장용) |
| OUT | HTTP | `GET /healthz` | `{"status":"ok"}` (향후) |

## 처리 규칙 (설계)

1. 수신 메시지를 JSON Schema 로 검증. 실패 시 폐기 + `SYSTEM_ERROR` 로그.
2. `event_id` 기준 최근 N건 LRU 로 중복 제거 (QoS 1 재전송 대비).
3. `LIGHT_OFF_REQUESTED` 만 시뮬레이터로 전달. 나머지는 상태 표시용으로 브로드캐스트(선택).
4. `timing.controller_received_at`, `timing.controller_forwarded_at` 기록 → 병목 분석.
5. 시뮬레이터 미연결 시: 요청을 TTL 내 보관 후 재연결 시 재전송하거나 `SYSTEM_ERROR` 발행 (docs/test-plan.md 시나리오 S9).

## 환경변수

`MQTT_HOST`, `MQTT_PORT`, `MQTT_TOPIC_PREFIX`, `CONTROLLER_PORT` — Supabase 키는 **주입하지 않는다.**

## 향후 디렉터리 구조 (예정)

```
services/controller/
  app/main.py          # FastAPI app, /healthz, /ws/simulator/{room_id}
  app/mqtt_client.py
  app/contracts.py     # contracts/v1 스키마 로드/검증
  tests/test_*.py      # services-ci 가 자동 감지하여 실행
  requirements.txt
```
