-- Private, shared WearabLLM agent personality and voice settings.
--
-- The local bridge or hosted agent owns this row through the Supabase
-- service role. Browser, Android, and firmware clients must never receive the
-- service-role credential or access this table directly.

create table if not exists public.wearabllm_agent_settings (
    principal_id text primary key
        check (char_length(principal_id) between 1 and 80),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    llm_model text not null
        check (char_length(llm_model) between 1 and 120),
    tts_model text not null
        check (char_length(tts_model) between 1 and 120),
    tts_voice text not null
        check (char_length(tts_voice) between 1 and 40),
    system_prompt text not null
        check (char_length(system_prompt) between 32 and 12000),
    tts_instructions text not null
        check (char_length(tts_instructions) between 8 and 4000)
);

create or replace function public.wearabllm_agent_settings_touch_updated_at()
returns trigger
language plpgsql
set search_path = public
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists wearabllm_agent_settings_touch_updated_at
    on public.wearabllm_agent_settings;
create trigger wearabllm_agent_settings_touch_updated_at
before update on public.wearabllm_agent_settings
for each row execute function public.wearabllm_agent_settings_touch_updated_at();

alter table public.wearabllm_agent_settings enable row level security;

revoke all on table public.wearabllm_agent_settings from anon, authenticated;
revoke all on function public.wearabllm_agent_settings_touch_updated_at()
    from public, anon, authenticated;

grant select, insert, update, delete
    on table public.wearabllm_agent_settings to service_role;

comment on table public.wearabllm_agent_settings is
    'Private per-principal WearabLLM model, prompt, and TTS settings. Server access only.';
