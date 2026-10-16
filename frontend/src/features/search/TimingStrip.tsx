import { AlertTriangle } from "lucide-react";
import { Tooltip } from "@/components/ui/Tooltip";
import type { SearchResponse } from "@/lib/api/types";
import { formatMs } from "@/lib/format";

/** "12 passages · hybrid · 84 ms" plus a proportional per-stage timing bar. */
export function TimingStrip({ response }: { response: SearchResponse }) {
  const total = Math.max(response.total_ms, 1);
  return (
    <div className="mb-4 space-y-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 label">
        <span className="text-foreground">
          {response.hits.length} passage{response.hits.length === 1 ? "" : "s"}
        </span>
        <span className="text-faint">·</span>
        <span>{response.mode}</span>
        <span className="text-faint">·</span>
        <span className="tabular-nums">{formatMs(response.total_ms)}</span>
        {response.degraded.length > 0 && (
          <Tooltip content={`Skipped: ${response.degraded.join(", ")} (model unavailable)`}>
            <button
              type="button"
              className="inline-flex cursor-default items-center gap-1 rounded-full border border-warning/50 bg-warning/10 px-2 py-[1px] text-warning"
            >
              <AlertTriangle className="size-3" strokeWidth={2} />
              Degraded · {response.degraded.join(", ")}
            </button>
          </Tooltip>
        )}
      </div>
      {response.timings.length > 0 && (
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          <div
            className="flex h-[3px] w-full overflow-hidden rounded-full bg-border/60"
            aria-hidden
          >
            {response.timings.map((t, i) => (
              <div
                // biome-ignore lint/suspicious/noArrayIndexKey: stages may repeat; order is stable
                key={`${t.stage}-${i}`}
                style={{ width: `${(t.ms / total) * 100}%`, opacity: 1 - (i % 4) * 0.18 }}
                className="h-full bg-foreground/60 first:rounded-l-full"
              />
            ))}
          </div>
          {response.timings.map((t, i) => (
            <span
              // biome-ignore lint/suspicious/noArrayIndexKey: stages may repeat; order is stable
              key={`${t.stage}-${i}`}
              className="font-mono text-[10.5px] text-faint"
              title={t.detail ?? undefined}
            >
              {t.stage} <span className="tabular-nums text-muted-foreground">{formatMs(t.ms)}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
