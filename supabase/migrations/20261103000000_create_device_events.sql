-- BedLight AI — device_events
-- 설계 문서: docs/database.md
-- 적용: Supabase CLI (`supabase db push`) 또는 SQL Editor 에서 수동 실행.
-- 이 파일은 아직 실제 Supabase 프로젝트에 적용되지 않았다.

create table if not exists public.device_events (
    id              bigint generated always as identity primary key,
    event_id        uuid        not null,
    trace_id        uuid        not null,
    source          text        not null,
    device_id       text        not null,
    event_type      text        not null,
    sequence        bigint,
    schema_version  text        not null default '1.0.0',
    confidence      real,
    inference_ms    real,
    payload         jsonb       not null,
    created_at      timestamptz not null default now(),

    constraint device_events_event_id_key unique (event_id),
    constraint device_events_source_chk
        check (source in ('device', 'mock', 'simulator', 'controller')),
    constraint device_events_event_type_chk
        check (event_type in (
            'LYING_DETECTED', 'LYING_LOST', 'LISTENING_STARTED',
            'VOICE_KEYWORD_DETECTED', 'LIGHT_OFF_REQUESTED',
            'LIGHT_OFF_APPLIED', 'SYSTEM_ERROR')),
    constraint device_events_confidence_chk
        check (confidence is null or (confidence >= 0 and confidence <= 1)),
    constraint device_events_inference_ms_chk
        check (inference_ms is null or inference_ms >= 0)
);

comment on table  public.device_events is 'BedLight AI inference results and system events (one row per event_id).';
comment on column public.device_events.source     is 'device = real XIAO ESP32S3, mock = tests/mock-device, simulator, controller';
comment on column public.device_events.payload    is 'Full original event envelope (contracts/v1/event-envelope.schema.json)';
comment on column public.device_events.created_at is 'Server-side insert time (db-writer receive/store), not device time';

-- 조회 패턴: trace 단위 재구성, 디바이스별 최근 이벤트, 유형별 집계
create index if not exists device_events_trace_id_idx
    on public.device_events (trace_id);
create index if not exists device_events_device_created_idx
    on public.device_events (device_id, created_at desc);
create index if not exists device_events_type_created_idx
    on public.device_events (event_type, created_at desc);

-- 보안: RLS 활성화 + 클라이언트 역할 권한 제거.
-- 정책(policy)을 만들지 않으므로 anon/authenticated 는 어떤 행도 읽거나 쓸 수 없다.
-- db-writer 는 secret(service_role) key 로 접근하며 service_role 은 RLS 를 우회한다.
alter table public.device_events enable row level security;
revoke all on table public.device_events from anon, authenticated;
revoke all on sequence public.device_events_id_seq from anon, authenticated;
