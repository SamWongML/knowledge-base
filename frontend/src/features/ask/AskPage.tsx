import { useNavigate, useSearch } from "@tanstack/react-router";
import { ArrowUp, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Kbd } from "@/components/ui/Kbd";
import { Segmented } from "@/components/ui/Segmented";
import { modKeyLabel } from "@/hooks/useHotkeys";
import type { Engine } from "@/lib/api/types";
import { useSettings } from "@/lib/settings";
import { AnswerView } from "./AnswerView";
import { useAskStream } from "./useAskStream";

const ENGINES = [
  { value: "local", label: "Local", hint: "On-device model" },
  { value: "cloud", label: "Cloud", hint: "Claude (needs an API key on the server)" },
] as const satisfies readonly { value: Engine; label: string; hint: string }[];

export function AskPage() {
  const { q } = useSearch({ from: "/ask" });
  const navigate = useNavigate({ from: "/ask" });
  const settings = useSettings();
  const [question, setQuestion] = useState(q ?? "");
  const [engine, setEngine] = useState<Engine>(settings.defaultEngine);
  const { state, ask, stop } = useAskStream();
  const textarea = useRef<HTMLTextAreaElement>(null);
  const autoRan = useRef(false);

  const submit = (text = question) => {
    const t = text.trim();
    if (!t) return;
    void navigate({ search: { q: t }, replace: true });
    void ask({ question: t, engine });
  };

  // A question arriving via the URL (palette, shared link) runs once on mount.
  useEffect(() => {
    if (q && !autoRan.current) {
      autoRan.current = true;
      void ask({ question: q, engine: settings.defaultEngine });
    }
  }, [q, ask, settings.defaultEngine]);

  // Auto-grow the textarea.
  useEffect(() => {
    const el = textarea.current;
    if (!el) return;
    void question;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 320)}px`;
  }, [question]);

  const streaming = state.status === "streaming";
  const started = state.status !== "idle";

  return (
    <div className="mx-auto max-w-[1080px]">
      <header
        className={started ? "mx-auto mb-10 max-w-[820px]" : "mx-auto mb-10 max-w-[820px] pt-[4vh]"}
      >
        <p className="label mb-3">
          <span className="text-accent">§</span> Ask the commonplace
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
          className="rounded-lg border border-border bg-surface-raised/70 shadow-paper transition-colors focus-within:border-border-strong"
        >
          <textarea
            ref={textarea}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey || !e.shiftKey)) {
                e.preventDefault();
                submit();
              }
            }}
            rows={2}
            // biome-ignore lint/a11y/noAutofocus: asking is the page's single purpose
            autoFocus
            aria-label="Your question"
            placeholder="What did we decide about…?"
            className="block w-full resize-none bg-transparent px-5 pt-4 pb-2 font-display text-[28px] leading-[1.25] tracking-[-0.01em] placeholder:text-faint/80 focus:outline-none sm:text-[32px]"
          />
          <div className="flex flex-wrap items-center gap-3 px-4 pt-1 pb-3">
            <Segmented
              label="Answer engine"
              value={engine}
              onChange={setEngine}
              options={ENGINES}
            />
            <span className="ml-auto hidden items-center gap-1 label sm:flex">
              <Kbd>↵</Kbd> ask · <Kbd>⇧</Kbd>
              <Kbd>↵</Kbd> newline
            </span>
            {streaming ? (
              <Button variant="secondary" onClick={stop} aria-label="Stop generating">
                <Square className="!size-3 fill-current" /> Stop
              </Button>
            ) : (
              <Button
                type="submit"
                variant="primary"
                disabled={!question.trim()}
                aria-label={`Ask (${modKeyLabel}↵)`}
              >
                Ask <ArrowUp strokeWidth={1.75} />
              </Button>
            )}
          </div>
        </form>
      </header>

      {started ? (
        <AnswerView state={state} onRetry={() => submit(state.question)} />
      ) : (
        <AskHints
          onPick={(t) => {
            setQuestion(t);
            submit(t);
          }}
        />
      )}
    </div>
  );
}

const EXAMPLES = [
  "Summarise what we know about the deployment setup",
  "Which documents mention embeddings, and how do they differ?",
  "What open questions are recorded in my notes?",
];

function AskHints({ onPick }: { onPick: (q: string) => void }) {
  return (
    <section className="mx-auto max-w-[820px]" aria-labelledby="hints-h">
      <h2 id="hints-h" className="label mb-3 border-b border-border pb-2">
        Try asking
      </h2>
      <ul className="divide-y divide-border/70">
        {EXAMPLES.map((e) => (
          <li key={e}>
            <button
              type="button"
              onClick={() => onPick(e)}
              className="group flex w-full items-baseline gap-3 py-3 text-left"
            >
              <span className="font-mono text-[11px] text-accent">→</span>
              <span className="font-serif text-[18px] italic text-foreground/80 group-hover:text-foreground">
                {e}
              </span>
            </button>
          </li>
        ))}
      </ul>
      <p className="mt-6 max-w-prose text-[13px] text-muted-foreground">
        The agent plans, searches your library (possibly several times), reads around the best
        passages and writes an answer with numbered citations. Hover a citation to preview its
        source.
      </p>
    </section>
  );
}
