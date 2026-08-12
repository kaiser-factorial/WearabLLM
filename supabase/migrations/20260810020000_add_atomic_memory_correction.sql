create or replace function public.wearabllm_correct_memory(
    p_principal_id text,
    p_memory_id uuid,
    p_subject text,
    p_kind text,
    p_content text,
    p_tags text[],
    p_source_device_id text
)
returns setof public.wearabllm_memory_records
language plpgsql
security definer
set search_path = public
as $$
declare
    current_record public.wearabllm_memory_records%rowtype;
    replacement public.wearabllm_memory_records%rowtype;
begin
    select * into current_record
    from public.wearabllm_memory_records
    where id = p_memory_id
      and principal_id = p_principal_id
      and status = 'active'
      and (expires_at is null or expires_at > now())
    for update;

    if not found then
        raise exception 'Active memory not found';
    end if;
    if lower(btrim(p_content)) = lower(btrim(current_record.content)) then
        raise exception 'A correction must change the memory content';
    end if;

    insert into public.wearabllm_memory_records (
        principal_id,
        subject,
        kind,
        content,
        tags,
        importance,
        confidence,
        source,
        source_device_id,
        supersedes_id,
        last_confirmed_at,
        expires_at
    ) values (
        p_principal_id,
        coalesce(nullif(btrim(p_subject), ''), current_record.subject),
        coalesce(nullif(btrim(p_kind), ''), current_record.kind),
        btrim(p_content),
        coalesce(p_tags, current_record.tags),
        current_record.importance,
        1.0,
        'wearabllm-explicit-tool',
        nullif(btrim(p_source_device_id), ''),
        current_record.id,
        now(),
        current_record.expires_at
    ) returning * into replacement;

    update public.wearabllm_memory_records
    set status = 'superseded'
    where id = current_record.id;

    return next replacement;
end;
$$;

revoke all on function public.wearabllm_correct_memory(text, uuid, text, text, text, text[], text)
    from public, anon, authenticated;
grant execute on function public.wearabllm_correct_memory(text, uuid, text, text, text, text[], text)
    to service_role;

comment on function public.wearabllm_correct_memory(text, uuid, text, text, text, text[], text) is
    'Atomically supersedes one active memory with an explicit corrected record.';
