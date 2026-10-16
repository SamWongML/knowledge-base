import { PreviewCard } from "@base-ui/react/preview-card";
import { Link } from "@tanstack/react-router";
import { ArrowUpRight } from "lucide-react";
import { createContext, useContext } from "react";
import { Snippet } from "@/features/search/Snippet";
import type { Citation } from "@/lib/api/types";
import { splitHeadingPath } from "@/lib/format";
import { cn } from "@/lib/utils";

export const CitationContext = createContext<ReadonlyMap<number, Citation>>(new Map());

const markerClass =
  "mx-[1px] inline-flex min-w-[1.35em] -translate-y-[0.35em] items-center justify-center rounded-[3px] px-[3px] font-mono text-[0.62em] leading-[1.5] tabular-nums no-underline align-baseline transition-colors";

export function CitationMarker({ n }: { n: number }) {
  const citations = useContext(CitationContext);
  const c = citations.get(n);
  if (!c) {
    return (
      <span className={cn(markerClass, "bg-surface text-muted-foreground")} title={`Source ${n}`}>
        {n}
      </span>
    );
  }
  return (
    <PreviewCard.Root>
      <PreviewCard.Trigger
        delay={150}
        closeDelay={120}
        render={
          <Link
            to="/documents/$id"
            params={{ id: c.document_id }}
            hash={`chunk-${c.chunk_id}`}
            data-cite=""
            aria-label={`Source ${n}: ${c.document_title}`}
            className={cn(
              markerClass,
              "bg-accent-soft text-accent hover:bg-accent hover:text-accent-foreground",
            )}
          />
        }
      >
        {n}
      </PreviewCard.Trigger>
      <PreviewCard.Portal>
        <PreviewCard.Positioner sideOffset={8} className="z-50">
          <PreviewCard.Popup className="shadow-paper w-[min(380px,calc(100vw-2rem))] rounded-lg border border-border bg-surface-raised p-4 transition-[opacity,transform] duration-150 data-[ending-style]:opacity-0 data-[starting-style]:translate-y-1 data-[starting-style]:opacity-0">
            <CitationCardBody citation={c} />
          </PreviewCard.Popup>
        </PreviewCard.Positioner>
      </PreviewCard.Portal>
    </PreviewCard.Root>
  );
}

export function CitationCardBody({ citation: c }: { citation: Citation }) {
  const crumbs = splitHeadingPath(c.heading_path);
  return (
    <div>
      <div className="label flex items-center gap-2">
        <span className="text-accent">[{c.n}]</span>
        <span className="truncate">{crumbs.join(" › ") || "Passage"}</span>
      </div>
      <p className="display mt-1.5 text-xl leading-tight">{c.document_title}</p>
      <p className="mt-2 line-clamp-5 font-serif text-[15px] leading-snug text-muted-foreground">
        <Snippet html={c.snippet} />
      </p>
      <Link
        to="/documents/$id"
        params={{ id: c.document_id }}
        hash={`chunk-${c.chunk_id}`}
        className="mt-3 inline-flex items-center gap-1 text-[12.5px] font-medium text-accent hover:underline"
      >
        Open in reader <ArrowUpRight className="size-3.5" strokeWidth={1.75} />
      </Link>
    </div>
  );
}
