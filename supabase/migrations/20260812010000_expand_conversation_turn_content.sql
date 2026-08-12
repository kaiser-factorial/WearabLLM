-- Keep durable conversation storage aligned with the bridge's bounded model
-- output and request sizes. The previous 4,000-character checks could reject
-- an otherwise valid long assistant reply, causing the atomic user/assistant
-- insert to fail as a pair.

alter table public.wearabllm_conversation_turns
    drop constraint if exists wearabllm_conversation_turns_content_check;
alter table public.wearabllm_conversation_turns
    add constraint wearabllm_conversation_turns_content_check
    check (char_length(content) between 1 and 65536);

alter table public.wearabllm_conversation_archive
    drop constraint if exists wearabllm_conversation_archive_content_check;
alter table public.wearabllm_conversation_archive
    add constraint wearabllm_conversation_archive_content_check
    check (char_length(content) between 1 and 65536);

comment on column public.wearabllm_conversation_turns.content is
    'Private conversation text, bounded to 65,536 characters per user or assistant turn.';

comment on column public.wearabllm_conversation_archive.content is
    'Archived private conversation text, bounded to 65,536 characters per turn.';
