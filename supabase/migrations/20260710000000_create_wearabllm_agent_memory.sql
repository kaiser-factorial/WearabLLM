create table if not exists public.wearabllm_memories (
    id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    principal_id text not null check (char_length(principal_id) between 1 and 80),
    memory_key text not null check (memory_key ~ '^[a-f0-9]{24}$'),
    content text not null check (char_length(content) between 8 and 400),
    source text not null default 'wearabllm-auto-extract' check (char_length(source) <= 80),
    unique (principal_id, memory_key)
);

create index if not exists wearabllm_memories_principal_updated_idx
    on public.wearabllm_memories (principal_id, updated_at desc);

alter table public.wearabllm_memories enable row level security;

revoke all on table public.wearabllm_memories from anon, authenticated;
grant select, insert, update, delete on table public.wearabllm_memories to service_role;

comment on table public.wearabllm_memories is
    'Private long-term WearabLLM agent memory. The hosted bridge accesses it only with the server-side service role.';
