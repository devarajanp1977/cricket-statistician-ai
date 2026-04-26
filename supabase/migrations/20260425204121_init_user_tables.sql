-- Cricket Statistician AI: per-user data tables.
-- Cricket statistical data lives in DuckDB on the backend VM; Supabase only
-- holds auth + user-scoped artefacts (chats, bookmarks, preferences).

-- ============================================================================
-- chat_history: one row per turn in a user's conversation.
-- ============================================================================
create table if not exists public.chat_history (
    id           uuid        primary key default gen_random_uuid(),
    user_id      uuid        not null references auth.users (id) on delete cascade,
    session_id   uuid        not null,
    role         text        not null check (role in ('user', 'assistant', 'system')),
    content      text        not null,
    metadata     jsonb       not null default '{}'::jsonb,
    created_at   timestamptz not null default now()
);

create index if not exists chat_history_user_session_idx
    on public.chat_history (user_id, session_id, created_at);

alter table public.chat_history enable row level security;

create policy "chat_history_select_own"
    on public.chat_history for select
    using (auth.uid() = user_id);

create policy "chat_history_insert_own"
    on public.chat_history for insert
    with check (auth.uid() = user_id);

create policy "chat_history_delete_own"
    on public.chat_history for delete
    using (auth.uid() = user_id);

-- ============================================================================
-- bookmarks: saved queries/answers a user wants to keep.
-- ============================================================================
create table if not exists public.bookmarks (
    id          uuid        primary key default gen_random_uuid(),
    user_id     uuid        not null references auth.users (id) on delete cascade,
    title       text        not null,
    query       text        not null,
    answer      text,
    tags        text[]      not null default '{}',
    created_at  timestamptz not null default now()
);

create index if not exists bookmarks_user_idx
    on public.bookmarks (user_id, created_at desc);

alter table public.bookmarks enable row level security;

create policy "bookmarks_select_own"
    on public.bookmarks for select using (auth.uid() = user_id);
create policy "bookmarks_insert_own"
    on public.bookmarks for insert with check (auth.uid() = user_id);
create policy "bookmarks_update_own"
    on public.bookmarks for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "bookmarks_delete_own"
    on public.bookmarks for delete using (auth.uid() = user_id);

-- ============================================================================
-- user_preferences: per-user app settings (favourite team, theme, etc).
-- ============================================================================
create table if not exists public.user_preferences (
    user_id        uuid        primary key references auth.users (id) on delete cascade,
    favourite_team text,
    theme          text not null default 'system' check (theme in ('system', 'light', 'dark')),
    settings       jsonb not null default '{}'::jsonb,
    updated_at     timestamptz not null default now()
);

alter table public.user_preferences enable row level security;

create policy "user_preferences_select_own"
    on public.user_preferences for select using (auth.uid() = user_id);
create policy "user_preferences_upsert_own"
    on public.user_preferences for insert with check (auth.uid() = user_id);
create policy "user_preferences_update_own"
    on public.user_preferences for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
