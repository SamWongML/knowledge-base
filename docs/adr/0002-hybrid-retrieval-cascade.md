# 0002. Hybrid retrieval with RRF and a reranker cascade

- Status: accepted
- Date: 2026-10-16
- Related: [research §1](../research/README.md#1-hybrid-retrieval-and-reranking), [raw/01](../research/raw/01-retrieval-reranking.md), `kb.search.pipeline`, `kb/config.py` (search and rerank settings)

## Context

Lexical and dense retrieval fail in complementary ways. BM25 misses paraphrases; dense retrieval misses exact identifiers and rare terms. Modern cross-encoders are far more precise than either retriever but cost O(candidates) model calls. Interactive search needs about 1.5 s p95 on a Mac mini. Agentic Q&A can spend more time for precision.

## Decision

A staged cascade. Each stage can be configured and skipped when its model is down:

1. **BM25 top-100** (pg_search) **‖ dense top-100** (HNSW over `halfvec`, with Qwen3-Embedding's query instruction prefix). `KB_SEARCH_CANDIDATES=100`.
2. **Reciprocal Rank Fusion, k=60** (`KB_RRF_K`). Fusion is rank-only, so scores need no normalisation.
3. **Cross-encoder** Qwen3-Reranker-0.6B (llama-server `/v1/rerank`) over the **top 50** (`KB_RERANK_CANDIDATES`).
4. **Optional LLM judge.** `claude-haiku-5-5` ranks the **top 20** listwise (`KB_LLM_RERANK`, `KB_LLM_RERANK_TOP_N`). It is off by default and enabled per request with `llm_rerank`.
5. **Diversity.** Cap the hits per document so one long document cannot fill the page. Then return the top-k (`limit`, default 10), optionally expanding each hit with neighbouring chunks (`expand_context`).

Indexing supports this cascade in three ways:
- Chunks are heading-aware, about 384 tokens with 48 overlap.
- Each chunk carries a `heading_path` breadcrumb, which is indexed by BM25.
- An optional **contextual chunk header** (`KB_CONTEXTUAL_CHUNKS`) is stored in `chunks.context` and fed to both BM25 and the embedding ([Anthropic Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval): −49% retrieval failures, −67% with rerank).

Each response reports per-stage `scores`, `timings` and `degraded[]`.

## Alternatives considered

| Option | Why not (yet) |
|---|---|
| Convex combination (α·dense + (1−α)·bm25) | Bruch et al. show it can beat RRF, but α must be tuned on labelled data. Revisit once the eval set exists. |
| Learned sparse (SPLADE-v3, OpenSearch neural sparse v3) | Small gain over contextual BM25 + dense + rerank. SPLADE is non-commercial. |
| ColBERT / late interaction | 100–300× the storage, and no MaxSim index in Postgres. A 0.6B cross-encoder is stronger and simpler. |
| Larger rerankers (Qwen3-Reranker-4B, zerank-2) | About 3–4× the latency. They are configuration swaps if evals justify them. |
| jina-reranker-v3.5 | CC-BY-NC. |
| Dense-only or BM25-only | Each loses 10–30% of the recall that hybrid retrieval recovers. |

## Consequences

- Recall comes from the cheap stages and precision from the expensive ones. Candidate recall@100 is the number to protect.
- One embedding call, one SQL round-trip per retriever, one rerank call and optionally one Haiku call per query.
- Degraded modes fall out naturally. With no embedder, search is BM25-only. With no reranker, the RRF order is kept.
- The optional Haiku stage sends snippets to the cloud. It is opt-in and costs about $0.001 per query.
- We owe an eval harness: LLM-generated and real queries, recall@100, nDCG@10 and MRR@10, and an ablation grid. Re-run it whenever a model, the chunker or the fusion changes.
