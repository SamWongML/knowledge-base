import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { FilePlus2, Upload } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Segmented } from "@/components/ui/Segmented";
import { ErrorState } from "@/components/ui/States";
import { Tag } from "@/components/ui/StatusChip";
import { useDebounced } from "@/hooks/useDebounced";
import { collectionsQuery, documentListQuery } from "@/lib/api/queries";
import type { DocumentStatus } from "@/lib/api/types";
import { formatNumber } from "@/lib/format";
import { setUI } from "@/lib/ui-store";
import { CollectionsPanel } from "./CollectionsPanel";
import { DocumentList, DocumentListSkeleton } from "./DocumentList";
import { UploadZone } from "./UploadZone";

type StatusFilter = DocumentStatus | "all";
const STATUS_OPTIONS = [
  { value: "all", label: "All" },
  { value: "ready", label: "Ready" },
  { value: "processing", label: "Indexing" },
  { value: "pending", label: "Pending" },
  { value: "failed", label: "Failed" },
] as const satisfies readonly { value: StatusFilter; label: string }[];

export function LibraryPage() {
  const params = useSearch({ from: "/library" });
  const navigate = useNavigate({ from: "/library" });
  const [titleDraft, setTitleDraft] = useState(params.q ?? "");
  const q = useDebounced(titleDraft.trim(), 250) || undefined;

  const filters = { collection: params.collection, status: params.status, tag: params.tag, q };
  const list = useInfiniteQuery(documentListQuery(filters));
  const cols = useQuery(collectionsQuery());
  const items = list.data?.pages.flatMap((p) => p.items) ?? [];
  const total = list.data?.pages[0]?.total ?? 0;
  const current = cols.data?.find((c) => c.slug === params.collection);

  return (
    <div>
      <header className="mb-8 flex flex-wrap items-end justify-between gap-4 border-b border-border pb-6">
        <div>
          <p className="label mb-2">
            <span className="text-accent">§</span> Library
          </p>
          <h1 className="display text-[44px] sm:text-[56px]">{current?.name ?? "Everything"}</h1>
          <p className="mt-1 font-serif text-[16px] italic text-muted-foreground">
            {current?.description ||
              `${formatNumber(total)} document${total === 1 ? "" : "s"}${params.collection ? "" : " across all collections"}`}
          </p>
        </div>
        <div className="flex gap-2">
          <Button onClick={() => setUI({ uploadRequested: true })}>
            <Upload strokeWidth={1.5} /> Upload
          </Button>
          <Button variant="primary" onClick={() => setUI({ newNoteOpen: true })}>
            <FilePlus2 strokeWidth={1.5} /> New note
          </Button>
        </div>
      </header>

      <div className="grid grid-cols-[minmax(0,1fr)] gap-10 lg:grid-cols-[14rem_minmax(0,1fr)]">
        <CollectionsPanel
          active={params.collection}
          onSelect={(slug) => void navigate({ search: (s) => ({ ...s, collection: slug }) })}
        />

        <section aria-label="Documents" className="min-w-0">
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <input
              type="search"
              value={titleDraft}
              onChange={(e) => {
                setTitleDraft(e.target.value);
              }}
              onBlur={() =>
                void navigate({
                  search: (s) => ({ ...s, q: titleDraft.trim() || undefined }),
                  replace: true,
                })
              }
              placeholder="Filter by title…"
              aria-label="Filter by title"
              className="h-8 w-full min-w-0 border-b sm:w-auto sm:flex-1 border-border bg-transparent font-serif text-[17px] placeholder:text-faint focus:border-foreground focus:outline-none sm:max-w-xs"
            />
            <Segmented
              label="Status"
              value={params.status ?? "all"}
              onChange={(v) =>
                void navigate({
                  search: (s) => ({ ...s, status: v === "all" ? undefined : v }),
                  replace: true,
                })
              }
              options={STATUS_OPTIONS}
              className="scrollbar-thin max-w-full overflow-x-auto"
            />
            {params.tag ? (
              <Tag onRemove={() => void navigate({ search: (s) => ({ ...s, tag: undefined }) })}>
                {params.tag}
              </Tag>
            ) : null}
          </div>

          <UploadZone defaultCollection={params.collection ?? "default"} />

          {list.isError ? (
            <ErrorState error={list.error} onRetry={() => void list.refetch()} />
          ) : list.isPending ? (
            <DocumentListSkeleton />
          ) : (
            <DocumentList
              items={items}
              hasMore={!!list.hasNextPage}
              loadingMore={list.isFetchingNextPage}
              onLoadMore={() => void list.fetchNextPage()}
              onTag={(tag) => void navigate({ search: (s) => ({ ...s, tag }) })}
              filtered={!!(params.collection || params.status || params.tag || q)}
            />
          )}
        </section>
      </div>
    </div>
  );
}
