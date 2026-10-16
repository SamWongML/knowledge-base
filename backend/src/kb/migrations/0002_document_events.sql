-- Document events v2.
--  * Deletes now emit ``document.deleted`` and metadata edits (title, tags, collection,
--    source, metadata) emit ``document.updated``, so every API process drops cached search
--    results that would otherwise keep serving deleted or renamed documents.
--  * ``title`` and ``error`` are truncated: pg_notify payloads must stay under 8000 bytes,
--    and an over-long payload would make the triggering UPDATE itself fail.

create or replace function notify_document_status() returns trigger language plpgsql as $$
declare
    kind text;
begin
    if tg_op = 'DELETE' then
        perform pg_notify('kb_events', json_build_object(
            'type', 'document.deleted',
            'id', old.id,
            'collection_id', old.collection_id
        )::text);
        return old;
    end if;
    if tg_op = 'INSERT' or new.status is distinct from old.status then
        kind := 'document.status';
    elsif (new.title, new.tags, new.collection_id, new.source_uri, new.metadata)
          is distinct from (old.title, old.tags, old.collection_id, old.source_uri, old.metadata)
    then
        kind := 'document.updated';
    else
        return new;
    end if;
    perform pg_notify('kb_events', json_build_object(
        'type', kind,
        'id', new.id,
        'collection_id', new.collection_id,
        'status', new.status,
        'title', left(new.title, 200),
        'error', left(new.error, 1000),
        'chunk_count', new.chunk_count
    )::text);
    return new;
end $$;

drop trigger if exists documents_notify on documents;
create trigger documents_notify after insert or update or delete on documents
    for each row execute function notify_document_status();
