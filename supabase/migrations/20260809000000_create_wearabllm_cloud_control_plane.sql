-- WearabLLM cloud control-plane foundation.
--
-- This migration is deliberately service-role only. The public app and board
-- never receive the Supabase service-role key; the hosted agent mediates every
-- operation after checking a device-specific credential.

create table if not exists public.wearabllm_device_actions (
    id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    principal_id text not null check (char_length(principal_id) between 1 and 80),
    origin_device_id text not null check (origin_device_id ~ '^[A-Za-z0-9._-]{1,80}$'),
    target_device_id text not null check (target_device_id ~ '^[A-Za-z0-9._-]{1,80}$'),
    idempotency_key text check (idempotency_key is null or char_length(idempotency_key) between 1 and 160),
    transcript text not null check (char_length(transcript) between 1 and 4000),
    command text not null check (command in ('GS', 'GP', 'GC', 'RS', 'RF', 'YP', 'BS', 'PS', 'PP')),
    reply text not null check (char_length(reply) between 1 and 8000),
    status text not null default 'pending'
        check (status in ('pending', 'claimed', 'delivered', 'played', 'failed', 'expired')),
    delivery_attempts integer not null default 0 check (delivery_attempts >= 0),
    lease_expires_at timestamptz,
    delivered_at timestamptz,
    played_at timestamptz,
    failed_at timestamptz,
    error text check (error is null or char_length(error) <= 2000)
);

create unique index if not exists wearabllm_device_actions_idempotency_idx
    on public.wearabllm_device_actions (principal_id, origin_device_id, idempotency_key)
    where idempotency_key is not null;

create index if not exists wearabllm_device_actions_target_pending_idx
    on public.wearabllm_device_actions (principal_id, target_device_id, created_at)
    where status in ('pending', 'claimed');

create table if not exists public.wearabllm_memory_records (
    id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    last_confirmed_at timestamptz,
    expires_at timestamptz,
    principal_id text not null check (char_length(principal_id) between 1 and 80),
    subject text not null default 'principal' check (char_length(subject) between 1 and 120),
    kind text not null check (kind in ('preference', 'person', 'relationship', 'household', 'routine', 'fact', 'instruction')),
    content text not null check (char_length(content) between 8 and 1200),
    tags text[] not null default '{}',
    importance smallint not null default 3 check (importance between 1 and 5),
    confidence numeric(3,2) not null default 0.70 check (confidence between 0 and 1),
    status text not null default 'active' check (status in ('active', 'superseded', 'forgotten')),
    source text not null default 'wearabllm-auto-extract' check (char_length(source) between 1 and 80),
    source_device_id text check (source_device_id is null or source_device_id ~ '^[A-Za-z0-9._-]{1,80}$'),
    source_turn_id bigint references public.wearabllm_conversation_turns (id),
    supersedes_id uuid references public.wearabllm_memory_records (id),
    check (expires_at is null or expires_at > created_at)
);

create index if not exists wearabllm_memory_records_active_idx
    on public.wearabllm_memory_records (principal_id, importance desc, updated_at desc)
    where status = 'active';

create index if not exists wearabllm_memory_records_subject_idx
    on public.wearabllm_memory_records (principal_id, subject, updated_at desc)
    where status = 'active';

create or replace function public.wearabllm_touch_updated_at()
returns trigger
language plpgsql
set search_path = public
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists wearabllm_device_actions_touch_updated_at on public.wearabllm_device_actions;
create trigger wearabllm_device_actions_touch_updated_at
before update on public.wearabllm_device_actions
for each row execute function public.wearabllm_touch_updated_at();

drop trigger if exists wearabllm_memory_records_touch_updated_at on public.wearabllm_memory_records;
create trigger wearabllm_memory_records_touch_updated_at
before update on public.wearabllm_memory_records
for each row execute function public.wearabllm_touch_updated_at();

-- Atomically lease one pending action to the requesting board. A board that
-- dies after claiming an action becomes eligible again once its lease expires.
create or replace function public.wearabllm_claim_next_device_action(
    p_principal_id text,
    p_target_device_id text,
    p_lease_seconds integer default 45
)
returns setof public.wearabllm_device_actions
language plpgsql
security definer
set search_path = public
as $$
begin
    if p_lease_seconds < 5 or p_lease_seconds > 300 then
        raise exception 'lease seconds must be between 5 and 300';
    end if;

    return query
    with candidate as (
        select id
        from public.wearabllm_device_actions
        where principal_id = p_principal_id
          and target_device_id = p_target_device_id
          and (
              status = 'pending'
              or (status = 'claimed' and lease_expires_at <= now())
          )
        order by created_at asc
        for update skip locked
        limit 1
    )
    update public.wearabllm_device_actions as action
    set status = 'claimed',
        lease_expires_at = now() + make_interval(secs => p_lease_seconds),
        delivery_attempts = action.delivery_attempts + 1,
        error = null
    from candidate
    where action.id = candidate.id
    returning action.*;
end;
$$;

alter table public.wearabllm_device_actions enable row level security;
alter table public.wearabllm_memory_records enable row level security;

revoke all on table public.wearabllm_device_actions from anon, authenticated;
revoke all on table public.wearabllm_memory_records from anon, authenticated;
revoke all on function public.wearabllm_claim_next_device_action(text, text, integer) from public, anon, authenticated;

grant select, insert, update, delete on table public.wearabllm_device_actions to service_role;
grant select, insert, update, delete on table public.wearabllm_memory_records to service_role;
grant execute on function public.wearabllm_claim_next_device_action(text, text, integer) to service_role;

comment on table public.wearabllm_device_actions is
    'Private durable phone/dashboard-to-device actions. The hosted WearabLLM agent mediates all access.';
comment on table public.wearabllm_memory_records is
    'Private long-term WearabLLM context: user preferences, roomies, household facts, routines, and instructions with provenance and expiry.';
