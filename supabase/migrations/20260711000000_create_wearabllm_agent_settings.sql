create table if not exists public.wearabllm_agent_settings (
    principal_id text primary key check (char_length(principal_id) between 1 and 80),
    updated_at timestamptz not null default now(),
    system_prompt text not null check (char_length(system_prompt) between 32 and 12000),
    tts_voice text not null check (char_length(tts_voice) between 1 and 40),
    tts_instructions text not null check (char_length(tts_instructions) between 8 and 4000),
    tts_model text not null check (char_length(tts_model) between 1 and 120),
    llm_model text not null check (char_length(llm_model) between 1 and 120)
);

alter table public.wearabllm_agent_settings enable row level security;
revoke all on table public.wearabllm_agent_settings from anon, authenticated;
grant select, insert, update, delete on table public.wearabllm_agent_settings to service_role;

comment on table public.wearabllm_agent_settings is
    'Runtime WearabLLM agent personality/config edited from Sphere. Hosted bridge only, service role.';
