-- BedLight AI — architecture change: keyword spotting moved from ESP32 to the central voice-service.
-- VOICE_KEYWORD_DETECTED events are now emitted with source = 'voice-service'.
-- Not yet applied to any Supabase project.

alter table public.device_events drop constraint if exists device_events_source_chk;
alter table public.device_events
    add constraint device_events_source_chk
    check (source in ('device', 'mock', 'voice-service', 'simulator', 'controller'));

comment on column public.device_events.source is
    'device = real XIAO ESP32S3, mock = tests/mock-device, voice-service = central KWS server, simulator, controller';
