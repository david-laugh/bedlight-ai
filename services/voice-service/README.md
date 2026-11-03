# services/voice-service

> **상태: 데이터셋 수집 기능 동작** (단위 테스트 + 컨테이너 smoke test 로 검증).
> KWS 추론은 미구현 — `/api/v1/recognize` 는 503 을 반환한다 ([voice-inference-plan](../../docs/voice-inference-plan.md)).

## 실행

```bash
docker compose up -d --build voice-service
curl http://127.0.0.1:8001/health
docker compose logs -f voice-service
```

녹음 파일은 bind mount 로 PC 의 `datasets/voice/` 에 저장되어 컨테이너를 재시작/삭제해도 유지된다.

## API

| Method | Path | 설명 |
|---|---|---|
| GET | `/health` | `{"status":"ok","data_dir_writable":true,"model_loaded":false}` (쓰기 불가 시 503) |
| POST | `/api/v1/recordings` | WAV 업로드 (multipart/form-data) → 검증 → 저장 |
| GET | `/api/v1/recordings/stats` | 클래스별 파일 수·총 길이 (디스크 기준) |
| GET | `/api/v1/model` | 모델 배포 상태 |
| POST | `/api/v1/recognize` | 503 `model_not_deployed` (추론 미구현) |
| GET | `/docs` | OpenAPI UI |

### POST /api/v1/recordings

| 필드 | 필수 | 설명 |
|---|---|---|
| `audio` | O | WAV 파일 (PCM, 16-bit, mono, 16000 Hz, 0.5–10 s, ≤ 1 MB) |
| `label` | O | `off` \| `unknown` \| `noise` |
| `device_id` | O | `^[a-z0-9][a-z0-9-]{2,63}$` (예: `bedlight-01`) |
| `capture_id` | O | `^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$` (ESP32 는 UUID v4) — 멱등 키 |
| `sample_rate` | O | `16000` (WAV 헤더와 일치해야 함) |
| `record_ms`, `free_heap`, `free_psram`, `device_uptime_ms`, `firmware_version` | - | 디바이스 성능 지표 (metadata 에 저장) |
| Header `X-Device-Token` | 조건부 | `VOICE_UPLOAD_TOKEN` 설정 시 필수 |

응답 (스키마: `contracts/v1/voice-recording-response.schema.json`):

| HTTP | 의미 |
|---|---|
| 201 | 저장됨 `status="saved"` |
| 200 | 같은 `capture_id` 재전송 → 기존 파일 반환 `status="duplicate"` |
| 400 | 빈 파일 |
| 401 | 토큰 불일치 |
| 409 | 같은 `capture_id` 가 다른 label/크기로 이미 존재 |
| 413 | 크기 초과 |
| 422 | 라벨/ID 형식 오류, WAV 손상·잘림, 형식 불일치(PCM/16-bit/mono/16 kHz), 길이 범위 밖, 전부 0 인 오디오 |

본문 주요 필드: `capture_id, label, filename, relative_path, duration_ms, sample_rate, file_size, status,
validation_status (valid | valid_with_warnings), warnings (likely_silent | clipping | high_dc_offset), quality, timing`.

`timing`: `receive_ms` (요청 도착 → 업로드 본문 수신·파싱 완료), `validate_ms`, `save_ms`, `total_ms`. `Server-Timing` 헤더에도 포함.

```bash
curl -F audio=@sample.wav -F label=off -F device_id=bedlight-01 \
     -F capture_id=manual-0000001 -F sample_rate=16000 http://127.0.0.1:8001/api/v1/recordings
```

## 보안

- 업로드 파일명은 무시하고 서버가 `label_<UTC시각>_<capture_id 앞 8자>.wav` 로 생성. label 은 allow-list, ID 는 정규식 검증,
  최종 경로가 `raw/<label>/` 안인지 resolve 로 재확인 → 경로 조작 차단
- 같은 이름은 `O_EXCL` 로 생성해 덮어쓰지 않고 `_1`, `_2` 접미사 부여
- 포트 8001 은 ESP32 접속을 위해 LAN 에 노출된다 → `VOICE_UPLOAD_TOKEN` 설정 권장, 인터넷 포트포워딩 금지

## 환경변수

| 변수 | 기본값 |
|---|---|
| `VOICE_DATA_DIR` | `/data/voice` |
| `VOICE_MODEL_DIR` | `/models` |
| `VOICE_UPLOAD_TOKEN` | (빈 값 = 미사용) |
| `VOICE_ALLOWED_LABELS` | `off,unknown,noise` |
| `VOICE_MIN_DURATION_MS` / `VOICE_MAX_DURATION_MS` | 500 / 10000 |
| `VOICE_MAX_UPLOAD_BYTES` | 1000000 |

## 테스트

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest -q tests        # 합성 오디오 fixture 사용 (학습 데이터 아님)
```
