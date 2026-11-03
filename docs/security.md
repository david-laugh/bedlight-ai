# BedLight AI — 보안 설계

## 1. 비밀값 관리

| 비밀값 | 저장 위치 | 사용 주체 |
|---|---|---|
| Supabase secret / service_role key | 로컬 `.env` (커밋 금지), CI 는 GitHub Secrets | **db-writer 만** |
| MQTT 계정/비밀번호 | `.env`, `infra/mosquitto/passwd` (커밋 금지) | 각 서비스, ESP32 |
| WiFi / MQTT 계정 (펌웨어) | `firmware/.../secrets.h` (커밋 금지, `.example` 만 커밋) | ESP32 |
| Edge Impulse API key | GitHub Secrets (CI 다운로드 시) | firmware-ci |

- `.env.example` 에는 변수명과 `<placeholder>` 만 둔다.
- `scripts/check_structure.sh` 는 `.env` 가 git 에 추적되면 실패한다.
- compose 는 `.env` 를 `env_file` 로 **db-writer 에만** 주입한다. controller, simulator-web, mock-device 에는 Supabase 키가 없다.
- 브라우저(simulator-web), ESP32 펌웨어에는 어떤 Supabase 키도 넣지 않는다. 디바이스는 MQTT 로만 통신한다.

## 2. MQTT

| 단계 | 설정 | 노출 범위 |
|---|---|---|
| 개발 (현재) | `mosquitto.conf`, `allow_anonymous true` | host `127.0.0.1:1883` 만 바인딩 |
| LAN 실기기 | `mosquitto.auth.conf.example` + `passwd` + `acl` | LAN IP 바인딩, 공유기 포트포워딩 금지 |
| 원격 접근 (권장하지 않음) | TLS 8883 + 인증 + ACL | 1883 무인증 포트는 절대 공개 금지 |

- **무인증 1883 포트를 인터넷에 공개하지 않는다.** 공개 시 누구나 `LIGHT_OFF_REQUESTED` 를 위조하거나 이벤트를 수집할 수 있다.
- ACL 원칙: 디바이스는 `devices/{자기 id}/#` 에만 쓰기, db-writer 는 읽기 전용, controller 는 `simulator/+/events` 쓰기.
- `message_size_limit 65536` 으로 대용량 페이로드 차단.

## 2-A. Voice Service (포트 8001)

- ESP32 가 LAN 에서 접속해야 하므로 **8001 은 0.0.0.0 에 바인딩**된다 (다른 서비스는 127.0.0.1).
  `VOICE_BIND_ADDR` 로 특정 LAN IP 에만 바인딩할 수 있다.
- `VOICE_UPLOAD_TOKEN` 설정 시 `X-Device-Token` 헤더 필수 (`hmac.compare_digest`). 펌웨어 `config.h` 에 같은 값.
  평문 HTTP 이므로 같은 LAN 의 도청은 막지 못한다 → 신뢰할 수 있는 가정용 Wi-Fi 에서만 사용, 공유기 포트포워딩 금지.
- 파일명은 서버가 생성, label allow-list, ID 정규식, 경로 resolve 재검증, 크기·길이 상한 → 경로 조작/디스크 고갈 완화.
- Wi-Fi SSID/비밀번호와 토큰은 `firmware/.../config.h` (git ignore). `scripts/check_structure.sh` 가 추적 여부를 검사.
- 녹음은 개인 음성 데이터 → `datasets/voice/**` WAV·metadata 는 커밋 금지 (`.gitignore` + CI 검사).

## 3. Supabase

- `public.device_events` 는 RLS 활성화 + 정책 없음 → PostgREST 로 `anon`/`authenticated` 접근 시 0 행 / 권한 오류.
- `revoke all ... from anon, authenticated` 로 권한 자체를 제거 (이중 방어).
- service_role(secret key)는 RLS 를 우회하므로 서버 측 db-writer 에서만 사용하고, 로그에 출력하지 않는다.
- 적용 후 검증 체크리스트 (마이그레이션 적용 시 수행, 아직 미수행):
  - [ ] publishable/anon key 로 `GET /rest/v1/device_events` → 데이터 반환 없음
  - [ ] publishable/anon key 로 `POST` → 거부
  - [ ] Supabase Dashboard Security Advisor 경고 없음

## 4. 개인정보

- 카메라 이미지는 디바이스 밖으로 전송하지 않는다 (Vision 추론 결과만 전송).
- 음성은 아키텍처 v2 에서 **LAN 안의 voice-service 로 전송**된다 (데이터 수집 및 중앙 KWS). 인터넷/클라우드로 직접 보내지 않으며,
  Edge Impulse 업로드는 사용자가 정리된 데이터셋을 수동으로 올리는 경우에만 일어난다.
- 학습 데이터셋(침실 영상, 음성)은 Repository 에 커밋하지 않는다 (`models/**/dataset/` ignore).

## 5. 컨테이너

- Python 컨테이너는 non-root 사용자(uid 10001)로 실행.
- 모든 host 포트는 `127.0.0.1` 바인딩.
- 이미지 태그는 버전 고정 (`eclipse-mosquitto:2.0.20`, `python:3.11-slim`, `nginx:1.27-alpine`).
