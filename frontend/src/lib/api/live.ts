import { type InfiniteData, type QueryClient, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { streamEvents } from "./endpoints";
import { keys } from "./queries";
import type { Document, DocumentStatusEvent, DocumentSummary, Page } from "./types";

export type LiveState = "connecting" | "live" | "offline";

/** Apply a document.status event to every cached list and detail entry. */
export function applyStatusEvent(qc: QueryClient, ev: DocumentStatusEvent): void {
  let found = false;
  const patch = <T extends DocumentSummary>(d: T): T => {
    if (d.id !== ev.id) return d;
    found = true;
    return {
      ...d,
      status: ev.status,
      error: ev.error,
      chunk_count: ev.chunk_count,
      title: ev.title || d.title,
    };
  };
  qc.setQueriesData<InfiniteData<Page<DocumentSummary>>>({ queryKey: keys.documents }, (data) =>
    data ? { ...data, pages: data.pages.map((p) => ({ ...p, items: p.items.map(patch) })) } : data,
  );
  qc.setQueryData<Document>(keys.document(ev.id), (d) => (d ? patch(d) : d));
  if (!found) void qc.invalidateQueries({ queryKey: keys.documents });
  if (ev.status === "ready" || ev.status === "failed") {
    void qc.invalidateQueries({ queryKey: keys.stats });
    void qc.invalidateQueries({ queryKey: keys.collections });
    void qc.invalidateQueries({ queryKey: keys.chunks(ev.id) });
  }
}

/** Keep one /api/events subscription open for the app, reconnecting with backoff. */
export function useLiveEvents(): LiveState {
  const qc = useQueryClient();
  const [state, setState] = useState<LiveState>("connecting");

  useEffect(() => {
    const ctrl = new AbortController();
    let attempt = 0;
    const run = async () => {
      while (!ctrl.signal.aborted) {
        try {
          setState("connecting");
          for await (const ev of streamEvents(ctrl.signal)) {
            attempt = 0;
            setState("live");
            if (ev.type === "document.status") {
              applyStatusEvent(qc, ev.payload);
              if (ev.payload.status === "failed") {
                toast.error(`Ingestion failed: ${ev.payload.title || "document"}`, {
                  description: ev.payload.error ?? undefined,
                });
              }
            }
          }
        } catch {
          // fall through to backoff
        }
        if (ctrl.signal.aborted) return;
        attempt += 1;
        setState(attempt > 1 ? "offline" : "connecting");
        const delay = Math.min(30_000, 1000 * 2 ** Math.min(attempt, 5));
        await new Promise((r) => setTimeout(r, delay));
      }
    };
    void run();
    return () => ctrl.abort();
  }, [qc]);

  return state;
}
