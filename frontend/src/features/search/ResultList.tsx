import { useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/States";
import { useHotkeys } from "@/hooks/useHotkeys";
import type { SearchHit } from "@/lib/api/types";
import { ResultItem } from "./ResultItem";
import { stageMaxima } from "./scores";

export function ResultList({
  hits,
  query,
  inspector,
}: {
  hits: SearchHit[];
  query: string;
  inspector: boolean;
}) {
  const [active, setActive] = useState(-1);
  const navigate = useNavigate();
  const refs = useRef<(HTMLElement | null)[]>([]);
  const maxima = useMemo(() => stageMaxima(hits), [hits]);

  // Reset the cursor whenever the result set changes.
  useEffect(() => {
    void hits;
    setActive(-1);
  }, [hits]);

  const move = (delta: number) => {
    if (!hits.length) return;
    setActive((i) => {
      const next = Math.max(0, Math.min(hits.length - 1, (i < 0 && delta < 0 ? 0 : i) + delta));
      refs.current[next]?.focus({ preventScroll: true });
      refs.current[next]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      return next;
    });
  };
  const open = (hit: SearchHit) =>
    void navigate({
      to: "/documents/$id",
      params: { id: hit.document_id },
      hash: `chunk-${hit.chunk_id}`,
    });

  useHotkeys([
    { combo: "j", handler: () => move(1) },
    {
      combo: "arrowdown",
      allowInInputs: true,
      handler: (e) => {
        const fromSearch = e.target instanceof HTMLInputElement && e.target.type === "search";
        if (active < 0 && !fromSearch) return;
        if (fromSearch) setActive(-1);
        e.preventDefault();
        move(1);
      },
    },
    { combo: "k", handler: () => move(-1) },
    {
      combo: "arrowup",
      handler: (e) => {
        if (active < 0) return;
        e.preventDefault();
        move(-1);
      },
    },
    {
      combo: "enter",
      handler: (e) => {
        const hit = hits[active];
        if (hit && !(e.target instanceof HTMLAnchorElement)) open(hit);
      },
    },
  ]);

  if (!hits.length) {
    return (
      <EmptyState title="No passages found" glyph="∅">
        Nothing in the commonplace matches “{query}”. Try semantic mode, fewer filters, or different
        words.
      </EmptyState>
    );
  }

  return (
    <ol
      className="divide-y divide-border border-t border-border"
      aria-label={`${hits.length} results`}
    >
      {hits.map((hit, i) => (
        <ResultItem
          key={hit.chunk_id}
          ref={(el) => {
            refs.current[i] = el;
          }}
          hit={hit}
          index={i}
          active={i === active}
          inspector={inspector}
          maxima={maxima}
          onFocus={() => setActive(i)}
        />
      ))}
    </ol>
  );
}

export function ResultsSkeleton() {
  return (
    <div
      role="status"
      className="divide-y divide-border border-t border-border"
      aria-busy
      aria-label="Loading results"
    >
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="grid grid-cols-[2.5rem_1fr] gap-4 py-6">
          <Skeleton className="h-3 w-5" />
          <div className="space-y-2.5">
            <Skeleton className="h-6 w-1/2" />
            <Skeleton className="h-3 w-1/3" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-4/5" />
          </div>
        </div>
      ))}
    </div>
  );
}
