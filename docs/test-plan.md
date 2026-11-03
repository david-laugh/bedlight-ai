# BedLight AI — 테스트 계획

> 현재 존재하는 테스트는 **계약 검사(JSON Schema)**, **구조 검사**, **compose smoke test** 뿐이다. 나머지는 계획이다.

## 1. 테스트 계층

| 계층 | 대상 | 도구 | 위치 | 상태 |
|---|---|---|---|---|
| 구조 | 필수 디렉터리/파일, `.env` 미추적 | bash | `scripts/check_structure.sh` | **동작** |
| 계약 | 스키마 유효성, valid/invalid 예시 | jsonschema | `scripts/validate_contracts.py` | **동작** |
| 인프라 smoke | compose config/build, broker pub/sub, 정적 페이지 | docker compose | `scaffold-ci.yml` | **동작** |
| 단위 | controller, db-writer, mock-device | pytest | `services/*/tests/` | 미작성 |
| 프론트엔드 | 시뮬레이터 상태 전이, WS 처리 | Vitest | `services/simulator-web/` | 미작성 |
| 통합 | mock-device → broker → controller → simulator, → db-writer | pytest + compose `test` profile | `tests/integration/` | 미작성 |
| 펌웨어 | 컴파일 | arduino-cli | `firmware-ci.yml` | 스케치 없음 (skipped) |
| 실기기 | 누움 감지/“off” 인식 정확도, E2E | 수동 체크리스트 | `docs/` (예정) | 미작성 |
| 성능 | 구간 지연, 부하, 자원 | 아래 및 performance-plan | `tests/performance/` | 미작성 |

## 1-A. 음성 데이터 수집 (이번 단계)

| 계층 | 대상 | 위치 | 상태 |
|---|---|---|---|
| 단위 | WAV 파싱/형식 검증/품질 분석 | `services/voice-service/tests/test_wav_utils.py` | **동작** |
| 단위 | 업로드 API: 정상 저장, 잘못된 라벨/ID/샘플레이트, 스테레오·8 kHz·8-bit·float, 빈·손상·잘린 파일, 길이·크기 제한, 전부 0, 경로 조작, 중복 capture_id, 파일명 충돌, 토큰, 재시작 후 유지, 응답 스키마 | `services/voice-service/tests/test_api.py` | **동작** |
| 단위 | `check_wav.py`, `prepare_voice_dataset.py` (구간 선택, split 누수 방지, 중복 제거, override, 재실행) | `scripts/tests/` | **동작** |
| 호스트 | 펌웨어 `wav_header.h` (g++) + 생성 WAV 를 서버 검증기로 교차 확인 | `firmware/xiao-esp32s3-sense/tests/` | **동작** |
| 컴파일 | `bedlight.ino` @ Core 2.0.17 | `firmware-ci.yml` | **동작** |
| 컨테이너 | voice-service 기동, 합성 WAV 업로드 → bind mount 파일 확인 → 재시작 후 유지 → 잘못된 파일 422 | `scaffold-ci.yml` | **동작** |
| 수동 | 실제 보드 녹음·업로드·청취 (H1~H7) | [voice-dataset.md §9](voice-dataset.md) | **미검증** |

테스트 오디오는 모두 실행 시 생성하는 **합성 신호**이며 학습 데이터로 사용하지 않는다.

## 2. 기능 시나리오

| ID | 시나리오 | 기대 결과 |
|---|---|---|
| S1 | 정상 단일 명령 | 동일 `trace_id` 로 LYING_DETECTED → LISTENING_STARTED → VOICE_KEYWORD_DETECTED → LIGHT_OFF_REQUESTED → LIGHT_OFF_APPLIED(`applied`), DB 5행 |
| S2 | 연속 명령 (이미 꺼진 상태에서 재요청) | 두 번째 결과 `already_off`, 모든 이벤트 저장 |
| S3 | 1 evt/s 지속 | 유실 0, 처리 지연 안정 |
| S4 | 5 evt/s 지속 | 유실·지연 기록 |
| S5 | 20 evt/s 지속 | 병목 구간 식별 (큐 적체 위치) |
| S6 | MQTT 연결 지연/단절 | 재연결 후 QoS 1 메시지 전달, `sequence` 로 유실 집계 |
| S7 | DB 응답 지연/오류 | db-writer 재시도, controller 경로(조명 제어)는 영향 없음 |
| S8 | 동일 `event_id` 중복 수신 | controller 1회만 전달, DB 1행 |
| S9 | 시뮬레이터 연결 해제 후 복구 | 해제 중 요청 처리 정책(보관/에러) 확인, 복구 후 상태 일치 |
| S10 | 스키마 위반 이벤트 | 폐기 + 카운터 증가, 다른 이벤트 처리 계속 |
| S11 | 키워드 없이 누움만 | LIGHT_OFF_REQUESTED 미발생 |
| S12 | 누움 없이 "off" | 마이크 비활성 → 키워드 이벤트 미발생 |

S11, S12 는 실기기 시나리오, 나머지는 mock-device 로 재현.

## 3. 장애 주입 방법 (계획)

| 대상 | 방법 |
|---|---|
| MQTT 지연 | `docker compose pause mqtt-broker` / `tc netem` (Linux) |
| DB 지연 | db-writer 에 지연 주입 플래그 또는 잘못된 URL 로 실패 유도 (테스트 프로젝트 한정) |
| 시뮬레이터 해제 | 브라우저 탭 종료 / Playwright 로 WS 강제 종료 |

## 4. CI 원칙

- 대상 코드가 없으면 job 은 **skipped**. 빈 테스트를 통과로 표시하지 않는다.
- CI 에서 운영 Supabase 키를 사용하지 않는다.
