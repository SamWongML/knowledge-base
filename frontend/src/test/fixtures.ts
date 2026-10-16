import type { Collection, SearchHit, SearchResponse } from "@/lib/api/types";

export const collections: Collection[] = [
  {
    id: "c0",
    slug: "default",
    name: "Default",
    description: "",
    created_at: "2026-10-01T10:00:00Z",
    document_count: 12,
  },
  {
    id: "c1",
    slug: "research",
    name: "Research",
    description: "Papers and reading notes",
    created_at: "2026-10-02T10:00:00Z",
    document_count: 31,
  },
];

export function hit(over: Partial<SearchHit> = {}): SearchHit {
  return {
    chunk_id: 101,
    document_id: "8c7d2c55-0a52-4c38-9d0d-6a4a4c1f0a01",
    document_title: "Hybrid retrieval notes",
    collection: "research",
    source_uri: null,
    heading_path: "Retrieval > Fusion",
    content: "Reciprocal rank fusion combines BM25 and dense rankings.",
    snippet: "Reciprocal rank <mark>fusion</mark> combines BM25 &amp; dense rankings.",
    score: 0.912,
    scores: { bm25: 12.4, dense: 0.81, rrf: 0.032, rerank: 0.93 },
    ord: 3,
    tags: ["search", "rag"],
    ...over,
  };
}

export const searchResponse: SearchResponse = {
  query: "fusion",
  mode: "hybrid",
  hits: [
    hit(),
    hit({
      chunk_id: 102,
      document_title: "Postgres as a search engine",
      heading_path: "BM25 > Tokenizers",
      snippet: "The <mark>fusion</mark> step <script>alert(1)</script> runs last.",
      scores: { bm25: 8.1, dense: 0.62, rrf: 0.021, rerank: 0.55 },
      score: 0.55,
      ord: 0,
      tags: [],
    }),
  ],
  timings: [
    { stage: "lexical", ms: 4.2, detail: null },
    { stage: "dense", ms: 11.8, detail: null },
    { stage: "rerank", ms: 61.3, detail: null },
  ],
  degraded: [],
  total_ms: 78.4,
};
