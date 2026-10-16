import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { RotateCw } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/States";
import { healthQuery, recentQueriesQuery, statsQuery } from "@/lib/api/queries";
import type { ComponentHealth, Health } from "@/lib/api/types";
import { formatMs, formatNumber, timeAgo } from "@/lib/format";
import { cn } from "@/lib/utils";
import { SettingsForm } from "./SettingsForm";

const STATUS_COPY: Record<Health["status"], { label: string; tone: string }> = {
  ok: { label: "All systems nominal", tone: "text-success" },
  degraded: { label: "Running degraded", tone: "text-warning" },
  down: { label: "Service down", tone: "text-destructive" },
};

export function SystemPage() {
  const health = useQuery(healthQuery());
  const stats = useQuery(statsQuery());
  const recent = useQuery(recentQueriesQuery());

  return (
    <div className="space-y-16">
      <header className="flex flex-wrap items-end justify-between gap-4 border-b border-border pb-6">
        <div>
          <p className="label mb-2">
            <span className="text-accent">§</span> System
          </p>
          <h1 className="display text-[44px] sm:text-[56px]">
            {health.data ? (
              <span className={cn("italic", STATUS_COPY[health.data.status].tone)}>
                {STATUS_COPY[health.data.status].label}.
              </span>
            ) : health.isError ? (
              <span className="italic text-destructive">Unreachable.</span>
            ) : (
              "Checking…"
            )}
          </h1>
          {health.data ? <p className="mt-1 label">Version {health.data.version}</p> : null}
        </div>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            void health.refetch();
            void stats.refetch();
            void recent.refetch();
          }}
        >
          <RotateCw strokeWidth={1.5} className={health.isFetching ? "animate-spin" : undefined} />{" "}
          Refresh
        </Button>
      </header>

      <section aria-labelledby="components-h">
        <h2 id="components-h" className="label mb-4">
          Components
        </h2>
        {health.isError ? (
          <ErrorState error={health.error} onRetry={() => void health.refetch()} />
        ) : (
          <div className="grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2 lg:grid-cols-4">
            {health.data
              ? health.data.components.map((c) => <ComponentCard key={c.name} c={c} />)
              : [0, 1, 2, 3].map((i) => (
                  <div key={i} className="bg-background p-5">
                    <Skeleton className="h-6 w-24" />
                    <Skeleton className="mt-3 h-3 w-16" />
                  </div>
                ))}
          </div>
        )}
      </section>

      <section aria-labelledby="stats-h">
        <h2 id="stats-h" className="label mb-4">
          Figures
        </h2>
        {stats.isError ? (
          <ErrorState error={stats.error} onRetry={() => void stats.refetch()} />
        ) : (
          <dl className="grid grid-cols-2 gap-y-8 sm:grid-cols-3 lg:grid-cols-6">
            {(
              [
                ["Documents", stats.data?.documents],
                ["Passages", stats.data?.chunks],
                ["Collections", stats.data?.collections],
                ["Queued jobs", stats.data?.queued_jobs],
                ["Failed jobs", stats.data?.failed_jobs],
                ["Queries · 24h", stats.data?.queries_24h],
              ] as const
            ).map(([label, value]) => (
              <div key={label} className="border-l border-border pl-4">
                <dd
                  className={cn(
                    "display text-[44px] tabular-nums leading-none",
                    label === "Failed jobs" && value ? "text-destructive" : undefined,
                  )}
                >
                  {value === undefined ? (
                    <span className="text-faint">—</span>
                  ) : (
                    formatNumber(value)
                  )}
                </dd>
                <dt className="label mt-2">{label}</dt>
              </div>
            ))}
          </dl>
        )}
      </section>

      <div className="grid grid-cols-[minmax(0,1fr)] gap-16 lg:grid-cols-[minmax(0,1fr)_24rem]">
        <section aria-labelledby="recent-h">
          <h2 id="recent-h" className="label mb-3 border-b border-border pb-2">
            Recent queries
          </h2>
          {recent.isError ? (
            <ErrorState error={recent.error} onRetry={() => void recent.refetch()} />
          ) : recent.isPending ? (
            <div className="space-y-3">
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-4 w-1/2" />
            </div>
          ) : recent.data.length ? (
            <table className="w-full text-left text-[13.5px]">
              <thead className="sr-only">
                <tr>
                  <th>Query</th>
                  <th>Mode</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/70">
                {recent.data.map((r) => (
                  <tr key={`${r.query}-${r.created_at}`}>
                    <td className="py-2.5 pr-4">
                      <Link
                        to="/"
                        search={{ q: r.query }}
                        className="font-serif text-[16.5px] italic hover:text-accent"
                      >
                        {r.query}
                      </Link>
                    </td>
                    <td className="py-2.5 pr-4 font-mono text-[10.5px] uppercase tracking-[0.08em] text-muted-foreground">
                      {r.mode}
                    </td>
                    <td className="py-2.5 text-right font-mono text-[10.5px] whitespace-nowrap text-faint">
                      {timeAgo(r.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="font-serif italic text-muted-foreground">No queries yet.</p>
          )}
        </section>
        <SettingsForm />
      </div>
    </div>
  );
}

const COMPONENT_LABELS: Record<string, string> = {
  db: "Database",
  embedder: "Embedder",
  reranker: "Reranker",
  llm: "Language model",
};

function ComponentCard({ c }: { c: ComponentHealth }) {
  return (
    <div className="bg-background p-5">
      <div className="flex items-center justify-between">
        <span className="display text-[24px]">{COMPONENT_LABELS[c.name] ?? c.name}</span>
        <span
          className={cn(
            "flex items-center gap-1.5 label",
            c.ok ? "text-success" : "text-destructive",
          )}
        >
          <span className={cn("size-1.5 rounded-full", c.ok ? "bg-success" : "bg-destructive")} />
          {c.ok ? "ok" : "down"}
        </span>
      </div>
      <p className="mt-2 font-mono text-[11px] tabular-nums text-muted-foreground">
        {c.latency_ms != null ? formatMs(c.latency_ms) : "—"}
      </p>
      {c.detail ? (
        <p className="mt-1.5 line-clamp-2 text-[12px] text-muted-foreground" title={c.detail}>
          {c.detail}
        </p>
      ) : null}
    </div>
  );
}
