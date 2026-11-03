# Fall 감지 → 5초 LISTENING → 런타임 오디오 파이프라인 (펌웨어 0.3)

> 이 문서의 "검증" 표기는 실제로 확인한 범위만 나타낸다. 2026-11-03 실제 XIAO ESP32S3 Sense 보드에서 4개 LISTENING 세션으로 파이프라인을 확인했다 (§10). Supabase 기록은 마이그레이션 적용 대기 중이다.

## 1. Vision 모델 라이브러리

| 항목 | 값 (라이브러리 `model_metadata.h` 에서 확인) |
|---|---|
| Arduino 라이브러리 | `Fall_detection_system_for_workers_in_industry_inferencing` v1.0.2 |
| 설치 위치 (이 PC) | `D:\Arduino\Sketchbook\libraries\Fall_detection_system_for_workers_in_industry_inferencing` |
| 원본 공개 프로젝트 | Edge Impulse public 158336 → 사용자 클론 **project 1155470** (owner `matda80`), deploy **v2** |
| 모델 | FOMO object detection (`EI_CLASSIFIER_OBJECT_DETECTION 1`, `LAST_LAYER_FOMO`) |
| 입력 | 96×96 **Grayscale**, resize mode `FIT_SHORTEST` (중앙 정사각형 crop 후 resize) |
| 라벨 | `Fall`, `Standing` |
| 양자화 | INT8 입력/출력, TFLite (TFLM) |
| Tensor arena | 289,580 B (런타임 동적 할당 → PSRAM) |
| 모델 내부 검출 임계값 | 0.5 (`EI_CLASSIFIER_OBJECT_DETECTION_THRESHOLD`) — 펌웨어 `VISION_THRESHOLD` 0.75 가 그 위에 적용됨 |

주의: 산업현장 **낙상** 감지 모델이며 침대에 누운 자세용으로 검증되지 않았다. 이 프로젝트는 프로토타입 목적상 `Fall` 을 "누운 상태"로 간주한다.
공개 프로젝트의 Raspberry Pi 성능 수치는 ESP32 성능이 아니다. ESP32 실측 추론 시간은 보드 실행 후 `v` 명령/status 로 확인해야 한다.

라이브러리(약 176 MB)는 Git 에 포함하지 않는다. 다른 PC/CI 에서는:
- Arduino IDE: Edge Impulse Studio(project 1155470) → Deployment → **Arduino library**, **Quantized (int8)**, TFLite → zip → Sketch > Include Library > Add .ZIP Library
- CI: `EI_API_KEY` secret 설정 시 `scripts/fetch_ei_arduino_library.sh` 가 API 로 다운로드 (§9)

## 2. 펌웨어 구조

```
firmware/xiao-esp32s3-sense/bedlight/
  bedlight.ino          FreeRTOS 태스크 구성, 상태 전이 처리, 시리얼 UI, DATASET_RECORDING(o/u/n)
  config.example.h      설정 템플릿 (config.h 로 복사, git ignore)
  config_defaults.h     config.h 에 없는 설정 기본값 (기존 config.h 그대로 컴파일 가능)
  state_manager.h       WATCHING/LISTENING 상태 머신 (순수 C++, 호스트 테스트)
  camera_handler.*      OV2640/OV3660 grayscale QVGA → 96×96 (fit-shortest, bilinear)
  vision_inference.*    Edge Impulse run_classifier → bounding box 집계 (Fall/Standing)
  audio_capture.*       I2S0 PDM 드라이버 1회 설치, start/stop, RX 오버플로 이벤트 카운트, 소유권 mutex
  audio_dsp.h           연속 DC blocker + 게인 (청크 경계 불연속 없음)
  audio_upload.*        multipart WAV 업로드 (PSRAM 에서 스트리밍)
  mqtt_events.*         esp-mqtt(코어 내장) 이벤트/상태 발행, LWT
  wav_header.h          WAV 헤더 / 데이터셋용 PCM 처리
```

`mqtt_client.h` 이름은 ESP-IDF esp-mqtt 의 헤더와 충돌하므로 `mqtt_events.*` 로 명명했다. MQTT 는 외부 라이브러리 없이 Core 2.0.17 내장 esp-mqtt 를 사용한다.

### 태스크

| 태스크 | 코어/우선순위 | 역할 | 금지 |
|---|---|---|---|
| `visionTask` | core 1 / 2 | 캡처 → FOMO → 상태 머신 → 비전 이벤트 | — |
| `audioTask` | core 0 / 6 | LISTENING 동안 I2S 연속 읽기 → DSP → PSRAM 청크 풀 → 전송 큐 | 네트워크 I/O 없음 |
| `netTask` | core 0 / 3 | Wi-Fi 재연결, SNTP, MQTT 시작, 청크 HTTP 업로드, status heartbeat | — |
| `loop()` | core 1 / 1 | 20 ms 마다 `tick()` (유지 시간 만료), 시리얼 명령 | — |

청크 풀: 4 × 32,044 B (PSRAM). 업로드가 4 초 이상 밀리면 다음 청크는 **버려지고 sequence 는 증가** → 서버가 누락으로 감지한다.
I2S DMA: 16 × 512 샘플 (512 ms 버퍼). 드라이버 이벤트 큐의 `I2S_EVENT_RX_Q_OVF` 를 세어 청크마다 `dma_overflows` 로 보고한다.

## 3. 상태 전환 로직

```
WATCHING --Fall(conf ≥ VISION_THRESHOLD)--> LISTENING   (STARTED: session_id 생성, 마이크 시작)
LISTENING --Fall(conf ≥ threshold)--------> LISTENING   (EXTENDED: lastFall = now, I2S 재초기화 없음)
LISTENING --now - lastFall ≥ 5000 ms------> WATCHING    (STOPPED: 마지막 부분 청크 is_final 전송, i2s_stop)
Standing / 미검출 프레임은 LISTENING 을 종료시키지 않는다.
```

- `millis()` 기반 비차단 (delay 없음). 비교는 `(uint32_t)(now - lastFall) >= hold` → 49.7 일 오버플로 안전.
- 이벤트 폭주 방지: `VISION_*_DETECTED` 는 라벨이 바뀔 때만, `LISTENING_EXTENDED` 는 최대 1 회/초.

### 5 초 유지/연장 테스트 결과 (호스트 단위 테스트, `tests/test_state_manager.cpp`)

| 시나리오 | 입력 | 기대 | 결과 |
|---|---|---|---|
| A | 0 s Fall, 2 s Standing | 5.000 s 에 OFF (4.999 s 까지 ON) | **통과** (t0=0, t0=0xFFFFF000 둘 다) |
| B | 0 s Fall, 3 s Fall, 6 s Standing | 8.000 s 에 OFF | **통과** (오버플로 직전 시작 포함) |
| C | Fall conf 0.60 < 0.75 | LISTENING 진입 안 함 | **통과** (0.75 정확히는 진입) |
| D | LISTENING 중 Fall 100 회 | STARTED 1 회, EXTENDED 100 회 → I2S 재초기화 없음 | **통과** |
| wrap | 오버플로 1 초 전 Fall | 정확히 5 s 후 OFF | **통과** |

실제 카메라 영상 기반 동작은 미검증 (§10).

## 4. 오디오 전송 형식 (RUNTIME_AUDIO)

`POST http://<PC LAN IP>:8001/api/v1/audio/chunks`, `multipart/form-data` (Base64 없음)

| part | 값 |
|---|---|
| `audio` | WAV: PCM, 16 kHz, mono, 16-bit LE, 1 s = 32,000 B + 44 B 헤더. 마지막 청크는 짧거나 0 샘플 가능 |
| `device_id`, `session_id` | `bedlight-01`, UUID v4 (LISTENING 세션) |
| `sequence` | 세션 내 0 부터 1 씩 증가 (버린 청크 포함) |
| `sample_start` | 세션 시작 기준 첫 샘플의 절대 인덱스 → 경계 걸친 2 s 구간 재구성, 누락 위치 정확히 복원 |
| `sample_rate`, `channels`, `bits_per_sample`, `duration_ms` | 16000, 1, 16, 실제 샘플 수 기반 |
| `capture_timestamp_ms` | 청크 첫 샘플 시점 `millis()` |
| `capture_epoch_ms` | SNTP 동기 후 Unix ms (미동기 시 빈 값) |
| `is_final` | 세션 마지막 청크 |
| `dma_overflows`, `dropped_chunks_before`, `room_id` | 진단 |

서버 동작: 형식 검증, `(session_id, sequence)` 중복 → 같은 내용이면 200 `duplicate` / 다르면 409, 누락 sequence 감지,
`sample_start` 불연속 감지, 처리 시간 측정. 저장: `runtime-data/audio/sessions/<device>/<session>/chunk_NNNNNN.wav` + `session.json`
(학습 데이터셋 `datasets/voice` 와 분리, git ignore).

| 조회 | 설명 |
|---|---|
| `GET /api/v1/audio/sessions?device_id=` | 세션 요약 (received_chunks, missing_sequences, discontinuities, loss_ratio, complete) |
| `GET /api/v1/audio/sessions/{id}` | 청크별 메타데이터 |
| `GET /api/v1/audio/sessions/{id}/audio.wav` | 전체 재구성 (누락 구간은 0 으로 채우고 `X-Gap-Samples` 헤더로 보고) |
| `GET /api/v1/audio/sessions/{id}/window?start_sample=&duration_ms=2000` | 청크 경계를 넘는 연속 2 s 구간 (누락 겹치면 409) |

음성 분류는 수행하지 않는다 (`inference.performed=false`).

## 5. MQTT

| Topic | QoS | 내용 |
|---|---|---|
| `bedlight/v1/devices/{device_id}/events` | 1 | 런타임 이벤트 (`contracts/v1/device-runtime-event.schema.json`, v1.2) |
| `bedlight/v1/devices/{device_id}/status` | 1, retained | heartbeat (1 s LISTENING 중 / 5 s 평상시) + LWT `offline` |
| `bedlight/v1/simulator/{room_id}/events` | 1 | 시뮬레이터 수동 조명 `LIGHT_STATE_CHANGED` (source=simulator) |

이벤트 공통 필드: `schema_version, event_id, trace_id, source, device_id, event_type, model, confidence, timestamp_ms, inference_ms`
(+ `device_uptime_ms, session_id, data`). 세션 이벤트의 `trace_id` = `session_id`.

| event_type | confidence | inference_ms | data 주요 필드 |
|---|---|---|---|
| `VISION_FALL_DETECTED` / `VISION_STANDING_DETECTED` | 박스 최고 conf | FOMO 전체 ms | label, fall/standing_confidence, counts, box, threshold, capture/dsp/classification ms |
| `LISTENING_STARTED` / `LISTENING_EXTENDED` | 트리거 Fall conf | FOMO ms | hold_ms, remaining_ms, listen_until_uptime_ms, extensions |
| `AUDIO_CHUNK_SENT` | null | 업로드 ms | ok, http_status, upload_ms, sequence, sample_start, samples, is_final, dma_overflows |
| `LISTENING_STOPPED` | null | null | duration_ms, extensions, chunks_queued/sent/failed/dropped, dma_overflows |
| `SYSTEM_ERROR` | null | null | component, code, message, recoverable (5 s rate limit) |

예시: `contracts/v1/examples/valid/runtime-*.json`

## 6. Docker 서비스 변경

| 서비스 | 변경 |
|---|---|
| `mqtt-broker` | host 바인딩을 `MQTT_BIND_ADDR` 로 설정 가능 (기본 127.0.0.1). **ESP32 접속 시 `.env` 에 `MQTT_BIND_ADDR=0.0.0.0`** + Windows 방화벽 TCP 1883 개인 네트워크 허용 |
| `voice-service` | `/api/v1/audio/chunks` + 세션 조회/재구성 API, `runtime-data/audio` 볼륨 |
| `controller` | **구현**: MQTT 상태 집계, voice-service 세션 폴링, `ws://127.0.0.1:8000/ws`, `POST /api/v1/light` (수동). 자동 소등 없음 |
| `db-writer` | **구현**: MQTT → JSON Schema 검증 → event_id 중복 제거 → Supabase REST (배치, 재시도). 자격증명 없으면 **DRY-RUN** (`runtime-data/db-writer/events.jsonl`) |
| `simulator-web` | 정적 대시보드 (`http://127.0.0.1:8080`): 연결, Fall/Standing, conf, LISTENING 남은 시간, 서버 수신 상태, 조명, 이벤트 로그(source 색 구분) |
| `mock-device` | **구현**: 시나리오 A/B 재현 (source=mock, 합성 오디오, 스크립트된 비전 결과) |

## 7. Supabase 로그

마이그레이션 `20261103020000_runtime_vision_audio_events.sql`: 이벤트 타입 확장 + `session_id, device_timestamp_ms, network_ms, ok` 컬럼.
행 = 이벤트 1 건 (원본 오디오 없음, 메타데이터만). 매핑: `confidence`, `inference_ms`(비전), `network_ms`(청크 업로드), `ok`, 전체 `payload` jsonb.
상태: `.env` 에 Supabase 자격증명 등록 완료, db-writer `Supabase sink enabled` 확인. 그러나 `public.device_events` 테이블이 아직 없어
REST 가 404 (`PGRST205`) 를 반환한다 → **실제 Supabase 기록은 미검증**. 적용 절차:
1. Supabase SQL Editor 에서 `supabase/migrations/*.sql` 3 개를 파일명 순서대로 실행
2. `docker compose up -d db-writer` (로그 `Supabase sink enabled`)
3. 그동안 쌓인 행 재전송: `docker compose exec db-writer python -m app.replay /data/db-writer/events.jsonl /data/db-writer/failed.jsonl`
   (`event_id` UNIQUE + `ON CONFLICT DO NOTHING` 이라 여러 번 실행해도 중복되지 않음)

삽입 실패(테이블 없음 등 4xx, 재시도 소진) 행은 버리지 않고 dead-letter `runtime-data/db-writer/failed.jsonl` 에 보관된다.

## 8. Arduino IDE 빌드·업로드

1. Boards Manager: **esp32 2.0.17** (3.x 금지)
2. Library: `Fall_detection_system_for_workers_in_industry_inferencing` 설치 확인 (이 PC 는 설치됨)
3. `bedlight/config.h` 에 기존 값 유지 + 필요 시 추가 (없으면 `config_defaults.h` 기본값): `VISION_THRESHOLD`, `LISTEN_HOLD_MS`, `MQTT_HOST`
4. Tools: Board **XIAO_ESP32S3**, PSRAM **OPI PSRAM**, USB CDC On Boot **Enabled**, Partition **Default with spiffs (3MB APP)**
5. 첫 컴파일은 Edge Impulse SDK 때문에 수 분 걸린다. 업로드 후 Serial 115200

시리얼 명령: `s` 상태, `v` 비전 통계(fps, 추론 ms), `p` 프레임별 출력 토글, `r` 런타임 일시정지, `o/u/n` 데이터셋 녹음(런타임 일시정지 후 수행), `h` 도움말.

## 9. CI

| Workflow / job | 내용 |
|---|---|
| firmware-ci `host-tests` | `wav_header.h`, **`state_manager.h` 시나리오 A–D** (g++) |
| firmware-ci `compile` | Core 2.0.17 컴파일, `BEDLIGHT_VISION_ENABLED=0` (**비전 통합 검증 아님** — 명칭에 명시) |
| firmware-ci `ei-library` / `compile-vision` | secret `EI_API_KEY` 가 있으면 API 로 라이브러리 다운로드 → 메타데이터 확인 → 비전 포함 컴파일. 없으면 **skipped** + 요약 |
| services-ci `python-unit` | voice-service(청크 순서/중복/누락/불연속/2 s 윈도우), controller, db-writer, scripts |
| services-ci `integration` | compose 기동 → mock 시나리오 B → 세션 완전성, controller 상태, db-writer 기록 |
| scaffold-ci | compose 검증, 이미지 빌드, broker/voice/controller/db-writer smoke |

## 10. 검증 현황 (2026-11-03)

실기기 근거: XIAO ESP32S3 Sense, 펌웨어 `0.3.0-runtime`, `source=device`, LISTENING 세션 4 개 (서버 `runtime-data`, db-writer DRY-RUN 34 행).

| # | 완료 기준 | 상태 | 근거 |
|---|---|---|---|
| 1 | 실제 ESP32 에 FOMO 탑재 | **검증** | 보드에서 실행, status `firmware_version=0.3.0-runtime`, 모델 project 1155470 v2 |
| 2 | 실제 카메라로 Fall/Standing 추론 | **검증** | `VISION_FALL_DETECTED` conf 0.87–0.91, `VISION_STANDING_DETECTED` 수신. 추론 178–186 ms, 약 5.5 FPS. 정확도(오탐/미탐)는 정량 평가하지 않음 |
| 3 | Fall 감지 시 LISTENING 시작 | **검증** | `LISTENING_STARTED` 4 회 (트리거 conf 0.85–0.91 ≥ 0.75) |
| 4 | 마지막 Fall 이후 5 초 유지 | **검증** | 연장 없는 세션 길이 5,063 / 5,075 / 5,077 ms (마지막 Fall + 5 s, 오차는 프레임 간격 ~180 ms 이내) |
| 5 | 재감지 시 5 초 연장 | **검증 (1 회)** | 연장 1 회 세션 5,250 ms. 반복 연장(시나리오 B 형태)은 호스트 테스트로만 검증 |
| 6 | 오디오 수집 중 Vision 추론 지속 | **검증 (간접)** | LISTENING 중 `LISTENING_EXTENDED` 발생 = 녹음 중 추론 결과가 상태 머신에 반영됨. 녹음 중 FPS 별도 측정은 안 함 |
| 7 | 오디오 데이터 누락 측정 | **검증** | 4 세션 모두 `complete=true`, missing 0, discontinuity 0, `dma_overflows` 0, dropped 0 |
| 8 | 중앙 서버에 실제 오디오 업로드 | **검증** | 청크 21 개 전부 HTTP 201, 업로드 평균 441 ms / 최대 2,825 ms, 세션 RMS 약 -29~-30 dBFS (무음 아님) |
| 9 | MQTT 상태 이벤트 | **검증** | 34 개 이벤트 전부 v1.2 스키마 통과, controller 가 device online / 0.3.0-runtime 인식 |
| 10 | Supabase 이벤트 기록 | **미검증** | 자격증명 등록·sink 활성 확인. 테이블 미생성(404) → 마이그레이션 적용 후 replay 필요 (§7) |
| 11 | 웹 시뮬레이터 상태 업데이트 | **부분** | controller 상태(WS 소스)가 실기기 값으로 갱신됨 확인. 브라우저 화면은 사용자 육안 확인 |
| 12 | GitHub Actions CI | PR 에서 확인 | 이전 `firmware-ci` 실패 원인(toolchain.env 정규식이 숫자 포함 변수명을 누락) 수정 |

### 관찰된 한계

- Fall 직후 약 100 ms 는 녹음되지 않는다 (PDM 안정화 warm-up). 세션 5,063 ms 대비 오디오 4,960 ms.
- `LISTENING_STOPPED.chunks_sent` 는 종료 시점 스냅샷이다 (업로드 진행 중 청크 미포함). 최종 수신 여부는 서버 세션 기준.
- 업로드 최대 2.8 s 관찰. 청크 풀(4 s) 을 넘는 지연이 지속되면 청크가 버려지고 서버에 누락으로 보고된다.
- 최소 free heap 153,920 B (짧은 실행). 장시간 실행 누수 여부는 미측정.
- 모델은 산업현장 낙상용이며 침대 누움 자세에 대한 정확도는 평가하지 않았다.
