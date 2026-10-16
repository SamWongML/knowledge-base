import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useParams } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Markdown } from "@/components/markdown/Markdown";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState, ErrorState } from "@/components/ui/States";
import { StatusChip } from "@/components/ui/StatusChip";
import { ApiError } from "@/lib/api/client";
import { chunksQuery, documentQuery } from "@/lib/api/queries";
import { formatDate, mimeLabel } from "@/lib/format";
import { cn } from "@/lib/utils";
import { ChunkOutline } from "./ChunkOutline";
import { DocumentActions } from "./DocumentActions";
import { MetadataPanel } from "./MetadataPanel";
import { segmentBody } from "./segments";
import { TagEditor } from "./TagEditor";

export function ReaderPage() {
  const { id } = useParams({ from: "/documents/$id" });
  const hash = useLocation({ select: (l) => l.hash });
  const doc = useQuery(documentQuery(id));
  const chunks = useQuery({
    ...chunksQuery(id),
    enabled: doc.data?.status === "ready" || (doc.data?.chunk_count ?? 0) > 0,
  });

  const targetChunk = useMemo(() => {
    const m = /^chunk-(\d+)$/.exec(hash ?? "");
    return m ? Number(m[1]) : null;
  }, [hash]);

  const segmentation = useMemo(
    () => (doc.data ? segmentBody(doc.data.body, chunks.data ?? []) : null),
    [doc.data, chunks.data],
  );
  const [visible, setVisible] = useState<number | null>(null);

  // Scroll to and highlight the linked chunk once its segment is in the DOM.
  useEffect(() => {
    if (targetChunk === null || !segmentation || !chunks.data) return;
    const el = document.getElementById(`chunk-${targetChunk}`);
    if (!el) return;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    requestAnimationFrame(() =>
      el.scrollIntoView({ block: "center", behavior: reduce ? "auto" : "smooth" }),
    );
  }, [targetChunk, segmentation, chunks.data]);

  // Scroll-spy for the outline.
  useEffect(() => {
    if (!segmentation || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(
      (entries) => {
        const top = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        const first = top?.target.getAttribute("data-first-chunk");
        if (first) setVisible(Number(first));
      },
      { rootMargin: "-20% 0px -60% 0px" },
    );
    for (const el of document.querySelectorAll("[data-first-chunk]")) io.observe(el);
    return () => io.disconnect();
  }, [segmentation]);

  if (doc.isError) {
    if (doc.error instanceof ApiError && doc.error.status === 404) {
      return (
        <EmptyState
          glyph="¶"
          title="This document is gone"
          action={
            <Link to="/library" className="text-accent hover:underline">
              Back to the library →
            </Link>
          }
        >
          It may have been deleted.
        </EmptyState>
      );
    }
    return <ErrorState error={doc.error} onRetry={() => void doc.refetch()} />;
  }
  if (!doc.data || !segmentation) return <ReaderSkeleton />;

  const d = doc.data;
  const unmatchedTarget =
    targetChunk !== null && segmentation.unmatched.includes(targetChunk)
      ? chunks.data?.find((c) => c.id === targetChunk)
      : undefined;

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-x-14 gap-y-10 lg:grid-cols-[minmax(0,1fr)_17rem]">
      <article className="min-w-0">
        <Link
          to="/library"
          search={{ collection: d.collection }}
          className="label mb-4 inline-flex items-center gap-1.5 hover:text-foreground"
        >
          <ArrowLeft className="size-3" strokeWidth={1.75} /> {d.collection}
        </Link>
        <h1 className="display max-w-[22ch] text-[42px] text-balance sm:text-[56px]">
          {d.title || "Untitled"}
        </h1>
        <div className="mt-3 mb-10 flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border pb-5 font-mono text-[11px] text-muted-foreground">
          <StatusChip status={d.status} />
          <span className="text-faint">·</span>
          <span>{mimeLabel(d.mime_type)}</span>
          <span className="text-faint">·</span>
          <span>{d.chunk_count} passages</span>
          <span className="text-faint">·</span>
          <span>updated {formatDate(d.updated_at)}</span>
        </div>

        {d.status === "failed" && d.error ? (
          <p
            role="alert"
            className="mb-8 border-l-2 border-destructive pl-4 text-[13.5px] text-destructive"
          >
            Ingestion failed: {d.error}
          </p>
        ) : null}

        {unmatchedTarget ? (
          <aside
            className="mb-10 max-w-[68ch] animate-arrive rounded-md border-l-2 border-accent px-5 py-4"
            id={`chunk-${unmatchedTarget.id}`}
          >
            <p className="label mb-2">Cited passage</p>
            <Markdown compact>{unmatchedTarget.content}</Markdown>
          </aside>
        ) : null}

        {d.body.trim() ? (
          <div>
            {segmentation.segments.map((seg) => {
              const first = seg.chunkIds[0];
              const hit = targetChunk !== null && seg.chunkIds.includes(targetChunk);
              return (
                <section
                  key={`${seg.start}-${first ?? "x"}`}
                  id={first !== undefined ? `chunk-${first}` : undefined}
                  data-first-chunk={first}
                  className={cn(
                    "relative -mx-4 scroll-mt-28 rounded-md px-4 py-0.5 sm:-mx-6 sm:px-6",
                    hit && "animate-arrive",
                  )}
                >
                  {hit ? (
                    <span
                      aria-hidden
                      className="absolute top-2 bottom-2 left-0 w-[2px] rounded-full bg-accent"
                    />
                  ) : null}
                  {seg.chunkIds.slice(1).map((cid) => (
                    <span key={cid} id={`chunk-${cid}`} className="block scroll-mt-28" />
                  ))}
                  <Markdown>{seg.text}</Markdown>
                </section>
              );
            })}
          </div>
        ) : (
          <EmptyState title="No text extracted" glyph="∅" />
        )}
      </article>

      <aside
        className="space-y-8 lg:sticky lg:top-20 lg:max-h-[calc(100dvh-6rem)] lg:self-start lg:overflow-y-auto scrollbar-thin"
        aria-label="Document details"
      >
        <ChunkOutline
          documentId={d.id}
          chunks={chunks.data}
          loading={chunks.isPending && chunks.fetchStatus !== "idle"}
          active={targetChunk ?? visible}
        />
        <TagEditor doc={d} />
        <MetadataPanel doc={d} />
        <DocumentActions doc={d} />
      </aside>
    </div>
  );
}

function ReaderSkeleton() {
  return (
    <div role="status" className="max-w-[68ch] space-y-4" aria-busy aria-label="Loading document">
      <Skeleton className="h-3 w-20" />
      <Skeleton className="h-14 w-4/5" />
      <Skeleton className="h-3 w-1/2" />
      <div className="space-y-2.5 pt-8">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <Skeleton key={i} className="h-4" />
        ))}
      </div>
    </div>
  );
}
