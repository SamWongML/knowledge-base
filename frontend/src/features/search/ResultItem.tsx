import { Link } from "@tanstack/react-router";
import { AnimatePresence, m } from "motion/react";
import { forwardRef } from "react";
import type { SearchHit } from "@/lib/api/types";
import { pad2, splitHeadingPath } from "@/lib/format";
import { cn } from "@/lib/utils";
import { ScoreInspector } from "./ScoreInspector";
import { Snippet } from "./Snippet";

interface Props {
  hit: SearchHit;
  index: number;
  active: boolean;
  inspector: boolean;
  maxima: Record<string, number>;
  onFocus: () => void;
}

export const ResultItem = forwardRef<HTMLAnchorElement, Props>(function ResultItem(
  { hit, index, active, inspector, maxima, onFocus },
  ref,
) {
  const crumbs = splitHeadingPath(hit.heading_path);
  return (
    <li
      className="relative animate-fade-in"
      style={{ animationDelay: `${Math.min(index, 8) * 25}ms` }}
    >
      <span
        aria-hidden
        className={cn(
          "absolute top-6 bottom-6 -left-3 w-[2px] rounded-full bg-accent transition-opacity sm:-left-5",
          active ? "opacity-100" : "opacity-0",
        )}
      />
      <Link
        ref={ref}
        to="/documents/$id"
        params={{ id: hit.document_id }}
        hash={`chunk-${hit.chunk_id}`}
        onFocus={onFocus}
        data-active={active || undefined}
        className="group grid grid-cols-[2rem_1fr] gap-x-3 py-6 outline-none sm:grid-cols-[2.75rem_1fr] sm:gap-x-4 focus-visible:outline-none"
      >
        <span className="pt-2 font-mono text-[11px] tabular-nums text-faint group-data-[active]:text-accent">
          {pad2(index + 1)}
        </span>
        <div className="min-w-0">
          <h3 className="display text-[24px] leading-[1.15] text-foreground decoration-accent/60 decoration-1 underline-offset-4 group-hover:underline group-data-[active]:underline sm:text-[26px]">
            {hit.document_title}
          </h3>
          {crumbs.length > 0 && (
            <nav
              aria-label="Section"
              className="mt-1 flex flex-wrap items-center gap-x-1.5 font-mono text-[11px] text-muted-foreground"
            >
              {crumbs.map((c, i) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: crumbs are positional
                <span key={`${c}-${i}`} className="flex items-center gap-1.5">
                  {i > 0 && <span className="text-faint">›</span>}
                  <span className="truncate">{c}</span>
                </span>
              ))}
            </nav>
          )}
          <p className="mt-2.5 line-clamp-4 font-serif text-[16.5px] leading-[1.6] text-foreground/85">
            <Snippet html={hit.snippet} />
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 label normal-case tracking-normal">
            <span className="uppercase tracking-[0.08em]">{hit.collection}</span>
            <span className="text-faint">·</span>
            <span>¶ {hit.ord + 1}</span>
            {hit.tags.slice(0, 4).map((t) => (
              <span key={t} className="text-faint">
                #{t}
              </span>
            ))}
            <span className="ml-auto tabular-nums" title="Final score">
              {hit.score.toFixed(3)}
            </span>
          </div>
        </div>
      </Link>
      <AnimatePresence initial={false}>
        {inspector && (
          <m.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: [0.25, 1, 0.5, 1] }}
            className="overflow-hidden"
          >
            <ScoreInspector
              scores={hit.scores}
              maxima={maxima}
              className="mb-6 ml-[2.75rem] sm:ml-[3.75rem]"
            />
          </m.div>
        )}
      </AnimatePresence>
    </li>
  );
});
