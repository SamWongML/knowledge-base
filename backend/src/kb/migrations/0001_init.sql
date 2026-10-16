-- Core schema. Requires PostgreSQL 18 (native uuidv7()), pgvector >= 0.8 and ParadeDB pg_search.
-- {{EMBEDDING_DIM}} is substituted by the migration runner from KB_EMBEDDING_DIM.

create extension if not exists vector;
create extension if not exists pg_search;

-- Collections ("spaces") group documents; every document belongs to exactly one.
create table collections (
    id          uuid primary key default uuidv7(),
    slug        text not null unique check (slug ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
    name        text not null,
    description text not null default '',
    created_at  timestamptz not null default now()
);

insert into collections (slug, name, description)
values ('default', 'Default', 'Everything that is not filed elsewhere');

create table documents (
    id            uuid primary key default uuidv7(),
    collection_id uuid not null references collections (id) on delete cascade,
    title         text not null,
    source_uri    text,                       -- URL, file path or agent-provided origin
    mime_type     text not null default 'text/markdown',
    content_hash  text not null,              -- sha256 of normalized text; drives dedup
    body          text not null default '',   -- normalized Markdown of the whole document
    metadata      jsonb not null default '{}'::jsonb,
    tags          text[] not null default '{}',
    status        text not null default 'pending'
                  check (status in ('pending', 'processing', 'ready', 'failed')),
    error         text,
    chunk_count   integer not null default 0,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    unique (collection_id, content_hash)
);
create index documents_collection_idx on documents (collection_id, updated_at desc);
create index documents_status_idx on documents (status) where status <> 'ready';
create index documents_tags_idx on documents using gin (tags);
create unique index documents_source_uri_idx on documents (collection_id, source_uri)
    where source_uri is not null;

create table chunks (
    id            bigint generated always as identity primary key,
    document_id   uuid not null references documents (id) on delete cascade,
    collection_id uuid not null,               -- denormalized for filter push-down
    ord           integer not null,            -- position in document
    heading_path  text not null default '',    -- "Guide > Install > macOS"
    content       text not null,               -- chunk text as shown to readers
    context       text not null default '',    -- situating context prepended for retrieval
    token_count   integer not null default 0,
    char_start    integer not null default 0,
    char_end      integer not null default 0,
    embedding     halfvec({{EMBEDDING_DIM}}),
    unique (document_id, ord)
);
create index chunks_document_idx on chunks (document_id, ord);

-- Dense ANN index (cosine). halfvec halves memory with negligible recall loss.
create index chunks_embedding_hnsw on chunks
    using hnsw (embedding halfvec_cosine_ops) with (m = 16, ef_construction = 128);

-- Lexical BM25 index (ParadeDB / Tantivy) with English stemming.
create index chunks_bm25 on chunks
    using bm25 (id, content, context, heading_path, collection_id, document_id)
    with (text_fields = '{
        "content":      {"tokenizer": {"type": "default", "stemmer": "English"}},
        "context":      {"tokenizer": {"type": "default", "stemmer": "English"}},
        "heading_path": {"tokenizer": {"type": "default", "stemmer": "English"}}
    }');

-- Durable job queue (SELECT ... FOR UPDATE SKIP LOCKED). Survives restarts; leases expire.
create table jobs (
    id           bigint generated always as identity primary key,
    kind         text not null,
    payload      jsonb not null default '{}'::jsonb,
    status       text not null default 'queued'
                 check (status in ('queued', 'running', 'done', 'failed')),
    priority     smallint not null default 0,
    attempts     integer not null default 0,
    max_attempts integer not null default 5,
    run_after    timestamptz not null default now(),
    locked_by    text,
    lease_until  timestamptz,
    last_error   text,
    dedup_key    text,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);
create index jobs_ready_idx on jobs (priority desc, run_after, id) where status = 'queued';
create index jobs_lease_idx on jobs (lease_until) where status = 'running';
create unique index jobs_dedup_idx on jobs (dedup_key)
    where dedup_key is not null and status in ('queued', 'running');

-- Query log: powers analytics, "recent searches" and offline evaluation.
create table query_log (
    id          bigint generated always as identity primary key,
    query       text not null,
    mode        text not null,
    filters     jsonb not null default '{}'::jsonb,
    latency_ms  integer not null,
    result_ids  bigint[] not null default '{}',
    created_at  timestamptz not null default now()
);
create index query_log_created_idx on query_log (created_at desc);

create or replace function touch_updated_at() returns trigger language plpgsql as $$
begin
    new.updated_at := now();
    return new;
end $$;

create trigger documents_touch before update on documents
    for each row execute function touch_updated_at();
create trigger jobs_touch before update on jobs
    for each row execute function touch_updated_at();

-- Fan out lifecycle events to API processes (SSE) via LISTEN/NOTIFY.
create or replace function notify_document_status() returns trigger language plpgsql as $$
begin
    if tg_op = 'INSERT' or new.status is distinct from old.status then
        perform pg_notify('kb_events', json_build_object(
            'type', 'document.status',
            'id', new.id,
            'collection_id', new.collection_id,
            'status', new.status,
            'title', new.title,
            'error', new.error,
            'chunk_count', new.chunk_count
        )::text);
    end if;
    return new;
end $$;

create trigger documents_notify after insert or update on documents
    for each row execute function notify_document_status();
