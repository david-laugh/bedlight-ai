# BedLight AI

XIAO ESP32S3 Sense 의 카메라로 사람이 침대에 누운 상태를 감지하고, 내장 마이크로 음성 명령 **"off"** 를 인식하면
가상 침실 시뮬레이터의 조명을 끄는 Edge AI 기반 스마트 침실 시스템.

- **AI 추론은 ESP32 에서만** 수행한다 (Edge Impulse: Vision 분류 + Keyword Spotting).
- Docker 서비스는 이벤트 중계(MQTT), 가상 환경(웹 시뮬레이터), 로그 저장(Supabase), 성능 측정을 담당한다.

> ## 현재 상태: Repository scaffold
> 설계·계약·Docker 환경·CI 골격만 존재한다. **펌웨어, AI 모델, Controller/DB Writer 로직, 시뮬레이터, Mock 이벤트 생성은 구현되지 않았다.**
> placeholder 컨테이너가 실행된다는 것은 해당 서비스 기능이 동작한다는 의미가 아니다.

## 구성

```
ESP32 ──MQTT──▶ mqtt-broker ──▶ controller ◀──WebSocket──▶ simulator-web
mock-device ──▶ mqtt-broker ──▶ db-writer ──HTTPS──▶ Supabase PostgreSQL
```

| 서비스 | 기술 | 상태 |
|---|---|---|
| `mqtt-broker` | Eclipse Mosquitto 2.0 | 동작 (개발용 무인증, `127.0.0.1:1883`) |
| `controller` | Python 3.11 / FastAPI (예정) | placeholder |
| `db-writer` | Python 3.11 (예정) | placeholder |
| `simulator-web` | React + Vite (예정), 현재 nginx 정적 페이지 | placeholder (`127.0.0.1:8080`) |
| `mock-device` | Python 3.11, compose profile `test` | placeholder |

## 빠른 시작

```bash
cp .env.example .env            # 선택. 없어도 기본값으로 기동됨 (Supabase 키는 아직 사용 안 함)
docker compose up -d --build
docker compose ps
docker compose --profile test up --build mock-device   # mock-device placeholder 확인
docker compose --profile test down -v
```

검증 스크립트:

```bash
bash scripts/check_structure.sh
pip install -r scripts/requirements-ci.txt && python scripts/validate_contracts.py
```

## 디렉터리

| 경로 | 내용 |
|---|---|
| `firmware/xiao-esp32s3-sense/` | 펌웨어 (미구현), 툴체인 버전 고정 `toolchain.env` |
| `models/vision/`, `models/voice/` | Edge Impulse 모델 카드/설정 (미학습) |
| `services/controller/` | MQTT ↔ WebSocket 중계 (placeholder) |
| `services/db-writer/` | MQTT → Supabase 저장 (placeholder) |
| `services/simulator-web/` | 가상 침실 웹 (placeholder 정적 페이지) |
| `infra/mosquitto/` | Mosquitto 개발 설정 + 인증/ACL 템플릿 |
| `contracts/v1/` | 이벤트 JSON Schema, topic 매핑, valid/invalid 예시 |
| `supabase/migrations/` | `device_events` 테이블 + RLS (미적용) |
| `tests/mock-device/` | ESP32 모의 이벤트 생성기 (placeholder) |
| `tests/integration/`, `tests/performance/` | 통합/성능 테스트 (미작성) |
| `scripts/` | 구조 검사, 계약 검사 |
| `docs/` | 설계 문서 |
| `.github/workflows/` | CI |

## 문서

- [아키텍처](docs/architecture.md)
- [인터페이스 계약 (MQTT/WS/컨테이너 I/O)](docs/interfaces.md)
- [DB 설계](docs/database.md)
- [보안](docs/security.md)
- [테스트 계획](docs/test-plan.md)
- [성능·병목 평가 계획](docs/performance-plan.md)

## CI

| Workflow | 내용 | 현재 실행되는 것 |
|---|---|---|
| `scaffold-ci.yml` | 구조, 스키마, compose config/build, smoke | 전부 |
| `firmware-ci.yml` | arduino-cli + ESP32 Core 2.0.17 컴파일 | detect 만 (스케치 없음 → compile **skipped**) |
| `services-ci.yml` | 계약 예시 검사, pytest, 프론트 빌드, 통합 | contracts 만 (나머지 대상 없음 → **skipped**) |

## 보안 요약

- 실제 키는 커밋하지 않는다. `.env.example` 은 placeholder 만 포함.
- Supabase secret/service_role key 는 **db-writer 컨테이너에만** 주입.
- 무인증 MQTT 1883 포트를 인터넷에 공개하지 않는다. 자세한 내용: [docs/security.md](docs/security.md)
