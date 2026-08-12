-- Give every body the same semantic expression payload while preserving the
-- command/reply columns consumed by the currently flashed Waveshare firmware.

alter table public.wearabllm_device_actions
    add column if not exists action_type text not null default 'expression'
        check (action_type in ('expression')),
    add column if not exists expression jsonb not null default '{}'::jsonb,
    add column if not exists expires_at timestamptz,
    add column if not exists completed_at timestamptz;

alter table public.wearabllm_device_actions
    alter column status set default 'queued';

alter table public.wearabllm_device_actions
    drop constraint if exists wearabllm_device_actions_status_check;

alter table public.wearabllm_device_actions
    add constraint wearabllm_device_actions_status_check
    check (status in (
        'queued',
        'dispatched',
        'delivered',
        'rendered',
        'tts_started',
        'completed',
        'played',
        'failed',
        'expired'
    ));

update public.wearabllm_device_actions
set expression = jsonb_build_object(
    'version', 1,
    'command', command,
    'text', reply,
    'channels', jsonb_build_array('visual', 'display', 'audio')
)
where expression = '{}'::jsonb;

alter table public.wearabllm_device_actions
    drop constraint if exists wearabllm_device_actions_expression_shape_check;
alter table public.wearabllm_device_actions
    add constraint wearabllm_device_actions_expression_shape_check
    check (
        expression ? 'version'
        and expression ? 'command'
        and expression ? 'text'
        and expression ? 'channels'
        and jsonb_typeof(expression -> 'channels') = 'array'
        and expression ->> 'command' = command
        and expression ->> 'text' = reply
    );

drop index if exists public.wearabllm_device_actions_expiry_idx;
create index wearabllm_device_actions_expiry_idx
    on public.wearabllm_device_actions (principal_id, target_device_id, expires_at)
    where status not in ('completed', 'played', 'failed', 'expired') and expires_at is not null;

drop index if exists public.wearabllm_device_actions_target_pending_idx;
create index wearabllm_device_actions_target_pending_idx
    on public.wearabllm_device_actions (principal_id, target_device_id, created_at)
    where status not in ('completed', 'played', 'failed', 'expired');

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
          and (expires_at is null or expires_at > now())
          and (
              status = 'queued'
              or (
                  status in ('dispatched', 'delivered', 'rendered', 'tts_started')
                  and (lease_expires_at is null or lease_expires_at <= now())
              )
          )
        order by created_at asc
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

comment on column public.wearabllm_device_actions.expression is
    'Device-neutral Sphere expression. Each body maps the semantic command and requested channels to its own renderer.';
