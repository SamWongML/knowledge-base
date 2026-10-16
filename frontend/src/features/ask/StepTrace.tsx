import {
  AlertTriangle,
  BookOpen,
  Compass,
  Lightbulb,
  type LucideIcon,
  PenLine,
  Search,
} from "lucide-react";
import { AnimatePresence, m, useReducedMotion } from "motion/react";
import type { AgentStepType, JsonValue } from "@/lib/api/types";
import { cn } from "@/lib/utils";
import type { TraceStep } from "./askReducer";

const META: Record<AgentStepType, { icon: LucideIcon; label: string }> = {
  plan: { icon: Compass, label: "Plan" },
  search: { icon: Search, label: "Search" },
  read: { icon: BookOpen, label: "Read" },
  reflect: { icon: Lightbulb, label: "Reflect" },
  answer_delta: { icon: PenLine, label: "Write" },
  answer: { icon: PenLine, label: "Answer" },
  error: { icon: AlertTriangle, label: "Note" },
};

function stringList(v: JsonValue | undefined): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

export function StepTrace({ steps, streaming }: { steps: TraceStep[]; streaming: boolean }) {
  const reduce = useReducedMotion();
  const writing = streaming && !steps.some((s) => s.type === "answer");
  return (
    <ol className="relative space-y-5 before:absolute before:top-2 before:bottom-2 before:left-[11px] before:w-px before:bg-border">
      <AnimatePresence initial={false}>
        {steps.map((s, i) => (
          <m.li
            key={s.key}
            layout={!reduce}
            initial={reduce ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.28, ease: [0.25, 1, 0.5, 1] }}
            className="relative pl-9"
          >
            <StepNode step={s} active={streaming && i === steps.length - 1 && !writing} />
            <StepBody step={s} />
          </m.li>
        ))}
        {writing && (
          <m.li
            key="pending"
            initial={reduce ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            className="relative pl-9"
          >
            <span className="absolute top-0.5 left-0 grid size-[23px] place-items-center rounded-full border border-accent bg-background">
              <span className="size-1.5 animate-pulse rounded-full bg-accent" />
            </span>
            <p className="label pt-1">{steps.length ? "Working…" : "Starting…"}</p>
          </m.li>
        )}
      </AnimatePresence>
    </ol>
  );
}

function StepNode({ step, active }: { step: TraceStep; active: boolean }) {
  const Icon = META[step.type].icon;
  return (
    <span
      className={cn(
        "absolute top-0.5 left-0 grid size-[23px] place-items-center rounded-full border bg-background",
        step.type === "error"
          ? "border-warning/60 text-warning"
          : step.type === "answer"
            ? "border-accent bg-accent text-accent-foreground"
            : "border-border-strong text-muted-foreground",
        active && "animate-pulse-ring border-accent text-accent",
      )}
    >
      <Icon className="size-3" strokeWidth={1.75} />
    </span>
  );
}

function StepBody({ step }: { step: TraceStep }) {
  const queries = stringList(step.data.queries);
  const nextQueries = stringList(step.data.next_queries);
  const hits = step.hits ?? [];
  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-2">
        <span className="label text-foreground">{META[step.type].label}</span>
        <span className="font-mono text-[10px] tabular-nums text-faint">
          +{(step.at / 1000).toFixed(1)}s
        </span>
      </div>
      {step.query ? (
        <p className="mt-0.5 font-serif text-[15px] italic leading-snug text-foreground/90">
          “{step.query}”
        </p>
      ) : null}
      {step.type !== "answer" && step.message ? (
        <p className="mt-0.5 text-[12.5px] leading-snug text-muted-foreground">{step.message}</p>
      ) : null}
      {step.type === "answer" ? (
        <p className="mt-0.5 text-[12.5px] text-muted-foreground">Answer written.</p>
      ) : null}
      {queries.length > 0 && (
        <ul className="mt-1.5 space-y-0.5">
          {queries.map((q) => (
            <li key={q} className="truncate font-mono text-[11px] text-muted-foreground">
              → {q}
            </li>
          ))}
        </ul>
      )}
      {nextQueries.length > 0 && (
        <ul className="mt-1.5 space-y-0.5">
          {nextQueries.map((q) => (
            <li key={q} className="truncate font-mono text-[11px] text-muted-foreground">
              ↻ {q}
            </li>
          ))}
        </ul>
      )}
      {hits.length > 0 && (
        <details className="group mt-1.5">
          <summary className="cursor-pointer list-none font-mono text-[11px] text-accent marker:hidden">
            <span className="group-open:hidden">▸</span>
            <span className="hidden group-open:inline">▾</span> {hits.length} hit
            {hits.length === 1 ? "" : "s"}
          </summary>
          <ul className="mt-1 space-y-1 border-l border-border pl-2">
            {hits.slice(0, 8).map((h) => (
              <li key={h.chunk_id} className="truncate text-[12px] text-muted-foreground">
                {h.document_title}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
