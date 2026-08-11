alter table public.wearabllm_conversation_turns
    add column if not exists metadata jsonb not null default '{}'::jsonb;

alter table public.wearabllm_conversation_archive
    add column if not exists metadata jsonb not null default '{}'::jsonb;

alter table public.wearabllm_conversation_turns
    drop constraint if exists wearabllm_conversation_turns_metadata_object_check;
alter table public.wearabllm_conversation_turns
    add constraint wearabllm_conversation_turns_metadata_object_check
    check (jsonb_typeof(metadata) = 'object');

alter table public.wearabllm_conversation_archive
    drop constraint if exists wearabllm_conversation_archive_metadata_object_check;
alter table public.wearabllm_conversation_archive
    add constraint wearabllm_conversation_archive_metadata_object_check
    check (jsonb_typeof(metadata) = 'object');

comment on column public.wearabllm_conversation_turns.metadata is
    'Non-spoken turn metadata such as public web citations and redacted tool audit results.';

comment on column public.wearabllm_conversation_archive.metadata is
    'Archived non-spoken turn metadata such as public web citations and redacted tool audit results.';
