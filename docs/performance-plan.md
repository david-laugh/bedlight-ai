# BedLight AI — 성능 및 병목현상 평가 계획

> **측정 결과 없음.** 이 문서는 측정 지점·방법·시나리오만 정의한다. 아래 표의 결과 칸은 실제 측정 후에만 채운다.

## 1. 목표

- 명령 1회(“off” 발화 → 시뮬레이터 조명 OFF 화면 반영)의 구간별 지연을 분해하여 병목 구간을 식별한다.
- 부하(1/5/20 evt/s)와 장애(MQTT/DB 지연, 시뮬레이터 해제)에서 지연·실패율·유실률 변화를 관찰한다.
- 컨테이너별 CPU·메모리 사용량을 수집한다.

## 2. 측정 지점

```
[ESP32]  capture ─▶ dsp ─▶ inference ─▶ publish ──MQTT──▶ [broker] ──▶ [controller] recv ─▶ forward ──WS──▶ [simulator] recv ─▶ render ─▶ applied
   T0        T1       T2        T3          T4                               T5            T6                    T7            T8          T9
                                                                     └──▶ [db-writer] recv ─▶ insert done
                                                                                 D1              D2
```

| # | 측정 항목 | 계산 | 기록 위치 | 측정 주체 |
|---|---|---|---|---|
| M1 | ESP32 카메라 프레임 처리 시간 | T1−T0 (`micros()`) | `timing.capture_ms` | 펌웨어 |
| M2 | Vision 모델 추론 시간 | EI `result.timing.dsp` + `classification` | `timing.dsp_ms`, `timing.inference_ms` | 펌웨어 |
| M3 | 음성 모델 추론 시간 | EI `result.timing` (KWS) | `timing.dsp_ms`, `timing.inference_ms` | 펌웨어 |
| M4 | MQTT 메시지 전달 지연 | T5 − `emitted_at` (SNTP 동기 필요) 또는 왕복 측정 | `timing.controller_received_at` | controller |
| M5 | Controller 처리 시간 | T6−T5 | `controller_forwarded_at − controller_received_at` | controller |
| M6 | WebSocket 이벤트 전달 시간 | T7−T6 | `simulator_received_at − controller_forwarded_at` | simulator |
| M7 | DB INSERT 처리 시간 | D2−D1 (HTTP 요청 시작~응답) | db-writer 로그 `db_insert_ms` | db-writer |
| M8 | 웹 시뮬레이터 화면 반영 시간 | 상태 변경 ~ 2번째 `requestAnimationFrame` | `timing.render_ms` | simulator |
| M9 | 컨테이너별 CPU·메모리 | `docker stats --no-stream` 1 s 주기 | `tests/performance/results/*.csv` | 수집 스크립트 |
| E2E | end-to-end | `simulator_applied_at − emitted_at` | trace 단위 계산 | analyze |

### 시계 동기화 주의

- ESP32 `millis()` 는 서버 시계와 무관하다. 디바이스 내부 구간(M1–M3)은 디바이스 duration 으로만 비교한다.
- 디바이스→서버 구간(M4)은 ESP32 SNTP 동기 후 `emitted_at` 사용. 동기 오차(수~수십 ms)를 결과와 함께 명시한다.
- mock-device 는 서버와 같은 호스트에서 실행되므로 M4 를 더 정확히 측정할 수 있다 (실기기 수치와 구분 보고).
- Controller·Simulator·DB Writer 는 같은 호스트 시계를 사용 (브라우저도 같은 PC 에서 실행 시).

## 3. 추적 방법

| 항목 | 방법 |
|---|---|
| 중복 구분 | `event_id` (DB UNIQUE, 각 서비스 LRU) |
| 흐름 연결 | `trace_id` 로 LYING → … → APPLIED 연결 |
| 유실 탐지 | 발행 수(mock-device 카운터, 디바이스 `sequence`) vs 수신/저장 수 |
| 구간 시간 | 각 컴포넌트가 `timing` 에 자기 필드만 추가 + 구조화 로그(JSON, `trace_id` 포함) |

## 4. 집계 지표

각 측정 항목 × 시나리오마다 다음을 산출한다.

- 샘플 수 (n)
- 평균, P50, **P95**, 최대
- 실패율 = 실패 처리 수 / 시도 수 (스키마 거부, WS 미전달, INSERT 실패)
- 이벤트 유실률 = 1 − (최종 저장 고유 `event_id` 수 / 발행 수)
- 중복률 = 중복 수신 수 / 수신 수

## 5. 시나리오

| ID | 시나리오 | 부하/조건 | 지속 | 주 관찰 지표 |
|---|---|---|---|---|
| P1 | 정상 단일 명령 | 1회 × 30 반복 | — | E2E, M1–M8 분해 |
| P2 | 연속 명령 | 간격 1 s, 50회 | — | M5, M6, M8 |
| P3 | 초당 1개 이벤트 | 1 evt/s | 5 min | 기준선 |
| P4 | 초당 5개 이벤트 | 5 evt/s | 5 min | M5, M7, 큐 길이 |
| P5 | 초당 20개 이벤트 | 20 evt/s | 5 min | 병목 구간, M9, 유실률 |
| P6 | MQTT 연결 지연 | broker pause 5 s / netem 200 ms | — | M4, 재전송, 유실률 |
| P7 | DB 응답 지연 | INSERT 지연 1–3 s 주입 | — | M7, db-writer 큐, controller 경로 무영향 확인 |
| P8 | 동일 event_id 중복 수신 | 각 이벤트 2회 발행 | — | 중복률, DB 행 수 |
| P9 | 시뮬레이터 연결 해제/복구 | 10 s 해제 후 복구 | — | 복구 시간, 해제 중 요청 처리 |

※ 실기기는 Vision 추론 주기 때문에 고부하(P4, P5) 발생이 어려우므로, 부하 시나리오는 mock-device 로 수행하고 실기기는 P1, P2 중심으로 측정한다.

## 6. 결과 기록 양식 (측정 전 — 비어 있음)

| 시나리오 | 항목 | n | 평균 | P95 | 실패율 | 유실률 | 비고 |
|---|---|---|---|---|---|---|---|
| P1 | E2E | | | | | | |
| … | | | | | | | |

환경 기록: 호스트 CPU/RAM, OS, Docker 버전, WiFi 환경, 펌웨어/모델 버전, 커밋 SHA.

## 7. 병목 판단 기준 (계획)

- 구간별 P95 비율이 E2E P95 의 50% 이상이면 1차 병목 후보.
- 부하 증가(P3→P5) 시 지연 증가 기울기가 가장 큰 구간.
- 큐 길이가 지속 증가하는 서비스 = 처리율 < 유입률.
- CPU 사용률 지속 80% 이상 또는 메모리 단조 증가 컨테이너.
