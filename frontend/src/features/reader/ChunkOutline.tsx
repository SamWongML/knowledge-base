import { Link } from "@tanstack/react-router";
import { Skeleton } from "@/components/ui/Skeleton";
import type { Chunk } from "@/lib/api/types";
import { pad2, splitHeadingPath } from "@/lib/format";
import { cn } from "@/lib/utils";

export function ChunkOutline({
  documentId,
  chunks,
  loading,
  active,
}: {
  documentId: string;
  chunks: Chunk[] | undefined;
  loading: boolean;
  active: number | null;
}) {
  return (
    <nav aria-label="Passages">
      <h2 className="label mb-2 flex justify-between border-b border-border pb-2">
        <span>Passages</span>
        {chunks ? <span className="tabular-nums">{chunks.length}</span> : null}
      </h2>
      {loading ? (
        <div className="space-y-2">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-5/6" />
        </div>
      ) : !chunks?.length ? (
        <p className="text-[12.5px] text-muted-foreground">
          Passages appear once the document is indexed.
        </p>
      ) : (
        <ol className="scrollbar-thin max-h-[40vh] space-y-px overflow-y-auto pr-1">
          {chunks.map((c) => {
            const crumbs = splitHeadingPath(c.heading_path);
            const isActive = active === c.id;
            return (
              <li key={c.id}>
                <Link
                  to="/documents/$id"
                  params={{ id: documentId }}
                  hash={`chunk-${c.id}`}
                  replace
                  aria-current={isActive ? "location" : undefined}
                  className={cn(
                    "grid grid-cols-[1.6rem_1fr_auto] items-baseline gap-1.5 rounded-sm px-1.5 py-1 text-[12.5px] transition-colors",
                    isActive
                      ? "bg-accent-soft text-foreground"
                      : "text-muted-foreground hover:bg-surface hover:text-foreground",
                  )}
                >
                  <span
                    className={cn(
                      "font-mono text-[10px] tabular-nums",
                      isActive ? "text-accent" : "text-faint",
                    )}
                  >
                    {pad2(c.ord + 1)}
                  </span>
                  <span className="truncate">{crumbs.at(-1) ?? c.content.slice(0, 48)}</span>
                  <span className="font-mono text-[10px] tabular-nums text-faint">
                    {c.token_count}
                  </span>
                </Link>
              </li>
            );
          })}
        </ol>
      )}
    </nav>
  );
}
