# BedLight AI — 아키텍처 (v2: 중앙 음성 인식)

> 변경 이력: v1 은 ESP32 에서 Vision + Voice(KWS) 를 모두 실행했다. v2 에서 **Voice 추론을 중앙 Voice Service 로 이전**했다.
> ESP32 는 Vision 추론(예정)과 음성 **녹음·전송**만 담당한다.

## 1. 역할 분담

| 영역 | 구성요소 | 역할 | 상태 |
|---|---|---|---|
| Edge | XIAO ESP32S3 Sense (실제 하드웨어, 컨테이너 아님) | 카메라로 누운 자세 인식 (Edge Impulse Vision, 온디바이스) | 미구현 |
| | | 내장 PDM 마이크 녹음 (16 kHz / mono / 16-bit) → WAV → HTTP 업로드 | **구현 (하드웨어 미검증)** |
| 중앙 서버 | `voice-service` (FastAPI, :8001) | WAV 수신·검증·저장 (데이터셋 수집) | **구현·컨테이너 검증** |
| | | KWS 추론 (Edge Impulse Linux 배포) → 결과를 Controller 로 전달 | 인터페이스만 (503) |
| IoT 제어 | `controller` | 음성 결과 검증, LIGHT_OFF 명령 생성, MQTT 로 가상 전등 제어 | placeholder |
| | `mqtt-broker` | 이벤트/명령 중계 | 동작 |
| | `simulator-web` | 가상 침실·조명 ON/OFF | placeholder |
| | `db-writer` → Supabase | 주요 이벤트·처리 시간 저장 | placeholder |
| 테스트 | `mock-device` (profile `test`) | ESP32 없이 이벤트 생성 | placeholder |

## 2. 구성도

```
 XIAO ESP32S3 Sense (LAN)                       PC: Docker Compose (bedlight-net)
┌─────────────────────────┐   HTTP POST       ┌──────────────────┐  bind mount   ./datasets/voice/
│ PDM mic → PSRAM → WAV   │──/api/v1/──────▶ │ voice-service    │──────────────▶ raw/{off,unknown,noise}/*.wav
│ (DATASET_RECORDING)     │  recordings :8001 │ FastAPI :8001    │               metadata.jsonl
│ Camera → Vision (예정)  │                   │ KWS model (예정) │◀── ./models/voice/deploy/*.eim (예정)
└───────────┬─────────────┘                   └────────┬─────────┘
            │ MQTT (예정) LYING_DETECTED                │ MQTT (예정) VOICE_KEYWORD_DETECTED
            ▼                                          ▼
      ┌──────────────┐  devices/+/events, voice/+/events   ┌────────────┐
      │ mqtt-broker  │────────────────────────────────────▶│ controller │
      │ :1883        │◀──── lights/{room}/commands ────────│            │
      └──────┬───────┘                                     └────────────┘
             │ (MQTT over WebSocket, 예정)          │ all events
             ▼                                      ▼
      ┌──────────────┐                        ┌───────────┐  HTTPS   ┌──────────┐
      │simulator-web │                        │ db-writer │────────▶ │ Supabase │
      └──────────────┘                        └───────────┘          └──────────┘
```

## 3. 향후 런타임 흐름 (동일 `trace_id`)

| # | 주체 | 동작 | 상태 |
|---|---|---|---|
| 1 | ESP32 | Vision 모델로 누운 자세 판단 → `LYING_DETECTED` (MQTT) | 미구현 |
| 2 | ESP32 | 누운 상태에서 음성 녹음 → `LISTENING_STARTED` | 녹음은 구현 (시리얼 명령 트리거) |
| 3 | ESP32 → voice-service | WAV 업로드 (`capture_id`) | **구현** (현재 `/recordings` 데이터셋 경로) |
| 4 | voice-service | KWS 추론, `off` 인식 → `VOICE_KEYWORD_DETECTED` (`source=voice-service`) | 미구현 |
| 5 | controller | 누움 ∧ 키워드 검증 → `LIGHT_OFF_REQUESTED` (`source=controller`) | 미구현 |
| 6 | controller → MQTT | `bedlight/v1/lights/{room_id}/commands` | 미구현 |
| 7 | simulator-web | 조명 OFF 렌더 → `LIGHT_OFF_APPLIED` | 미구현 |
| 8 | db-writer | 모든 이벤트·처리 시간 Supabase 저장 | 미구현 |

## 4. 설계 결정

| 결정 | 이유 |
|---|---|
| KWS 를 중앙 서버로 이전 | ESP32 에서 Vision + KWS 동시 실행 시 메모리·CPU 경합. 서버에서 모델 교체·재학습 배포가 쉬움. 대신 네트워크 지연이 명령 경로에 추가됨 (performance-plan 에서 측정) |
| 데이터 수집과 추론 엔드포인트 분리 | `/api/v1/recordings` 는 학습 데이터 저장 전용, 추론은 `/api/v1/recognize` (예정). 운영 음성이 데이터셋에 섞이지 않도록 |
| 녹음은 PSRAM 버퍼 + 단일 HTTP 업로드 | microSD 불필요. 2 s × 32 KB/s = 64 KB 로 PSRAM 8 MB 에 충분 |
| `driver/i2s.h` (IDF 4.4 legacy) 직접 사용 | Core 2.0.x 호환. `I2S.read()` 단일 샘플 읽기 대신 DMA 버퍼 bulk `i2s_read()` 로 정확한 샘플 수 확보 |
| 데이터셋을 bind mount (`./datasets/voice`) 로 저장 | 컨테이너 재시작에도 유지되고, PC 에서 바로 재생·검증·Edge Impulse 업로드 가능 |
| WAV 검증 코드를 서버·스크립트가 공유 (`app/wav_utils.py`) | 업로드 시 검증과 오프라인 검증 기준이 같아야 함 |
| `capture_id` 를 멱등 키로 사용 | ESP32 재시도 시 중복 파일 생성 방지 |
| 조명 OFF 판단을 Controller 로 이동 | Vision(디바이스)과 Voice(서버) 결과가 서로 다른 곳에서 생성되므로 결합 지점은 Controller |

## 5. 관련 문서

[interfaces.md](interfaces.md) · [voice-dataset.md](voice-dataset.md) · [voice-inference-plan.md](voice-inference-plan.md) ·
[database.md](database.md) · [security.md](security.md) · [test-plan.md](test-plan.md) · [performance-plan.md](performance-plan.md)
