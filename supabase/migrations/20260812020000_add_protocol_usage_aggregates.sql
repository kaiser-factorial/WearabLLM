-- Privacy-safe daily protocol migration evidence.
--
-- This table stores counters only. It deliberately has no columns for request
-- paths, query parameters, device IDs, request IDs, content, credentials, or
-- payload bodies. The hosted bridge accesses it with the service role.

create table if not exists public.wearabllm_protocol_usage_daily (
    principal_id text not null
        check (char_length(principal_id) between 1 and 80),
    day date not null,
    protocol_version text not null
        check (protocol_version in ('v1', 'v2')),
    route_family text not null
        check (route_family ~ '^[a-z][a-z0-9_]{0,39}$'),
    method text not null
        check (method in ('GET', 'POST', 'OPTIONS', 'OTHER')),
    status_class text not null
        check (status_class in ('1xx', '2xx', '3xx', '4xx', '5xx')),
    client_name text not null
        check (client_name in (
            'android', 'bench-doctor', 'bench-smoke', 'preflight',
            'waveshare', 'web-console', 'unknown'
        )),
    client_version text not null
        check (client_version ~ '^(unknown|[0-9]{1,4}(\.[0-9]{1,4}){0,3}([-+][A-Za-z0-9.-]{1,20})?)$'),
    request_count bigint not null default 0
        check (request_count >= 0),
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    primary key (
        principal_id,
        day,
        protocol_version,
        route_family,
        method,
        status_class,
        client_name,
        client_version
    )
);

create index if not exists wearabllm_protocol_usage_recent_idx
    on public.wearabllm_protocol_usage_daily (principal_id, day desc);

create or replace function public.wearabllm_increment_protocol_usage(
    p_principal_id text,
    p_rows jsonb
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
    item jsonb;
    item_count bigint;
begin
    if p_principal_id is null
       or char_length(p_principal_id) not between 1 and 80 then
        raise exception 'invalid principal id';
    end if;
    if jsonb_typeof(p_rows) <> 'array'
       or jsonb_array_length(p_rows) > 500 then
        raise exception 'protocol usage batch must be an array of at most 500 rows';
    end if;

    delete from public.wearabllm_protocol_usage_daily
    where principal_id = p_principal_id
      and day < current_date - 89;

    for item in select value from jsonb_array_elements(p_rows)
    loop
        item_count := (item->>'request_count')::bigint;
        if item_count < 1 or item_count > 1000000 then
            raise exception 'invalid protocol usage request count';
        end if;
        if (item->>'protocol_version') not in ('v1', 'v2')
           or (item->>'route_family') !~ '^[a-z][a-z0-9_]{0,39}$'
           or (item->>'method') not in ('GET', 'POST', 'OPTIONS', 'OTHER')
           or (item->>'status_class') not in ('1xx', '2xx', '3xx', '4xx', '5xx')
           or (item->>'client_name') not in (
               'android', 'bench-doctor', 'bench-smoke', 'preflight',
               'waveshare', 'web-console', 'unknown'
           )
           or (item->>'client_version') !~ '^(unknown|[0-9]{1,4}(\.[0-9]{1,4}){0,3}([-+][A-Za-z0-9.-]{1,20})?)$' then
            raise exception 'invalid protocol usage dimension';
        end if;

        insert into public.wearabllm_protocol_usage_daily (
            principal_id,
            day,
            protocol_version,
            route_family,
            method,
            status_class,
            client_name,
            client_version,
            request_count
        ) values (
            p_principal_id,
            (item->>'day')::date,
            item->>'protocol_version',
            item->>'route_family',
            item->>'method',
            item->>'status_class',
            item->>'client_name',
            item->>'client_version',
            item_count
        )
        on conflict (
            principal_id,
            day,
            protocol_version,
            route_family,
            method,
            status_class,
            client_name,
            client_version
        ) do update
        set request_count = wearabllm_protocol_usage_daily.request_count + excluded.request_count,
            last_seen_at = now();
    end loop;
end;
$$;

alter table public.wearabllm_protocol_usage_daily enable row level security;

revoke all on table public.wearabllm_protocol_usage_daily
    from public, anon, authenticated;
revoke all on function public.wearabllm_increment_protocol_usage(text, jsonb)
    from public, anon, authenticated;

grant select, insert, update
    on table public.wearabllm_protocol_usage_daily to service_role;
grant execute on function public.wearabllm_increment_protocol_usage(text, jsonb)
    to service_role;

comment on table public.wearabllm_protocol_usage_daily is
    'Private aggregate-only v1/v2 usage evidence. No content, credentials, device IDs, request IDs, raw paths, or query parameters.';
