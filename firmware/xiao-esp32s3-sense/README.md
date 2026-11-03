# firmware/xiao-esp32s3-sense

> **상태: 미구현.** 펌웨어 소스(`.ino`)가 없다. `firmware-ci.yml` 은 스케치가 없으면 compile job 을 **skipped** 로 남기며 성공으로 위장하지 않는다.

## 대상 하드웨어 / 툴체인

| 항목 | 값 |
|---|---|
| 보드 | Seeed Studio XIAO ESP32S3 Sense (ESP32-S3, 8MB OPI PSRAM, OV2640/OV3660 카메라, PDM 마이크) |
| Core | ESP32 Arduino Core **2.0.x** (`toolchain.env` 의 `ESP32_CORE_VERSION=2.0.17`) |
| FQBN | `esp32:esp32:XIAO_ESP32S3:PSRAM=opi` (첫 빌드 시 `arduino-cli board details` 로 옵션명 확인) |
| AI 런타임 | Edge Impulse Arduino 라이브러리 (Vision 1 + KWS 1) |

## 예정 구조

```
firmware/xiao-esp32s3-sense/
  toolchain.env                 # CI 가 읽는 버전 고정값
  bedlight/bedlight.ino         # 스케치 (SKETCH_DIR) — 이 파일이 생기면 CI compile 활성화
  bedlight/config.h             # 핀/임계값/topic (비밀값 금지)
  bedlight/secrets.h.example    # WiFi/MQTT 계정 템플릿 (secrets.h 는 .gitignore)
  libraries/*.zip               # Edge Impulse export (Vision, KWS)
```

## 동작 설계 (상태 머신)

```
IDLE --(vision: lying ≥ threshold, N 연속 프레임)--> LYING   : LYING_DETECTED 발행
LYING --> LISTENING (window_ms 동안 KWS 실행)                  : LISTENING_STARTED
LISTENING --(KWS "off" ≥ threshold)--> LIGHT_OFF_REQUESTED 발행 (cause_event_ids = [lying, keyword])
LYING/LISTENING --(lying 해제)--> IDLE                         : LYING_LOST
any --(오류)--> SYSTEM_ERROR
```

- 하나의 명령 흐름(LYING_DETECTED → … → LIGHT_OFF_REQUESTED)은 같은 `trace_id` 를 공유한다.
- 카메라/마이크 원본 데이터는 전송하지 않는다. 추론 결과만 MQTT 로 발행.
- `timing.capture_ms`, `dsp_ms`, `inference_ms` 는 `ei_impulse_result_t.timing` 과 `micros()` 로 측정.
- 부팅 시 `status` topic 에 retained `online`, LWT 로 `offline` 등록.

## 의존성 관리 계획

1. Edge Impulse Studio 에서 Arduino library 로 export (버전 = Studio deployment 번호).
2. zip 을 `libraries/` 에 두고 `models/*/MODEL_CARD.md` 에 버전·정확도·입력 크기 기록.
   용량이 크면 Git LFS 사용 또는 CI 에서 Edge Impulse API 로 다운로드(API key 는 GitHub Secret).
3. Vision 과 KWS 두 라이브러리를 동시에 링크할 때 심볼 충돌 가능 → 첫 통합 빌드에서 확인 필요.
