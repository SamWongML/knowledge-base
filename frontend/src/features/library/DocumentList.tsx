import { Link } from "@tanstack/react-router";
import { useWindowVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Skeleton } from "@/components/ui/Skeleton";
import { Spinner } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/States";
import { StatusChip } from "@/components/ui/StatusChip";
import type { DocumentSummary } from "@/lib/api/types";
import { mimeLabel, timeAgo } from "@/lib/format";
import { setUI } from "@/lib/ui-store";

export function DocumentList({
  items,
  hasMore,
  loadingMore,
  onLoadMore,
  onTag,
  filtered,
}: {
  items: DocumentSummary[];
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  onTag: (tag: string) => void;
  filtered: boolean;
}) {
  const listRef = useRef<HTMLUListElement>(null);
  const [offset, setOffset] = useState(0);
  useLayoutEffect(() => {
    setOffset(listRef.current?.offsetTop ?? 0);
  }, []);

  const virtualizer = useWindowVirtualizer({
    count: items.length,
    estimateSize: () => 84,
    overscan: 8,
    scrollMargin: offset,
    getItemKey: (i) => items[i]?.id ?? i,
  });
  const virtualItems = virtualizer.getVirtualItems();
  const lastIndex = virtualItems.at(-1)?.index ?? 0;

  useEffect(() => {
    if (hasMore && !loadingMore && lastIndex >= items.length - 10) onLoadMore();
  }, [hasMore, loadingMore, lastIndex, items.length, onLoadMore]);

  if (!items.length) {
    return filtered ? (
      <EmptyState title="Nothing matches these filters" glyph="∅" />
    ) : (
      <EmptyState
        title="An empty shelf"
        action={
          <button
            type="button"
            className="text-accent underline-offset-4 hover:underline"
            onClick={() => setUI({ newNoteOpen: true })}
          >
            Write the first note →
          </button>
        }
      >
        Drop Markdown, text, PDF or HTML files above, or write a note. Everything is chunked,
        embedded and searchable within seconds.
      </EmptyState>
    );
  }

  return (
    <>
      <ul
        ref={listRef}
        className="relative border-t border-border"
        style={{ height: virtualizer.getTotalSize() }}
        aria-label={`${items.length} documents`}
      >
        {virtualItems.map((v) => {
          const doc = items[v.index];
          if (!doc) return null;
          return (
            <li
              key={v.key}
              data-index={v.index}
              ref={virtualizer.measureElement}
              className="absolute inset-x-0 top-0 border-b border-border"
              style={{ transform: `translateY(${v.start - virtualizer.options.scrollMargin}px)` }}
            >
              <DocumentRow doc={doc} onTag={onTag} />
            </li>
          );
        })}
      </ul>
      {loadingMore ? (
        <div className="flex justify-center py-6 text-muted-foreground">
          <Spinner />
        </div>
      ) : null}
    </>
  );
}

function DocumentRow({ doc, onTag }: { doc: DocumentSummary; onTag: (t: string) => void }) {
  return (
    <div className="group flex items-start gap-4 py-4">
      <div className="min-w-0 flex-1">
        <Link
          to="/documents/$id"
          params={{ id: doc.id }}
          className="display block truncate text-[21px] leading-tight decoration-accent/60 decoration-1 underline-offset-4 group-hover:underline"
        >
          {doc.title || "Untitled"}
        </Link>
        <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1 font-mono text-[11px] text-muted-foreground">
          <span className="uppercase tracking-[0.08em]">{doc.collection}</span>
          <span className="text-faint">·</span>
          <span>{mimeLabel(doc.mime_type)}</span>
          <span className="text-faint">·</span>
          <span className="tabular-nums">{doc.chunk_count} ¶</span>
          <span className="text-faint">·</span>
          <span>{timeAgo(doc.updated_at)}</span>
          {doc.tags.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => onTag(t)}
              className="text-faint hover:text-accent"
            >
              #{t}
            </button>
          ))}
        </div>
        {doc.status === "failed" && doc.error ? (
          <p className="mt-1.5 line-clamp-2 text-[12.5px] text-destructive">{doc.error}</p>
        ) : null}
      </div>
      <StatusChip status={doc.status} className="mt-1.5 shrink-0" />
    </div>
  );
}

export function DocumentListSkeleton() {
  return (
    <div role="status" className="border-t border-border" aria-busy aria-label="Loading documents">
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <div key={i} className="flex items-start justify-between border-b border-border py-4">
          <div className="w-2/3 space-y-2">
            <Skeleton className="h-5 w-3/4" />
            <Skeleton className="h-3 w-1/2" />
          </div>
          <Skeleton className="h-3 w-14" />
        </div>
      ))}
    </div>
  );
}
