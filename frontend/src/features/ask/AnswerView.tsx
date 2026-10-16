import { Link } from "@tanstack/react-router";
import { m } from "motion/react";
import { Markdown } from "@/components/markdown/Markdown";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { Snippet } from "@/features/search/Snippet";
import { formatMs, splitHeadingPath } from "@/lib/format";
import type { AskState } from "./askReducer";
import { StepTrace } from "./StepTrace";

export function AnswerView({ state, onRetry }: { state: AskState; onRetry: () => void }) {
  const streaming = state.status === "streaming";
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-x-12 gap-y-8 lg:grid-cols-[17rem_minmax(0,1fr)]">
      <aside
        aria-label="Agent trace"
        className="order-last lg:sticky lg:top-24 lg:order-first lg:self-start"
      >
        <h2 className="label mb-4 flex items-center justify-between border-b border-border pb-2">
          <span>Trace</span>
          {state.response ? (
            <span className="tabular-nums normal-case tracking-normal">
              {formatMs(state.response.total_ms)} · {state.response.engine}
            </span>
          ) : null}
        </h2>
        <StepTrace steps={state.steps} streaming={streaming} />
      </aside>

      <article aria-live="polite" aria-busy={streaming} className="min-w-0">
        <h1 className="display mb-6 text-[34px] text-balance italic sm:text-[42px]">
          <span className="text-accent">“</span>
          {state.question}
          <span className="text-accent">”</span>
        </h1>

        {state.answer ? (
          <Markdown streaming={streaming} citations={state.citations}>
            {state.answer}
          </Markdown>
        ) : streaming ? (
          <div role="status" className="max-w-[68ch] space-y-3 pt-1" aria-label="Thinking">
            <Skeleton className="h-4 w-11/12" />
            <Skeleton className="h-4 w-4/5" />
            <Skeleton className="h-4 w-2/3" />
          </div>
        ) : null}

        {state.status === "error" && (
          <div
            role="alert"
            className="mt-6 flex max-w-[68ch] items-center justify-between gap-4 border-l-2 border-destructive py-1 pl-4"
          >
            <p className="text-[13.5px] text-destructive">{state.error}</p>
            <Button size="sm" variant="ghost" onClick={onRetry}>
              Try again
            </Button>
          </div>
        )}
        {state.status === "cancelled" && (
          <p className="mt-6 label">
            Stopped.{" "}
            <button type="button" className="text-accent underline" onClick={onRetry}>
              Ask again
            </button>
          </p>
        )}

        {state.citations.length > 0 && (
          <m.section
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3 }}
            aria-labelledby="sources-h"
            className="mt-12 max-w-[68ch] border-t border-border pt-4"
          >
            <h2 id="sources-h" className="label mb-3">
              Sources
            </h2>
            <ol className="space-y-4">
              {state.citations.map((c) => (
                <li key={c.n} className="grid grid-cols-[2rem_1fr] gap-2">
                  <span className="font-mono text-[12px] text-accent">[{c.n}]</span>
                  <div className="min-w-0">
                    <Link
                      to="/documents/$id"
                      params={{ id: c.document_id }}
                      hash={`chunk-${c.chunk_id}`}
                      className="display text-[19px] leading-tight hover:text-accent"
                    >
                      {c.document_title}
                    </Link>
                    {c.heading_path ? (
                      <span className="ml-2 font-mono text-[10.5px] text-faint">
                        {splitHeadingPath(c.heading_path).join(" › ")}
                      </span>
                    ) : null}
                    <p className="mt-1 line-clamp-2 font-serif text-[14.5px] text-muted-foreground">
                      <Snippet html={c.snippet} />
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          </m.section>
        )}
      </article>
    </div>
  );
}
