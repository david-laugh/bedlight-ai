# services/db-writer

> **상태 (펌웨어 0.3 단계): 구현됨** — MQTT 구독 → JSON Schema(v1.2 런타임/ v1.0 envelope) 검증 → event_id LRU 중복 제거 → Supabase REST 배치 INSERT(ON CONFLICT DO NOTHING, 5xx/네트워크 지수 백오프 재시도, 4xx 재시도 안 함). `SUPABASE_URL`/`SUPABASE_SECRET_KEY` 가 없으면 **DRY-RUN** (`runtime-data/db-writer/events.jsonl`, Supabase 로 전송 안 함).



## 역할 (향후 구현)

MQTT 이벤트를 구독하고, JSON Schema 검증 후 Supabase PostgreSQL `public.device_events` 에 저장한다.
**Supabase secret(service_role) key 를 사용하는 유일한 컴포넌트.**

## Input / Output 계약

| 방향 | 채널 | 대상 | 페이로드 |
|---|---|---|---|
| IN  | MQTT subscribe (QoS 1) | `bedlight/v1/devices/+/events` | `event-envelope.schema.json` |
| IN  | MQTT subscribe (QoS 1) | `bedlight/v1/simulator/+/events` | `event-envelope.schema.json` |
| IN  | MQTT subscribe (선택) | `bedlight/v1/devices/+/status` | `device-status.schema.json` (향후 별도 테이블) |
| OUT | HTTPS (PostgREST) | `POST {SUPABASE_URL}/rest/v1/device_events` | 아래 컬럼 매핑 |
| OUT | stdout | 구조화 로그 | 저장 성공/중복/실패, `db_insert_ms` |

## 컬럼 매핑

| 컬럼 | 원천 |
|---|---|
| `event_id`, `trace_id`, `source`, `device_id`, `event_type`, `sequence`, `schema_version` | envelope 동일 필드 |
| `confidence` | `data.confidence` (없으면 NULL) |
| `inference_ms` | `timing.inference_ms` (없으면 NULL) |
| `payload` | 원본 envelope 전체 (jsonb) |
| `created_at` | DB 기본값 `now()` (= 서버 수신/저장 시각) |

## 처리 규칙 (설계)

- **스키마 검증 실패**: 저장하지 않고 로그 + 카운터 증가 (향후 dead-letter 테이블 검토).
- **중복 이벤트**: `event_id` UNIQUE 제약 + `Prefer: resolution=ignore-duplicates` (ON CONFLICT DO NOTHING). 중복은 오류가 아님.
- **전송 실패 재시도**: 네트워크/5xx 는 지수 백오프(예: 0.5s→1s→2s→…, 최대 5회, jitter). 4xx(스키마/권한)는 재시도하지 않음.
- **버퍼링**: 메모리 큐 상한 설정. 상한 초과 시 가장 오래된 항목부터 폐기하고 유실 카운트 기록 (docs/performance-plan.md 유실률).
- **배치 INSERT**: 고부하 시나리오(20 evt/s) 결과에 따라 도입 여부 결정.

## 환경변수

| 변수 | 설명 |
|---|---|
| `SUPABASE_URL` | 프로젝트 URL |
| `SUPABASE_SECRET_KEY` | secret / service_role key. **서버 측 db-writer 전용** |
| `DB_WRITER_MAX_RETRIES`, `DB_WRITER_QUEUE_MAX` | 재시도/큐 상한 |

`.env` 는 compose 에서 이 서비스에만 `env_file` 로 주입된다.
