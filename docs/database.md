# BedLight AI — Supabase DB 설계

Supabase(관리형 PostgreSQL)를 외부 DB 로 사용한다. Docker Compose 에 DB 컨테이너를 추가하지 않는다.
마이그레이션: [`supabase/migrations/20261103000000_create_device_events.sql`](../supabase/migrations/20261103000000_create_device_events.sql) — **작성만 되었고 아직 적용되지 않았다.**

## 1. `public.device_events`

| 컬럼 | 타입 | 제약 | 원천 |
|---|---|---|---|
| `id` | bigint identity | PK | DB |
| `event_id` | uuid | NOT NULL, **UNIQUE** | envelope |
| `trace_id` | uuid | NOT NULL, index | envelope |
| `source` | text | CHECK (device, mock, simulator, controller) | envelope |
| `device_id` | text | NOT NULL | envelope |
| `event_type` | text | CHECK (7종) | envelope |
| `sequence` | bigint | nullable | envelope (추가 컬럼) |
| `schema_version` | text | default `1.0.0` | envelope (추가 컬럼) |
| `confidence` | real | 0–1 또는 NULL | `data.confidence` |
| `inference_ms` | real | ≥0 또는 NULL | `timing.inference_ms` |
| `payload` | jsonb | NOT NULL | 원본 envelope 전체 |
| `created_at` | timestamptz | default `now()` | DB 저장 시각 |

`sequence`, `schema_version` 은 필수 컬럼 외 추가 컬럼이다 (유실 분석과 계약 버전 추적용).

### 인덱스

- `(trace_id)` — 한 명령 흐름 재구성 / 구간 지연 계산
- `(device_id, created_at desc)` — 디바이스별 최근 이벤트
- `(event_type, created_at desc)` — 유형별 집계

### 중복 처리

`event_id` UNIQUE. db-writer 는 PostgREST `POST /rest/v1/device_events` 에
`Prefer: resolution=ignore-duplicates,return=minimal` 헤더를 사용하여 `ON CONFLICT (event_id) DO NOTHING` 동작으로 멱등 저장한다.

## 2. 분석 쿼리 예시

```sql
-- trace 단위 end-to-end (device 이벤트 저장 ~ simulator 결과 저장)
select trace_id,
       min(created_at) filter (where event_type = 'LIGHT_OFF_REQUESTED') as requested_at,
       min(created_at) filter (where event_type = 'LIGHT_OFF_APPLIED')   as applied_at
from public.device_events
where source in ('device', 'simulator')
group by trace_id;

-- 실기기만 vision 추론시간 P95
select percentile_cont(0.95) within group (order by inference_ms)
from public.device_events
where source = 'device' and event_type in ('LYING_DETECTED', 'LYING_LOST');
```

## 3. 권한 / RLS

[`security.md`](security.md#3-supabase) 참조. 요약:

- RLS **활성화**, 정책 없음 → `anon`, `authenticated` 역할은 읽기/쓰기 불가.
- `anon`, `authenticated` 의 테이블·시퀀스 권한 `REVOKE`.
- db-writer 만 secret(service_role) key 로 접근 (RLS 우회).
- 향후 대시보드에서 조회가 필요하면 `authenticated` 대상 SELECT 전용 정책을 별도 마이그레이션으로 추가하고, 쓰기 권한은 계속 서버에만 둔다.

## 4. 향후 확장 (미구현)

- `device_status` 테이블 (status topic 저장)
- `invalid_events` dead-letter 테이블
- 보존 정책: `created_at` 기준 N일 후 삭제 (pg_cron)
