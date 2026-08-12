-- Private hybrid lexical + semantic retrieval for Sphere household memory.
--
-- The current household corpus is deliberately small, so this uses an exact
-- vector scan. Add an HNSW index only after row count and latency justify it.

create extension if not exists vector with schema extensions;

alter table public.wearabllm_memory_records
    add column if not exists embedding extensions.vector(512),
    add column if not exists embedding_model text,
    add column if not exists embedded_at timestamptz;

alter table public.wearabllm_memory_records
    drop constraint if exists wearabllm_memory_embedding_complete;
alter table public.wearabllm_memory_records
    add constraint wearabllm_memory_embedding_complete check (
        (embedding is null and embedding_model is null and embedded_at is null)
        or
        (embedding is not null and embedding_model is not null and embedded_at is not null)
    );

drop function if exists public.wearabllm_correct_memory(text, uuid, text, text, text, text[], text);

create function public.wearabllm_correct_memory(
    p_principal_id text,
    p_memory_id uuid,
    p_subject text,
    p_kind text,
    p_content text,
    p_tags text[],
    p_source_device_id text,
    p_embedding extensions.vector(512),
    p_embedding_model text
)
returns setof public.wearabllm_memory_records
language plpgsql
security definer
set search_path = pg_catalog, public, extensions
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
    if (p_embedding is null) <> (nullif(btrim(p_embedding_model), '') is null) then
        raise exception 'Embedding and embedding model must be supplied together';
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
        expires_at,
        embedding,
        embedding_model,
        embedded_at
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
        current_record.expires_at,
        p_embedding,
        nullif(btrim(p_embedding_model), ''),
        case when p_embedding is null then null else now() end
    ) returning * into replacement;

    update public.wearabllm_memory_records
    set status = 'superseded'
    where id = current_record.id;

    return next replacement;
end;
$$;

revoke all on function public.wearabllm_correct_memory(
    text, uuid, text, text, text, text[], text, extensions.vector, text
) from public, anon, authenticated;
grant execute on function public.wearabllm_correct_memory(
    text, uuid, text, text, text, text[], text, extensions.vector, text
) to service_role;

create or replace function public.wearabllm_search_memory(
    p_principal_id text,
    p_query_text text,
    p_query_embedding extensions.vector(512),
    p_subject text default '',
    p_kinds text[] default '{}',
    p_limit integer default 5
)
returns table (
    id uuid,
    subject text,
    kind text,
    content text,
    tags text[],
    importance smallint,
    confidence numeric(3,2),
    status text,
    source text,
    source_device_id text,
    source_turn_id bigint,
    supersedes_id uuid,
    created_at timestamptz,
    updated_at timestamptz,
    last_confirmed_at timestamptz,
    expires_at timestamptz,
    lexical_score real,
    semantic_score real,
    hybrid_score real
)
language sql
stable
security definer
set search_path = pg_catalog, public, extensions
as $$
    with prepared as (
        select
            m.*,
            setweight(to_tsvector('english', coalesce(m.subject, '')), 'A')
                || setweight(to_tsvector('english', coalesce(m.content, '')), 'A')
                || setweight(to_tsvector('english', array_to_string(m.tags, ' ')), 'B') as document,
            websearch_to_tsquery('english', p_query_text) as query
        from public.wearabllm_memory_records as m
        where m.principal_id = p_principal_id
          and m.status = 'active'
          and (m.expires_at is null or m.expires_at > now())
          and (nullif(btrim(p_subject), '') is null or position(lower(p_subject) in lower(m.subject)) > 0)
          and (coalesce(cardinality(p_kinds), 0) = 0 or m.kind = any(p_kinds))
    ),
    scored as (
        select
            prepared.*,
            ts_rank_cd(prepared.document, prepared.query, 32)::real as lexical,
            case
                when p_query_embedding is null or prepared.embedding is null then 0.0::real
                else greatest(0.0, 1.0 - (prepared.embedding <=> p_query_embedding))::real
            end as semantic
        from prepared
        where p_query_embedding is not null or prepared.document @@ prepared.query
    )
    select
        scored.id,
        scored.subject,
        scored.kind,
        scored.content,
        scored.tags,
        scored.importance,
        scored.confidence,
        scored.status,
        scored.source,
        scored.source_device_id,
        scored.source_turn_id,
        scored.supersedes_id,
        scored.created_at,
        scored.updated_at,
        scored.last_confirmed_at,
        scored.expires_at,
        scored.lexical as lexical_score,
        scored.semantic as semantic_score,
        (
            (0.30 * scored.lexical)
            + (0.55 * scored.semantic)
            + (0.10 * scored.importance::real / 5.0)
            + (0.05 * scored.confidence::real)
        )::real as hybrid_score
    from scored
    order by hybrid_score desc, scored.updated_at desc
    limit greatest(1, least(coalesce(p_limit, 5), 20));
$$;

revoke all on function public.wearabllm_search_memory(
    text, text, extensions.vector, text, text[], integer
) from public, anon, authenticated;
grant execute on function public.wearabllm_search_memory(
    text, text, extensions.vector, text, text[], integer
) to service_role;

comment on column public.wearabllm_memory_records.embedding is
    'Private 512-dimensional semantic representation generated by the hosted Sphere bridge.';
comment on function public.wearabllm_search_memory(text, text, extensions.vector, text, text[], integer) is
    'Principal-scoped hybrid household-memory search. Raw embeddings are never returned.';
