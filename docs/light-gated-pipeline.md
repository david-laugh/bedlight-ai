# 조명 게이트 + 음성(박수) 소등 파이프라인 (펌웨어 0.4)

> 상태: 모델 없이 할 수 있는 부분은 구현 및 검증 완료 (단위 테스트, 펌웨어 컴파일, mock 장치 통합 테스트).
> **음성 모델(.eim)이 아직 없어서 실제 소리로 소등되는 경로는 검증되지 않았다.** 펌웨어 0.4 의 보드 실행도 미검증이다.

## 1. 동작

```
[시뮬레이터 벽 스위치 = 버튼 센서]
      │ POST /api/v1/light
      ▼
 controller ──retained MQTT bedlight/v1/lights/{room}/state──▶ ESP32 (구독)
      ▲                                                        light OFF → IDLE: 비전·녹음 중단
      │                                                        light ON  → WATCHING: Fall 감시
      │                                                           Fall → LISTENING → 1 s 오디오 청크
      │                                                                         │ HTTP
      │ MQTT bedlight/v1/voice/{id}/events                                      ▼
      └──── VOICE_KEYWORD_DETECTED ◀──── voice-service (슬라이딩 윈도우 KWS, .eim 있을 때만)
 controller 판단: 조명 ON ∧ 해당 누움 세션(진행 중 또는 종료 후 15 s 이내) ∧ source=voice-service
      → 조명 OFF (retained state + LIGHT_STATE_CHANGED) → ESP32 즉시 IDLE (LISTENING_STOPPED reason=light_off)
```

불이 꺼지면 감지와 녹음이 멈추므로, 아무도 듣지 않을 때 오디오와 DB 행이 쌓이지 않는다. 다시 켜면(벽 스위치 ON) Fall 감시를 재개한다.

**`off` 클래스의 실제 소리는 "박수 두 번"이다.** 라벨 이름은 데이터셋과 맞추기 위해 `off` 그대로 쓴다.

## 2. 구성요소별 변경

| 구성 | 변경 | 검증 |
|---|---|---|
| 계약 | `light-state.schema.json` (retained), 런타임 이벤트에 `VOICE_KEYWORD_DETECTED` (source voice-service/mock), `LISTENING_STOPPED.data.reason` | 계약 검사 통과 |
| 펌웨어 0.4 | 조명 상태 구독(esp-mqtt), `light OFF` → IDLE + 진행 중 세션 즉시 종료 (`forceStop`, reason `light_off`), status 에 `gate`, `light_on` | 상태 머신 호스트 테스트, Core 2.0.17 컴파일 (비전 포함 1.10 MB). **보드 미검증** |
| voice-service | `.eim` 로더 (Edge Impulse Linux 소켓 프로토콜 직접 구현), 세션 오디오 슬라이딩 윈도우 (1 s 창, 250 ms hop), 누락 구간 창은 판정 안 함, edge trigger (소리 1 덩어리당 감지 1 회), MQTT 발행, `/api/v1/recognize` | fake 모델 프로세스로 프로토콜·검출 테스트 (Linux) |
| controller | 조명 상태 소유·retained 발행·재시작 시 복원, 판단 로직 (`fusion.py`), 결정 기록, 모델 로드 여부에 따라 `auto_control` | 단위 테스트 + 통합 테스트 |
| db-writer | `bedlight/v1/voice/+/events` 구독 | 단위 테스트 |
| simulator-web | 벽 스위치(버튼 센서), ESP32 게이트, 음성 모델 상태·감지·판단 표시 | 화면은 육안 확인 필요 |
| mock-device | 조명 구독 (OFF → 즉시 중단), `--voice-keyword-at` 스크립트 감지 (source=mock) | 통합 테스트 |

모델이 없을 때: voice-service 는 분류·발행을 하지 않고, controller 는 `auto_control=false` 로 표시한다. 더미 감지 결과는 만들지 않는다.

## 3. 모델 배포 (학습 후)

1. Edge Impulse Studio → Deployment → **Linux (x86_64)** (`.eim`) 빌드·다운로드
   - 이 PC 의 Docker 는 x86_64 Linux 컨테이너이므로 x86_64 타깃. Arduino 라이브러리 export 는 서버에서 쓸 수 없다.
2. 파일을 `models/voice/deploy/` 에 복사 (예: `bedlight-kws-linux-x86_64-v1.eim`). git 에는 올라가지 않는다.
3. `docker compose restart voice-service`
4. 확인: `curl http://127.0.0.1:8001/api/v1/model` → `"loaded": true`, labels 에 `off`, sample_rate 16000
   - `failed to load` 이면 사유가 `reason` 에 표시됨 (주파수 불일치, 라벨 없음, 실행 실패 등)
5. 단일 파일 테스트: `curl -F audio=@datasets/voice/raw/off/<file>.wav http://127.0.0.1:8001/api/v1/recognize`
6. 시뮬레이터에 "자동 소등 활성" 이 표시되는지 확인

임계값: `VOICE_KEYWORD_THRESHOLD` (기본 0.8), hop: `VOICE_HOP_MS` (기본 250). `.env` 로 변경 후 voice-service 재시작.

`.eim` 실행 연동(소켓 프로토콜)은 실제 Edge Impulse 파일로는 아직 확인하지 못했다. 로드 실패 시 `reason` 과 `docker compose logs voice-service` 를 확인한다.

## 4. 판단 규칙 (controller `fusion.py`)

| 조건 | 결과 |
|---|---|
| source 가 voice-service/mock 이 아님 | 무시 |
| 조명이 이미 OFF | 무시 ("light already off") |
| 이벤트 session_id 가 장치의 진행 중 LISTENING 세션 | **소등** |
| 직전 세션이고 종료 후 15 s 이내 (추론이 업로드 뒤에 실행되므로) | **소등** |
| 그 외 | 무시 ("no matching lying (Fall) session") |

모든 판단은 `GET /api/v1/state` 의 `decisions` 와 시뮬레이터에 사유와 함께 표시된다. `keyword_to_decision_ms` 로 감지→판단 지연을 기록한다.

## 5. 보드에서 확인할 것 (펌웨어 0.4 업로드 후)

1. 부팅 로그 `firmware 0.4.0-lightgate`, `MQTT connected, subscribed bedlight/v1/lights/bedroom-01/state`, `[LIGHT] virtual light ON`
2. 시뮬레이터에서 벽 스위치 OFF → 시리얼 `[STATE] light OFF -> IDLE`, 시뮬레이터 게이트 `IDLE`
3. LISTENING 중 스위치 OFF → `LISTENING_STOPPED reason=light_off`, 서버 세션 `final` 수신
4. 스위치 ON → `[STATE] light ON -> WATCHING`
5. (모델 배포 후) 누운 상태에서 박수 두 번 → 시뮬레이터 조명 OFF, 장치 IDLE
