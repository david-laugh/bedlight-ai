# BedLight AI

XIAO ESP32S3 Sense 의 카메라로 사람이 침대에 누운 상태를 감지하고, 음성 명령 **"off"** 를 인식하면
가상 침실 시뮬레이터의 조명을 끄는 Edge AI 기반 스마트 침실 시스템.

아키텍처 v2:
- **ESP32**: Vision 모델(누운 자세)을 온디바이스 실행(예정), 내장 PDM 마이크로 **녹음해 중앙 Voice Service 로 전송**. ESP32 는 음성 모델을 실행하지 않는다.
- **voice-service (중앙 서버)**: WAV 수신·검증·저장, 향후 Edge Impulse KWS 모델로 "off" 인식.
- **IoT 제어**: controller 가 결과를 검증해 MQTT 로 가상 전등 제어, simulator-web 에서 ON/OFF, Supabase 에 이벤트 저장.

> ## 현재 단계: Fall 감지 → 5초 LISTENING → 런타임 오디오 파이프라인 (펌웨어 0.3)
> ESP32 Edge Impulse FOMO(Fall/Standing) + 조건부 오디오 청크 전송, controller/db-writer/simulator 구현.
> 실제 보드에서 Fall 감지 → 5초 LISTENING(연장) → 오디오 청크 업로드(누락 0) → Supabase 기록까지 확인. 상세: [docs/runtime-pipeline.md](docs/runtime-pipeline.md)
>
> ### 이전 단계: 음성 데이터셋 수집
> **구현됨**: ESP32 녹음·WAV·업로드 펌웨어 (Core 2.0.17 컴파일 확인), voice-service 업로드 API (테스트·컨테이너 검증),
> WAV 검증·Edge Impulse 전처리 스크립트, CI.
> **검증**: 실제 보드에서 클래스별 50 개 (총 150 개) 녹음·업로드 완료.
> **현재도 미구현**: 서버 KWS 추론(음성 분류), 음성 기반 자동 소등.

## 구성

```
ESP32 ──HTTP WAV──▶ voice-service :8001 ──▶ ./datasets/voice/raw/<label>/*.wav
ESP32 ──MQTT(예정)─▶ mqtt-broker ──▶ controller ──MQTT(예정)──▶ simulator-web
                     mqtt-broker ──▶ db-writer ──HTTPS──▶ Supabase PostgreSQL
mock-device ──▶ mqtt-broker
```

| 서비스 | 기술 | 상태 |
|---|---|---|
| `voice-service` | Python 3.11 / FastAPI, `0.0.0.0:8001` (LAN) | **동작** (데이터 수집). 추론 미구현 (503) |
| `mqtt-broker` | Eclipse Mosquitto 2.0 | 동작 (개발용 무인증, `127.0.0.1:1883`) |
| `controller` | Python 3.11 / FastAPI, `127.0.0.1:8000` (WS `/ws`) | 동작: 상태 집계·수동 조명. 자동 소등 미구현 |
| `db-writer` | Python 3.11, paho-mqtt → Supabase REST | 동작 (Supabase 자격증명 없으면 DRY-RUN) |
| `simulator-web` | 정적 HTML/JS 대시보드 (React 전환 예정) | 동작 (`127.0.0.1:8080`) |
| `mock-device` | Python 3.11, compose profile `test` | 동작: 시나리오 A/B (source=mock, 합성 오디오) |

## 음성 녹음 빠른 시작

1. PC: `docker compose up -d --build voice-service` → `curl http://127.0.0.1:8001/health`
2. `firmware/xiao-esp32s3-sense/bedlight/config.example.h` → `config.h` 복사, Wi-Fi 와 **PC LAN IP** 입력
3. Arduino IDE (esp32 core **2.0.17**, Board XIAO_ESP32S3, PSRAM **OPI**, USB CDC On Boot **Enabled**) 로 `bedlight.ino` 업로드
4. Serial Monitor 115200 → `o` 입력 → `>>> SPEAK NOW <<<` 후 "off"
5. PC 의 `datasets/voice/raw/off/` 에 WAV 생성 → **직접 재생해서 확인** → `python scripts/check_wav.py datasets/voice/raw`
6. `python scripts/prepare_voice_dataset.py` → `prepared/review.html` 에서 청취 → Edge Impulse 업로드

전체 절차: **[docs/voice-dataset.md](docs/voice-dataset.md)**

## 전체 스택

```bash
cp .env.example .env            # 선택
docker compose up -d --build
docker compose ps
docker compose --profile test up --build mock-device   # mock-device placeholder 확인
docker compose --profile test down
```

검증 스크립트:

```bash
bash scripts/check_structure.sh
pip install -r scripts/requirements-ci.txt && python scripts/validate_contracts.py
pip install -r services/voice-service/requirements.txt -r services/voice-service/requirements-dev.txt
pytest -q services/voice-service/tests && pytest -q scripts/tests
```

## 디렉터리

| 경로 | 내용 |
|---|---|
| `firmware/xiao-esp32s3-sense/bedlight/` | DATASET_RECORDING 펌웨어 (`bedlight.ino`, `wav_header.h`, `config.example.h`) |
| `services/voice-service/` | FastAPI 음성 업로드/검증/저장 서비스 + 테스트 |
| `datasets/voice/` | 녹음 저장소 (bind mount, WAV 는 git 제외) |
| `models/vision/`, `models/voice/` | 모델 카드/배포 위치 (`models/voice/deploy/*.eim` 예정) |
| `services/controller/`, `services/db-writer/`, `services/simulator-web/` | placeholder |
| `infra/mosquitto/` | Mosquitto 개발 설정 + 인증/ACL 템플릿 |
| `contracts/v1/` | 이벤트·응답 JSON Schema, topic/HTTP 매핑, valid/invalid 예시 |
| `supabase/migrations/` | `device_events` + RLS, voice-service source 추가 (미적용) |
| `scripts/` | `check_wav.py`, `prepare_voice_dataset.py`, 구조·계약 검사 |
| `tests/` | mock-device, integration, performance (placeholder/계획) |
| `docs/` | 설계 문서 |

## 문서

- [아키텍처 v2](docs/architecture.md)
- [조명 게이트 + 박수(off) 소등 파이프라인 (펌웨어 0.4)](docs/light-gated-pipeline.md)
- [음성 데이터셋 수집 가이드](docs/voice-dataset.md)
- [중앙 음성 추론 계획](docs/voice-inference-plan.md)
- [인터페이스 계약](docs/interfaces.md) · [DB 설계](docs/database.md) · [보안](docs/security.md)
- [테스트 계획](docs/test-plan.md) · [성능·병목 평가 계획](docs/performance-plan.md)

## CI

| Workflow | 내용 |
|---|---|
| `scaffold-ci.yml` | 구조 검사, 스키마 유효성, compose config/build, smoke (MQTT 왕복, voice-service 업로드→저장→재시작 유지→잘못된 파일 거부) |
| `firmware-ci.yml` | `wav_header.h` g++ 호스트 테스트 + 서버 검증기 교차 확인, `bedlight.ino` Core 2.0.17 컴파일 |
| `services-ci.yml` | 계약 예시 검사, pytest (voice-service, scripts), 프론트/통합 (대상 없으면 skipped) |

## 보안 요약

- 실제 키·Wi-Fi 정보는 커밋하지 않는다 (`.env`, `firmware/**/config.h` git ignore, CI 검사).
- 녹음 파일은 개인 데이터 → git 제외.
- voice-service 8001 은 LAN 노출 → `VOICE_UPLOAD_TOKEN` 권장, 인터넷 포트포워딩 금지.
- Supabase secret key 는 db-writer 에만. 무인증 MQTT 1883 은 localhost 전용. 자세한 내용: [docs/security.md](docs/security.md)
