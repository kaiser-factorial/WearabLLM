-- User-facing conversation names are independent of consolidation summaries.

alter table public.wearabllm_conversation_sessions
    add column if not exists title text
    check (title is null or char_length(title) between 1 and 120);

comment on column public.wearabllm_conversation_sessions.title is
    'Optional user-authored title shown in the Sphere dashboard and mobile conversation lists.';
