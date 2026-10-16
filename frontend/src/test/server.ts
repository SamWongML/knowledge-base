import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { collections, searchResponse } from "./fixtures";

export const handlers = [
  http.get("*/api/collections", () => HttpResponse.json(collections)),
  http.get("*/api/stats", () =>
    HttpResponse.json({
      documents: 43,
      chunks: 1204,
      collections: 2,
      queued_jobs: 0,
      failed_jobs: 0,
      queries_24h: 17,
    }),
  ),
  http.get("*/api/queries/recent", () =>
    HttpResponse.json([
      { query: "rank fusion", mode: "hybrid", created_at: "2026-10-16T08:00:00Z" },
    ]),
  ),
  http.post("*/api/search", () => HttpResponse.json(searchResponse)),
  http.get("*/api/documents/:id", ({ params }) =>
    HttpResponse.json({
      id: params.id,
      collection_id: "c1",
      collection: "research",
      title: "Hybrid retrieval notes",
      source_uri: null,
      mime_type: "text/markdown",
      tags: ["search"],
      status: "ready",
      error: null,
      chunk_count: 1,
      created_at: "2026-10-10T10:00:00Z",
      updated_at: "2026-10-12T10:00:00Z",
      body: "# Retrieval\n\nReciprocal rank fusion combines BM25 and dense rankings.",
      metadata: {},
    }),
  ),
  http.get("*/api/documents/:id/chunks", () =>
    HttpResponse.json([
      {
        id: 101,
        ord: 0,
        heading_path: "Retrieval",
        content: "# Retrieval\n\nReciprocal rank fusion combines BM25 and dense rankings.",
        token_count: 14,
      },
    ]),
  ),
  // A live-events stream that sends one ping and stays open until the client aborts.
  http.get("*/api/events", ({ request }) => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("event: ping\ndata: {}\n\n"));
        request.signal.addEventListener("abort", () => controller.close());
      },
    });
    return new HttpResponse(stream, { headers: { "Content-Type": "text/event-stream" } });
  }),
];

export const server = setupServer(...handlers);
