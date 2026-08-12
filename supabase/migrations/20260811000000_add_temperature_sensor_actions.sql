-- Add bounded, due-time-aware temperature requests to the existing private
-- device action queue. The ESP32 remains outbound-only and claims work over
-- the same authenticated endpoint as every other Sphere body.

alter table public.wearabllm_device_actions
    add column if not exists payload jsonb not null default '{}'::jsonb,
    add column if not exists result jsonb,
    add column if not exists available_at timestamptz not null default now();

alter table public.wearabllm_device_actions
    drop constraint if exists wearabllm_device_actions_action_type_check;
alter table public.wearabllm_device_actions
    add constraint wearabllm_device_actions_action_type_check
    check (action_type in ('expression', 'temperature_measurement'));

alter table public.wearabllm_device_actions
    drop constraint if exists wearabllm_device_actions_expression_shape_check;
alter table public.wearabllm_device_actions
    add constraint wearabllm_device_actions_expression_shape_check
    check (
        action_type <> 'expression'
        or (
            expression ? 'version'
            and expression ? 'command'
            and expression ? 'text'
            and expression ? 'channels'
            and jsonb_typeof(expression -> 'channels') = 'array'
            and expression ->> 'command' = command
            and expression ->> 'text' = reply
        )
    );

alter table public.wearabllm_device_actions
    drop constraint if exists wearabllm_device_actions_temperature_payload_check;
alter table public.wearabllm_device_actions
    add constraint wearabllm_device_actions_temperature_payload_check
    check (
        action_type <> 'temperature_measurement'
        or (
            payload ->> 'version' = '1'
            and payload ? 'schedule_id'
            and payload ? 'schedule_index'
            and payload ? 'schedule_count'
        )
    );

drop index if exists public.wearabllm_device_actions_target_pending_idx;
create index wearabllm_device_actions_target_pending_idx
    on public.wearabllm_device_actions
        (principal_id, target_device_id, available_at, created_at)
    where status not in ('completed', 'played', 'failed', 'expired');

create index if not exists wearabllm_temperature_schedule_idx
    on public.wearabllm_device_actions
        (principal_id, (payload ->> 'schedule_id'), available_at)
    where action_type = 'temperature_measurement';

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

    update public.wearabllm_device_actions
    set status = 'expired',
        lease_expires_at = null,
        error = 'Action expired before delivery'
    where principal_id = p_principal_id
      and target_device_id = p_target_device_id
      and status not in ('completed', 'played', 'failed', 'expired')
      and expires_at is not null
      and expires_at <= now();

    return query
    with candidate as (
        select id
        from public.wearabllm_device_actions
        where principal_id = p_principal_id
          and target_device_id = p_target_device_id
          and available_at <= now()
          and (expires_at is null or expires_at > now())
          and (
              status = 'queued'
              or (
                  status in ('dispatched', 'delivered', 'rendered', 'tts_started')
                  and (lease_expires_at is null or lease_expires_at <= now())
              )
          )
        order by available_at asc, created_at asc
        for update skip locked
        limit 1
    )
    update public.wearabllm_device_actions as action
    set status = 'dispatched',
        lease_expires_at = now() + make_interval(secs => p_lease_seconds),
        delivery_attempts = action.delivery_attempts + 1,
        error = null
    from candidate
    where action.id = candidate.id
    returning action.*;
end;
$$;

revoke all on function public.wearabllm_claim_next_device_action(text, text, integer)
    from public, anon, authenticated;
grant execute on function public.wearabllm_claim_next_device_action(text, text, integer)
    to service_role;
