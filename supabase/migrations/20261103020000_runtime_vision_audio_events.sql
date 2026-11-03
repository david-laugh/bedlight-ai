-- BedLight AI — firmware 0.3 runtime events (Fall-triggered listening, audio chunk upload).
-- Raw audio is NEVER stored in the database; only metadata / event logs.
-- Not yet applied to any Supabase project.

alter table public.device_events drop constraint if exists device_events_event_type_chk;
alter table public.device_events
    add constraint device_events_event_type_chk
    check (event_type in (
        -- contracts v1.0/1.1 envelope
        'LYING_DETECTED', 'LYING_LOST', 'LISTENING_STARTED', 'VOICE_KEYWORD_DETECTED',
        'LIGHT_OFF_REQUESTED', 'LIGHT_OFF_APPLIED', 'SYSTEM_ERROR',
        -- contracts v1.2 device runtime events
        'VISION_FALL_DETECTED', 'VISION_STANDING_DETECTED', 'LISTENING_EXTENDED',
        'AUDIO_CHUNK_SENT', 'LISTENING_STOPPED', 'LIGHT_STATE_CHANGED'));

-- Promoted metadata for analysis (also present inside payload).
alter table public.device_events add column if not exists session_id uuid;
alter table public.device_events add column if not exists device_timestamp_ms bigint;   -- device epoch ms (null if not SNTP-synced)
alter table public.device_events add column if not exists network_ms real;             -- AUDIO_CHUNK_SENT upload time
alter table public.device_events add column if not exists ok boolean;                  -- AUDIO_CHUNK_SENT success

create index if not exists device_events_session_idx on public.device_events (session_id, created_at);

comment on column public.device_events.inference_ms is 'Vision (FOMO) inference time for vision/listening events';
comment on column public.device_events.network_ms   is 'HTTP upload time measured on the device (AUDIO_CHUNK_SENT)';
