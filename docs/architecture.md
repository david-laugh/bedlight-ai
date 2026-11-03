# BedLight AI — 아키텍처

## 1. 개요

XIAO ESP32S3 Sense 가 **온디바이스(Edge)에서** 두 모델을 실행한다.

1. Vision: 카메라 프레임 → 침대에 누운 상태(`lying`) 분류
2. Voice (KWS): 누운 상태일 때만 PDM 마이크를 열고 `"off"` 키워드 인식

두 조건이 충족되면 디바이스가 `LIGHT_OFF_REQUESTED` 를 MQTT 로 발행하고, Controller 가 이를 웹 시뮬레이터로 전달하여
가상 침실의 조명을 끈다. 모든 이벤트는 DB Writer 를 통해 Supabase 에 저장된다.

**AI 추론은 ESP32 에서만 수행한다.** Docker 서비스는 이벤트 중계, 가상 환경, 저장, 성능 측정을 담당한다.

## 2. 구성도

```
                      ┌──────────────────────── Docker Compose (bedlight-net) ─────────────────────────┐
┌─────────────────┐   │                                                                                 │
│ XIAO ESP32S3    │   │  ┌──────────────┐  devices/+/events  ┌────────────┐  WebSocket   ┌────────────┐ │
│ Sense           │──MQTT─▶│ mqtt-broker  │───────────────────▶│ controller │◀────────────▶│ simulator- │ │
│ (Vision + KWS)  │   │  │ (Mosquitto)  │◀───────────────────│ (FastAPI)  │  REQUESTED / │ web        │ │
└─────────────────┘   │  │              │  simulator/+/events└────────────┘  APPLIED     │ (React)    │ │
┌─────────────────┐   │  │              │                                                └────────────┘ │
│ mock-device     │──MQTT─▶│              │  devices/+/events, simulator/+/events                          │
│ (profile: test) │   │  │              │──────────────────────────────▶┌───────────┐                   │
└─────────────────┘   │  └──────────────┘                               │ db-writer │                   │
                      │                                                 └─────┬─────┘                   │
                      └───────────────────────────────────────────────────────┼─────────────────────────┘
                                                                              │ HTTPS (PostgREST, secret key)
                                                                       ┌──────▼───────┐
                                                                       │ Supabase     │ (외부 관리형)
                                                                       │ PostgreSQL   │
                                                                       └──────────────┘
```

## 3. 데이터 흐름 (정상 명령 1회, 동일 `trace_id`)

| # | 주체 | 이벤트 / 동작 | 채널 |
|---|---|---|---|
| 1 | ESP32 | 누움 감지 → `LYING_DETECTED` | MQTT `devices/{id}/events` |
| 2 | ESP32 | 마이크 활성화 → `LISTENING_STARTED` | MQTT |
| 3 | ESP32 | "off" 인식 → `VOICE_KEYWORD_DETECTED` | MQTT |
| 4 | ESP32 | 조건 충족 → `LIGHT_OFF_REQUESTED` | MQTT |
| 5 | Controller | 검증·중복제거 후 시뮬레이터로 전달 | WebSocket |
| 6 | Simulator | 조명 OFF 렌더 → `LIGHT_OFF_APPLIED` | WebSocket |
| 7 | Controller | 결과를 MQTT 로 발행 | MQTT `simulator/{room}/events` |
| * | DB Writer | 1~4, 7 을 구독하여 Supabase 에 저장 | HTTPS |

## 4. 설계 결정

| 결정 | 이유 |
|---|---|
| 조명 OFF 판단(누움 ∧ 키워드)을 **디바이스**에서 수행 | AI 추론과 판단을 Edge 에 두어 네트워크 지연과 무관하게 일관된 동작. Controller 는 전달·검증에 집중 |
| Controller ↔ Simulator 는 WebSocket, 나머지는 MQTT | 브라우저는 MQTT(TCP 1883)에 직접 붙지 않음. 양방향 실시간 + 응답(APPLIED) 필요 |
| 시뮬레이터 결과를 Controller 가 MQTT 로 재발행 | DB Writer 가 MQTT 만 구독하면 되도록 단일 수집 경로 유지 |
| DB Writer 분리 | DB 지연/장애가 조명 제어 경로(Controller)에 영향을 주지 않도록 격리. 병목 측정도 분리 |
| Supabase 는 외부 관리형 | 로컬 컨테이너 운영 부담 제거. 키는 DB Writer 에만 주입 |
| `source` 필드로 실기기/모의 구분 | Mock 데이터가 실험 결과와 섞이지 않도록 DB 레벨에서 필터 가능 |
| QoS 1 + `event_id` 중복 제거 | at-least-once 전달을 수용하고 소비자 측에서 멱등 처리 |

## 5. 서비스 상태 (현재)

| 서비스 | 상태 |
|---|---|
| mqtt-broker | 동작 (개발용 무인증, localhost 바인딩) |
| controller | placeholder (대기 프로세스) |
| db-writer | placeholder (대기 프로세스) |
| simulator-web | placeholder (정적 안내 페이지) |
| mock-device | placeholder (메시지 출력 후 종료) |
| firmware | 미구현 |
| models | 미학습 |

관련 문서: [interfaces.md](interfaces.md) · [database.md](database.md) · [security.md](security.md) · [test-plan.md](test-plan.md) · [performance-plan.md](performance-plan.md)
