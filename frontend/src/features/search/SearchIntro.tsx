import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Skeleton } from "@/components/ui/Skeleton";
import { recentQueriesQuery, statsQuery } from "@/lib/api/queries";
import { formatNumber, timeAgo } from "@/lib/format";

/** The empty-state of search: a few figures and the recent-query index. */
export function SearchIntro() {
  const stats = useQuery(statsQuery());
  const recent = useQuery(recentQueriesQuery());
  const items = (recent.data ?? []).filter(
    (r, i, all) => all.findIndex((x) => x.query === r.query) === i,
  );

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-12 pt-4 md:grid-cols-[minmax(0,1fr)_15rem]">
      <section aria-labelledby="recent-h">
        <h2 id="recent-h" className="label mb-3 border-b border-border pb-2">
          Recently looked up
        </h2>
        {recent.isPending ? (
          <div className="space-y-3">
            <Skeleton className="h-5 w-2/3" />
            <Skeleton className="h-5 w-1/2" />
            <Skeleton className="h-5 w-3/5" />
          </div>
        ) : items.length ? (
          <ul className="divide-y divide-border/70">
            {items.slice(0, 8).map((r) => (
              <li key={`${r.query}-${r.created_at}`}>
                <Link
                  to="/"
                  search={{ q: r.query }}
                  className="group flex items-baseline justify-between gap-4 py-2.5"
                >
                  <span className="truncate font-serif text-[18px] italic text-foreground/85 group-hover:text-accent">
                    {r.query}
                  </span>
                  <span className="shrink-0 font-mono text-[10.5px] text-faint">
                    {timeAgo(r.created_at)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="font-serif text-[17px] italic text-muted-foreground">
            Nothing yet. Type above — results appear as you write.
          </p>
        )}
      </section>
      <aside aria-label="Collection figures" className="space-y-5">
        <Figure label="Documents" value={stats.data?.documents} />
        <Figure label="Passages indexed" value={stats.data?.chunks} />
        <Figure label="Queries today" value={stats.data?.queries_24h} />
      </aside>
    </div>
  );
}

function Figure({ label, value }: { label: string; value: number | undefined }) {
  return (
    <div className="border-l border-border pl-4">
      <div className="display text-[44px] tabular-nums leading-none">
        {value === undefined ? <span className="text-faint">—</span> : formatNumber(value)}
      </div>
      <div className="label mt-1.5">{label}</div>
    </div>
  );
}
