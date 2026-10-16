import { rawRequest, request } from "./client";
import { parseJSON, readSSE } from "./sse";
import type {
  AgentStep,
  AskRequest,
  AskResponse,
  Chunk,
  Collection,
  CollectionIn,
  Document,
  DocumentIn,
  DocumentPatch,
  DocumentStatus,
  DocumentStatusEvent,
  DocumentSummary,
  Health,
  IngestAccepted,
  Page,
  RecentQuery,
  SearchRequest,
  SearchResponse,
  Stats,
} from "./types";

export interface DocumentListParams {
  collection?: string | undefined;
  status?: DocumentStatus | undefined;
  tag?: string | undefined;
  q?: string | undefined;
  limit?: number;
  offset?: number;
}

export const api = {
  health: (signal?: AbortSignal) => request<Health>("/health", { signal }),
  stats: (signal?: AbortSignal) => request<Stats>("/stats", { signal }),
  recentQueries: (limit = 20, signal?: AbortSignal) =>
    request<RecentQuery[]>("/queries/recent", { query: { limit }, signal }),

  collections: (signal?: AbortSignal) => request<Collection[]>("/collections", { signal }),
  createCollection: (body: CollectionIn) =>
    request<Collection>("/collections", { method: "POST", body }),
  deleteCollection: (slug: string) =>
    request<void>(`/collections/${encodeURIComponent(slug)}`, { method: "DELETE" }),

  documents: (params: DocumentListParams, signal?: AbortSignal) =>
    request<Page<DocumentSummary>>("/documents", {
      query: {
        collection: params.collection,
        status: params.status,
        tag: params.tag,
        q: params.q,
        limit: params.limit ?? 50,
        offset: params.offset ?? 0,
      },
      signal,
    }),
  document: (id: string, signal?: AbortSignal) =>
    request<Document>(`/documents/${encodeURIComponent(id)}`, { signal }),
  chunks: (id: string, signal?: AbortSignal) =>
    request<Chunk[]>(`/documents/${encodeURIComponent(id)}/chunks`, { signal }),
  createDocument: (body: DocumentIn) =>
    request<IngestAccepted>("/documents", { method: "POST", body }),
  upload: (files: File[], collection: string, tags: string[]) => {
    const form = new FormData();
    for (const f of files) form.append("files", f, f.name);
    form.append("collection", collection);
    form.append("tags", tags.join(","));
    return request<IngestAccepted[]>("/documents/upload", { method: "POST", body: form });
  },
  updateDocument: (id: string, patch: DocumentPatch) =>
    request<Document>(`/documents/${encodeURIComponent(id)}`, { method: "PATCH", body: patch }),
  deleteDocument: (id: string) =>
    request<void>(`/documents/${encodeURIComponent(id)}`, { method: "DELETE" }),
  reindexDocument: (id: string) =>
    request<IngestAccepted>(`/documents/${encodeURIComponent(id)}/reindex`, { method: "POST" }),

  search: (body: SearchRequest, signal?: AbortSignal) =>
    request<SearchResponse>("/search", { method: "POST", body, signal }),
  ask: (body: AskRequest, signal?: AbortSignal) =>
    request<AskResponse>("/ask", { method: "POST", body, signal }),
};

// --- streaming ---------------------------------------------------------------------

export type AskStreamEvent =
  | { type: "step"; step: AgentStep }
  | { type: "done"; response: AskResponse }
  | { type: "error"; message: string };

/** POST /api/ask/stream — yields agent steps, then a final `done` (or `error`). */
export async function* streamAsk(
  body: AskRequest,
  signal?: AbortSignal,
): AsyncGenerator<AskStreamEvent> {
  const res = await rawRequest("/ask/stream", {
    method: "POST",
    body,
    signal,
    headers: { Accept: "text/event-stream" },
  });
  if (!res.body) throw new Error("Streaming is not supported by this browser");
  for await (const msg of readSSE(res.body, signal)) {
    yield* toAskEvent(msg.event, msg.data);
  }
}

export function* toAskEvent(event: string, data: string): Generator<AskStreamEvent> {
  if (event === "step") {
    const step = parseJSON<AgentStep>(data);
    if (step) yield { type: "step", step: normaliseStep(step) };
  } else if (event === "done") {
    const response = parseJSON<AskResponse>(data);
    if (response) yield { type: "done", response };
  } else if (event === "error") {
    const parsed = parseJSON<{ detail?: string; message?: string } | string>(data);
    const message =
      typeof parsed === "string"
        ? parsed
        : (parsed?.detail ?? parsed?.message ?? (data || "The answer stream failed"));
    yield { type: "error", message };
  }
}

function normaliseStep(step: Partial<AgentStep> & Pick<AgentStep, "type">): AgentStep {
  return {
    type: step.type,
    message: step.message ?? "",
    query: step.query ?? null,
    hits: step.hits ?? null,
    data: step.data ?? {},
  };
}

export type LiveEvent =
  | { type: "document.status"; payload: DocumentStatusEvent }
  | { type: "open" }
  | { type: "ping" };

/** GET /api/events — one connection; the caller handles reconnection. */
export async function* streamEvents(signal: AbortSignal): AsyncGenerator<LiveEvent> {
  const res = await rawRequest("/events", { signal, headers: { Accept: "text/event-stream" } });
  if (!res.body) return;
  yield { type: "open" };
  for await (const msg of readSSE(res.body, signal)) {
    if (msg.event === "document.status") {
      const payload = parseJSON<DocumentStatusEvent>(msg.data);
      if (payload) yield { type: "document.status", payload };
    } else if (msg.event === "ping") {
      yield { type: "ping" };
    }
  }
}
