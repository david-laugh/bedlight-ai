# firmware/xiao-esp32s3-sense

| 모드 | 상태 |
|---|---|
| `DATASET_RECORDING` — PDM 마이크 녹음 → WAV → Voice Service 업로드 | 구현, 보드에서 150 건 녹음으로 검증 |
| RUNTIME: FOMO Fall 감지 → 5 s LISTENING → 1 s 오디오 청크 업로드 + MQTT | **구현 (펌웨어 0.3). 실제 보드에서 4 개 세션 검증 (추론 ~180 ms, 오디오 누락 0)** |

상세: [docs/runtime-pipeline.md](../../docs/runtime-pipeline.md)

ESP32 는 Voice 모델을 실행하지 않는다 (아키텍처 v2). 사용 방법: [docs/voice-dataset.md](../../docs/voice-dataset.md).

## 파일

```
toolchain.env               CI 가 읽는 버전 고정값 (Core 2.0.17, FQBN)
bedlight/bedlight.ino       메인 스케치
bedlight/wav_header.h       WAV 헤더 생성 + DC 제거/게인 (Arduino 비의존, 호스트 테스트)
bedlight/config.example.h   설정 템플릿 → config.h 로 복사 (config.h 는 .gitignore)
tests/test_wav_header.cpp   g++ 호스트 단위 테스트 (firmware-ci)
```

## 대상 하드웨어 / 툴체인

| 항목 | 값 |
|---|---|
| 보드 | Seeed Studio XIAO ESP32S3 Sense (8 MB OPI PSRAM, 8 MB Flash) |
| 마이크 | 내장 PDM: CLK GPIO42, DATA GPIO41 |
| Core | ESP32 Arduino Core **2.0.x** (2.0.17 고정). `ESP_I2S.h`(3.x) 사용 안 함 |
| FQBN | `esp32:esp32:XIAO_ESP32S3:PSRAM=opi,CDCOnBoot=default` (= IDE: PSRAM OPI, USB CDC On Boot Enabled) |

## 녹음 구현 요약

| 항목 | 구현 |
|---|---|
| I2S 드라이버 | IDF 4.4 legacy `driver/i2s.h`: `I2S_MODE_MASTER \| RX \| PDM`, 16 kHz, 16-bit, `ONLY_LEFT`, DMA 8×512 |
| 핀 | `ws_io_num = 42` (PDM clock), `data_in_num = 41` |
| 읽기 | `i2s_read()` 1 KB 단위 bulk 읽기로 정확히 `16000 × 2 s × 2 B = 64000 B` 를 PSRAM 버퍼에 수집. 부족하면 실패 처리 |
| 대기 | 평소 `i2s_stop()`. 녹음 시 `i2s_start()` 후 200 ms 버림 (PDM 필터 안정화, 이전 DMA 데이터 제거) |
| 검증 | 수집 바이트 = 기대값, 샘플 수 = 32000, 실제 소요 시간이 2000 ms ±10 % 아니면 경고 |
| 후처리 | DC offset 제거 → 정수 게인(`AUDIO_GAIN`, 기본 4) + int16 포화, clipping 샘플 수 보고 |
| WAV | 44 B 표준 PCM 헤더, little-endian, `fmt` = PCM/1ch/16000/32000 B/s/align 2/16-bit |
| 메모리 | `heap_caps_malloc(MALLOC_CAP_SPIRAM)` + RAII 해제 (모든 반환 경로에서 free). 업로드는 같은 버퍼에서 스트리밍 (2차 복사 없음) |
| 업로드 | `WiFiClient` 로 multipart/form-data 직접 스트리밍, `Content-Length` 계산, 실패 시 재시도(4xx 는 재시도 안 함) |
| 지표 | `[metrics]` 줄: `capture_id, record_ms, wav_bytes, upload_ms, http, free_heap, free_psram` |

Seeed 위키 예제(`xiao_esp32s3_sense_mic`)는 `I2S.h` + `esp_i2s::i2s_read()` 를 쓰고, KWS 예제는 `driver/i2s.h` 를 직접 사용한다.
본 펌웨어는 후자 방식(드라이버 직접 설정)을 택해 오류 코드를 직접 처리한다. 두 방식 모두 단일 샘플 `I2S.read()` 가 아닌 DMA bulk 읽기다.

## 하드웨어에서 확인해야 할 위험 요소

- PDM 마이크 원신호 크기와 DC offset 은 보드마다 다를 수 있다 → `AUDIO_GAIN` 조정 필요 가능
- `ONLY_LEFT` 채널 선택이 실제 마이크 채널과 다르면 무음이 녹음된다 → 서버가 `all_zero_audio` 422 로 거부하고 시리얼에 `[WARN] very low signal` 출력
- 실제 샘플링 속도 오차 → `Recorded 32000 samples in N ms` 의 N 확인
