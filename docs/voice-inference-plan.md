# 중앙 Voice AI 추론 계획 (미구현)

> 현재 voice-service 에는 모델이 없다. `POST /api/v1/recognize` 는 **503 `model_not_deployed`** 를 반환하며,
> 더미 인식 결과를 만들지 않는다. `GET /api/v1/model` 로 배포 상태를 확인한다.

## 1. 학습과 배포의 분리

| 단계 | 위치 | 산출물 |
|---|---|---|
| 데이터 수집 | ESP32 → voice-service `/api/v1/recordings` | `datasets/voice/raw/**` |
| 정리 | `scripts/prepare_voice_dataset.py` | `datasets/voice/prepared/{training,testing}` |
| 학습 | Edge Impulse Studio (클래스 `off`, `unknown`, `noise`) | Impulse (MFCC/MFE + Classifier) |
| 배포 | Edge Impulse Deployment → **Linux (x86_64)** (또는 Docker 호스트 아키텍처에 맞는 Linux 타깃) | `.eim` 실행 파일 |
| 실행 | voice-service 컨테이너 (`/models`, read-only mount of `models/voice/deploy/`) | 추론 결과 |

**ESP32 용 Arduino library export 는 서버에서 사용할 수 없다.** 서버는 x86_64 Linux 컨테이너이므로 Linux 배포 타깃을 사용한다.

## 2. 런타임 후보와 결정

| 후보 | 방식 | 장점 | 단점 |
|---|---|---|---|
| **A. `.eim` + `edge_impulse_linux` Python SDK (1안)** | `ImpulseRunner(model.eim)` 를 FastAPI 프로세스에서 실행, int16 PCM → features → classify | Edge Impulse 공식 Linux 경로, Python 통합 간단, 모델 교체 = 파일 교체 | `.eim` 은 아키텍처별 바이너리 (amd64/arm64 맞춰야 함), 별도 프로세스 IPC |
| B. C++ library export + 자체 빌드 | `run_classifier()` 를 C++ 로 빌드해 pybind/CLI 로 호출 | 의존성 최소, 지연 최소 | 빌드 파이프라인 필요, 모델마다 재컴파일 |
| C. TensorFlow Lite export | tflite-runtime 으로 직접 실행 | 범용 | Edge Impulse DSP(MFCC) 를 서버에서 동일하게 재구현해야 함 → 학습/추론 불일치 위험 |

결정: **A 를 1안**으로 하되, 실제 `.eim` 으로 컨테이너 안에서 동작을 확인한 뒤 확정한다 (아직 미검증).
B 는 A 의 지연이 목표를 넘을 때의 대안.

## 3. 준비된 인터페이스

- `services/voice-service/app/inference.py`
  - `KeywordSpotter` Protocol: `classify(pcm16_mono: bytes, sample_rate: int) -> InferenceResult`
  - `InferenceResult`: `label, confidence, scores, inference_ms, model_name, model_version`
  - `load_spotter(model_dir)` → 현재 항상 `None`
  - `model_status(model_dir)` → `.eim` 존재 여부와 미로드 사유
- 모델 위치: `models/voice/deploy/<name>.eim` (git ignore) → 컨테이너 `/models` (read-only)
- 엔드포인트 (예정): `POST /api/v1/recognize` (multipart WAV, 같은 검증 규칙) →
  `{capture_id, label, confidence, scores, inference_ms, model:{name,version}}`
- 결과 전달 (예정): `off` 이고 `confidence ≥ threshold` 이면 MQTT `bedlight/v1/voice/{device_id}/events` 에
  `VOICE_KEYWORD_DETECTED` (`source=voice-service`, `data.capture_id`, `timing.inference_ms`) 발행 → controller 가 소비

## 4. 구현 시 체크리스트

- [ ] Linux x86_64 `.eim` 배포 파일로 컨테이너 내 실행 확인 (실행 권한, glibc 호환)
- [ ] 학습 때와 같은 1 s window. 2 s 녹음은 슬라이딩 윈도우(예: 250 ms hop) 후 최대 `off` 점수 사용
- [ ] Edge Impulse Testing 세트 정확도와 서버 추론 결과 일치 확인 (같은 WAV 로 비교)
- [ ] `inference_ms` 측정·기록 (performance-plan V5)
- [ ] 임계값은 Testing 세트 FAR/FRR 로 결정하고 `models/voice/MODEL_CARD.md` 에 기록
- [ ] 모델 없음/로드 실패 시 503 유지 (더미 결과 금지)
