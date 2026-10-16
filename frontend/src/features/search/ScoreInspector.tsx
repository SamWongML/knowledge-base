import type { SearchHit } from "@/lib/api/types";
import { cn } from "@/lib/utils";
import { stageEntries } from "./scores";

/** Per-stage score bars, each scaled to the best hit for that stage. */
export function ScoreInspector({
  scores,
  maxima,
  className,
}: {
  scores: SearchHit["scores"];
  maxima: Record<string, number>;
  className?: string;
}) {
  const entries = stageEntries(scores);
  if (!entries.length) {
    return <p className={cn("label", className)}>No per-stage scores reported.</p>;
  }
  return (
    <dl
      className={cn(
        "grid grid-cols-[4.5rem_1fr_4rem] items-center gap-x-3 gap-y-1.5 rounded-md border border-dashed border-border bg-surface/50 px-3 py-2.5",
        className,
      )}
    >
      {entries.map((e) => {
        const max = maxima[e.key] || 1;
        const pct = Math.max(2, Math.min(100, (Math.abs(e.value) / max) * 100));
        return (
          <div key={e.key} className="contents">
            <dt className="label">{e.label}</dt>
            <dd className="h-[5px] overflow-hidden rounded-full bg-border/70" aria-hidden>
              <div
                className={cn(
                  "h-full rounded-full",
                  e.key === "rerank" || e.key === "llm" ? "bg-accent" : "bg-foreground/55",
                )}
                style={{ width: `${pct}%` }}
              />
            </dd>
            <dd className="text-right font-mono text-[11px] tabular-nums text-muted-foreground">
              {e.value.toFixed(e.value !== 0 && Math.abs(e.value) < 0.1 ? 4 : 3)}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
