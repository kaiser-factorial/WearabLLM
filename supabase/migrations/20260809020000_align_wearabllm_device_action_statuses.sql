-- Align the hosted device-action table with the board acknowledgement protocol.
-- Existing queued work is retained while legacy status names are normalized.

alter table public.wearabllm_device_actions
    drop constraint if exists wearabllm_device_actions_status_check;

update public.wearabllm_device_actions
set status = case status
    when 'pending' then 'queued'
    when 'claimed' then 'dispatched'
    else status
end
where status in ('pending', 'claimed');

alter table public.wearabllm_device_actions
    add constraint wearabllm_device_actions_status_check
    check (status in (
        'queued',
        'dispatched',
        'delivered',
        'rendered',
        'tts_started',
        'played',
        'failed',
        'expired'
    ));

drop index if exists public.wearabllm_device_actions_target_pending_idx;
create index wearabllm_device_actions_target_pending_idx
    on public.wearabllm_device_actions (principal_id, target_device_id, created_at)
    where status in ('queued', 'dispatched');

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
              status = 'queued'
              or (status = 'dispatched' and lease_expires_at <= now())
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
