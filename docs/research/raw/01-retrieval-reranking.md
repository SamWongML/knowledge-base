# 01 — Hybrid Retrieval & Reranking: State of the Art (as of Oct 2026)

Target: personal/team knowledge base on one Apple Silicon Mac mini (M4/M4 Pro, 32–64 GB), Python + PostgreSQL.
Legend: **[verified]** = checked against a primary source during this research; **[uncertain]** = secondary source or my inference, worth re-checking before you commit.

---

## 1. Lexical retrieval in Postgres

| Option | What it is | License | Notes |
|---|---|---|---|
| Native `tsvector` + GIN + `ts_rank_cd` | Built in | PostgreSQL | No real BM25 (no IDF saturation/length norm). On BEIR SciFact it scored nDCG@10 **0.33**, vs **~0.68–0.69** for the BM25 extensions [verified, Particula benchmark]. Fine as a filter, bad as a ranker. |
| **pg_textsearch** (Tiger Data) | BM25 index AM on Postgres pages, Block-Max WAND | PostgreSQL licence; v1.4–1.5.x | Requires **PG 17/18**. `CREATE INDEX ... USING bm25(content) WITH (text_config='english')`, `ORDER BY content <@> 'query'` (negated score). Uses standard Postgres text-search configs for tokenizing. WAL-replicated. No phrase/fuzzy/prefix yet [verified]. |
| **ParadeDB pg_search** | Tantivy-based BM25 | **AGPL-3.0** (commercial Enterprise available); v0.25–0.26 | PG 15–18; phrase, fuzzy, prefix, highlighting; faster at top-100 (0.8 ms vs 10 ms p50 in one benchmark). Community index not physically replicated. Neon is deprecating it (retired 21 Sep 2026) [verified]. |
| VectorChord-bm25 (`vchord_bm25` + `pg_tokenizer`) | Block-WeakAnd BM25, pgvector-like operator | AGPL-3.0 (pg_tokenizer Apache-2.0); v0.3.0 | Good tokenizer options (incl. multilingual/LLM tokenizers), lighter than ParadeDB, smaller community [verified licence via Pigsty listing]. |

Gotchas: pg_search and pg_textsearch **cannot coexist in the same database** (both register an access method named `bm25`). Both leaked RLS-hidden rows into corpus statistics in one test, which matters if you do multi-tenant with RLS [verified, Particula].

**Recommendation:** pg_textsearch on PG 17/18 as the default (permissive licence, simple SQL, quality on par with pg_search). Use pg_search if you need phrase or fuzzy search now and AGPL is acceptable (AGPL is usually fine for an internal, unmodified self-hosted deployment, but check with whoever owns licensing). For CJK-heavy content, look at VectorChord-bm25 + pg_tokenizer or pgroonga.

**Learned sparse.** These models need a sparse-vector store. pgvector has `sparsevec` (up to 1,000 non-zero values for indexing [uncertain, check docs]).
- `opensearch-project/opensearch-neural-sparse-encoding-doc-v3-gte`: 133M params, Apache-2.0, **inference-free on the query side** (the query is just tokenizer lookup plus IDF weights). Scores 0.546 average nDCG@10 on a 13-dataset BEIR subset, vs 0.517 for v3-distill [verified]. English-only. A good fit for a Mac mini because the per-query cost is zero.
- SPLADE-v3 (naver): strong, but **CC-BY-NC-SA** [uncertain, from memory] and needs query-side inference.
- BGE-M3 sparse head (MIT): multilingual, and one forward pass gives you dense, sparse and ColBERT vectors. Quality is now behind Qwen3/jina-v5 dense models.

Verdict: learned sparse is **optional**. With contextual BM25 plus a strong dense model plus a reranker, its marginal gain is small. Add it only if your evals show lexical-recall misses that BM25 can't fix.

---

## 2. Dense embeddings

| Model (HF id) | Params / dims | Licence | Retrieval quality | Notes |
|---|---|---|---|---|
| `Qwen/Qwen3-Embedding-0.6B` | 0.6B / 1024, MRL 32–1024, 32K ctx | **Apache-2.0** | MTEB-Eng-v2 Retrieval 61.83; MMTEB Retrieval 64.64 | Query needs `Instruct: {task}\nQuery:{q}`. Leaving it out costs 1–5% [verified]. Official GGUF `Qwen/Qwen3-Embedding-0.6B-GGUF` [uncertain on exact repo name]. |
| `Qwen/Qwen3-Embedding-4B` | 4B / 2560, MRL | Apache-2.0 | Eng Retrieval 68.46; MMTEB Retrieval 69.60 | Big jump over 0.6B. About 4–5 GB at Q8 and feasible on a Mac mini. Indexing throughput is the cost. |
| `Qwen/Qwen3-Embedding-8B` | 8B / 4096 | Apache-2.0 | Eng Retrieval 69.44 | Marginal over 4B. Not worth it here. |
| `jinaai/jina-embeddings-v5-text-small` (Feb 2026) | 677M / 1024, MRL 32–1024, 32K ctx | **CC-BY-NC-4.0** | MMTEB 67.0 vs 64.3 for Qwen3-0.6B. Best sub-1B multilingual per Jina | Official GGUF (14 quants) and MLX (`...-retrieval-mlx`) builds. **Non-commercial**: fine for strictly personal use, a problem for a company team [verified]. `-nano` is 239M. |
| `google/embeddinggemma-300m` | 300M / 768, MRL 512/256/128, **2K ctx** | Gemma licence (gated) | MTEB Eng v2 mean 69.67 (all tasks) | Tiny and fast. Built into llama.cpp (`--embd-gemma-default`). The 2K context limits chunk size [verified]. |
| `BAAI/bge-m3` | 568M / 1024 + sparse + ColBERT | MIT | Older baseline | Useful only for the "all three representations" trick. |
| `nomic-embed-text-v2-moe` | ~475M MoE / 768 MRL | Apache-2.0 | Below the above | Easy via Ollama. |
| `Qwen/Qwen3-VL-Embedding-2B/8B` (Jan 2026) | multimodal (text + image + doc screenshots + video), MRL, 32K | [uncertain, likely Apache-2.0] | MMEB-V2 SOTA | Only if you want to embed page images or screenshots. |

Note: several 2026 "ranking" blog posts misstate the Qwen3 licence (as "Tongyi Qianwen") and the release date. The model cards say Apache-2.0 and a June 2025 release [verified].

**Recommendation:** default to **Qwen3-Embedding-0.6B** (Apache, 32K context, instruction-aware), stored at **1024-d halfvec**. If index-time budget allows and you want about 6 more retrieval points, use **Qwen3-Embedding-4B truncated via MRL to 1024 or 1536 dims**. Note that MRL truncation needs re-normalization. Make the model a pluggable setting and store the `model_id` and `dim` per embedding column, so you can re-embed later.

**pgvector (0.8.x, latest 0.8.7 on 2026-10-01) [verified]:**
- Use `halfvec(1024)`. It halves storage, recall loss is negligible, and HNSW supports up to 4,000 dims for halfvec (vs 2,000 for `vector`).
- HNSW defaults are `m=16, ef_construction=64, ef_search=40`. For a KB (<10M chunks), use `m=16–24, ef_construction=128–200` and set `hnsw.ef_search=100–200` per query, since you fetch about 100 candidates.
- With metadata filters (tags, ACL, workspace), set `hnsw.iterative_scan = relaxed_order` (0.8+) so filtered queries still return k rows. Tune `hnsw.max_scan_tuples` (default 20,000).
- Binary quantization: expression index on `binary_quantize(embedding)::bit(1024)` with Hamming distance, over-fetch (e.g. 200), then re-score with full-precision cosine. **Not needed below about 1–2M chunks** on a 32–64 GB machine. Keep it as an option.
- At the scale of a personal or team KB (10⁴–10⁶ chunks), exact scans are even viable. HNSW over halfvec is the simple default.

---

## 3. Late interaction (ColBERT)

Options: PyLate (LightOn's training/inference library), `lightonai/GTE-ModernColBERT-v1`, `Reason-ModernColBERT`, jina-colbert-v2 (NC licence).
- Storage is the problem. One vector per token (128-d) means about 100–300× the storage of single-vector embeddings. Postgres has no native MaxSim index. VectorChord has multivector support [uncertain on maturity]. Otherwise you need PLAID/Voyager sidecars.
- On a Mac mini the better use is **ColBERT as a reranker** (MaxSim over the ~100 fused candidates, encoding docs on the fly). But a 0.6B cross-encoder (Qwen3-Reranker / jina-reranker-v3.5) is now both stronger and simple to serve.
- **Verdict: not worth it as a first-stage index here.** Revisit only if evals show the reranker is the bottleneck and latency is tight.

---

## 4. Fusion

- **RRF**: `score = Σ 1/(k + rank_i)`, with the standard **k = 60**. It is rank-only, so you never normalize scores. It is the robust zero-config default, and pgvector docs point to RRF or a cross-encoder for hybrid search [verified].
- **Convex combination** (`α·norm(dense) + (1-α)·norm(bm25)`, with min-max or z-score normalization). Bruch et al. (TOIS 2023) find it **beats RRF in- and out-of-domain**, RRF is **parameter-sensitive**, and tuning α needs only a small labelled set [verified].
- Learned fusion (LTR over features) is overkill here.
- **Recommendation:** ship RRF (k=60, with per-retriever weights possible) and implement it in SQL with two CTEs and a `FULL OUTER JOIN`. Once you have the eval set from §7, try convex combination with a tuned α, and keep whichever wins. With a strong reranker downstream, the choice of fusion function matters much less than **candidate recall@100**.

---

## 5. Reranking

**Cross-encoder / "judgement" rerankers (local):**

| Model | Params | Licence | Evidence |
|---|---|---|---|
| `Qwen/Qwen3-Reranker-0.6B` | 0.6B | **Apache-2.0** | MTEB-R 65.80 vs 57.03 for bge-reranker-v2-m3; MMTEB-R 66.36 vs 58.36 [verified] |
| `Qwen/Qwen3-Reranker-4B` | 4B | Apache-2.0 | MTEB-R 69.76, MMTEB-R 72.74, FollowIR 14.84 (best of the family) [verified] |
| `jinaai/jina-reranker-v3.5` (Jul 2026) | 0.6B listwise | **CC-BY-NC-4.0** | BEIR 63.20 vs 62.28 for Qwen3-Reranker-4B (Jina's protocol); official GGUF + MLX [verified] |
| `zeroentropy/zerank-2` | 4B (Qwen3-4B base) | Apache-2.0 | Vendor table: beats Cohere 3.5 and Gemini 2.5 Flash as a reranker [verified claim, vendor-run] |
| `mixedbread-ai/mxbai-rerank-large-v2` / `-base-v2` | 1.5B / 0.5B (Qwen2.5) | Apache-2.0 | Solid, GGUF/MLX community builds [uncertain on numbers] |
| `BAAI/bge-reranker-v2-m3` | 568M | MIT | Previous default. Fastest classic cross-encoder, now clearly behind |

Qwen3-Reranker is a **"decision"-style reranker**. It prompts an LLM with `<Instruct>/<Query>/<Document>` and takes the score as P("yes") vs P("no") at the last token. Custom instructions add 1–5% [verified]. This means you can steer it with domain instructions, for example "Given a question about our internal engineering docs…".

**Serving on Apple Silicon [verified]:**
```
llama-server -m Qwen3-Reranker-0.6B-Q8_0.gguf --reranking --pooling rank --embedding --port 8081
# POST /v1/rerank {"query": "...", "documents": [...], "top_n": 20}
```
Use `ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF` (639 MB). Many community Qwen3-Reranker GGUFs **produce garbage scores** because they lack the classifier tensors (llama.cpp issue #16407). Use the ggml-org build or convert it yourself with current `convert_hf_to_gguf.py`. Embeddings are served the same way: `llama-server -m Qwen3-Embedding-0.6B-Q8_0.gguf --embedding --pooling last --port 8080` → `/v1/embeddings` (OpenAI-compatible). Ollama has embeddings but **no native rerank endpoint** as far as I could verify, so use llama-server (or MLX / sentence-transformers on MPS) for reranking. LM Studio exposes embeddings and is fine for experiments.

Latency [uncertain, extrapolated]: a 0.6B reranker at Q8 on M4 Pro scoring 50 pairs × ~400 tokens should take roughly **0.3–1 s**. The 4B model takes about 2–4 s for 50 pairs. Measure it.

**LLM rerank with a small cloud model:** Anthropic's current small model is **Claude Haiku 5.5, API id `claude-haiku-5-5`** (1M context, listed "from $0.10 / $0.50 per MTok" [verified on platform.claude.com models overview; "from" pricing should be checked on the pricing page]). Use it **listwise**: give it the top 15–30 reranked chunks (truncated to ~300 tokens each) and ask for a JSON list of relevant ids in order, plus an optional "none relevant" flag. That costs about 10k input tokens per query, i.e. ≈ $0.001, with roughly 1–2 s latency. It works best as an **optional precision stage** for agentic/QA use, and it also catches "no answer in KB".

**Cascade (recommended):** fused top-100 → local 0.6B cross-encoder → top-20/30 → *(optional)* Haiku listwise → top 5–10. Budget is about 1.5 s interactive without the LLM stage and about 3 s with it.

---

## 6. Chunking, indexing quality, parsing

- **Anthropic Contextual Retrieval** [verified]: prepend a 50–100-token LLM-written context ("this chunk is from X, section Y, about Z") to each chunk before **both** embedding and BM25. Top-20 retrieval failure fell 5.7% → 3.7% (contextual embeddings, −35%), → 2.9% (+contextual BM25, −49%), → 1.9% (+rerank, −67%). Anthropic retrieved the top 150, reranked, and kept 20. Cost is ~$1 per M doc tokens with prompt caching (on Claude 3 Haiku). Use `claude-haiku-5-5` with prompt caching on the full doc, or a local small LLM (e.g. a Qwen3 4B–8B instruct via llama-server or MLX) for zero marginal cost. **This is the highest-ROI indexing improvement.** Store the context separately so you can re-generate it.
- **Structure-aware chunking**: parse to Markdown, split on the heading hierarchy, target ~300–800 tokens with small overlap, never split tables or code blocks, and prepend a heading breadcrumb (`Doc title > H1 > H2`). This gives you a free, deterministic "contextual header" even without an LLM.
- **Parent-child / small-to-big**: retrieve and rerank small chunks, then return the parent section (or neighbouring chunks) to the LLM. Implement it as a `parent_id` column. Cheap, and it consistently helps answer quality.
- **Late chunking** (Jina, arXiv 2409.04701): embed the whole doc with a long-context model, then mean-pool per chunk span. It needs token-level outputs, which llama-server `--pooling none` can give [uncertain on ergonomics]. It overlaps with contextual retrieval; prefer the latter.
- **Semantic (embedding-similarity) chunking**: mixed evidence. Structure-aware splitting is simpler and usually as good.
- **Parsing** [verified, Jul 2026 versions]: **Docling** (MIT, v2.11x, MLX-accelerated on Apple Silicon, about 14 s for an arXiv paper; best on tables and layout) is the default. **MarkItDown** (MIT) for Office formats and the fast path. **Marker** v2 (code Apache-2.0, weights are modified RAIL-M with a revenue cap) for math-heavy PDFs.

---

## 7. Evaluation

- **Metrics:** recall@k for candidate generation (recall@50/100 before rerank is the key number), **nDCG@10** and MRR@10 after rerank, plus p50/p95 latency per stage.
- **Building a test set:** (1) sample ~200–500 chunks; (2) have an LLM (Haiku) generate 1–3 realistic queries per chunk, including keyword-style, paraphrased and multi-hop ones, and filter out queries that copy the text verbatim; (3) add 30–100 **real** queries from usage logs and label them by hand or with LLM judgement; (4) use LLM-as-judge (graded 0–3) to label the pooled top-20 of all systems, so you avoid false negatives where several chunks are relevant.
- **Harness:** store `queries`, `qrels` and `runs` tables in Postgres; score with `ranx` (fast, has fusion utilities incl. RRF/CombSUM and α-tuning) or `pytrec_eval`; run an ablation grid (BM25 only / dense only / RRF / +contextual / +rerank 0.6B / +rerank 4B / +Haiku). For sanity checks against public numbers, run a small BEIR set (SciFact, FiQA, NFCorpus) through the same pipeline.
- Re-run the grid whenever you swap the model, chunker or fusion. This is why the components must be pluggable.

---

## RECOMMENDED PIPELINE (default for an M4/M4 Pro Mac mini)

**Ingest (offline/async worker)**
1. Parse: Docling (PDF/HTML) → Markdown; MarkItDown for DOCX/PPTX/XLSX.
2. Chunk: heading-aware, 300–800 tokens, ~10–15% overlap, tables and code kept intact; keep `parent_id` (section) and a breadcrumb.
3. Contextualize *(optional, recommended)*: 50–100-token context per chunk via `claude-haiku-5-5` with prompt caching, **or** a local instruct LLM. Index the text = breadcrumb + context + chunk.
4. Index:
   - BM25: **pg_textsearch** (`USING bm25 ... text_config='english'` or per-language config). Alternative: pg_search.
   - Dense: **`Qwen/Qwen3-Embedding-0.6B`** (GGUF `Qwen3-Embedding-0.6B-Q8_0.gguf`, served by `llama-server --embedding --pooling last`) → `halfvec(1024)` + HNSW (`m=16, ef_construction=128`, `vector_cosine_ops`/`halfvec_cosine_ops`). Upgrade: `Qwen/Qwen3-Embedding-4B` at MRL 1024–1536.
   - *(optional)* sparse: `opensearch-project/opensearch-neural-sparse-encoding-doc-v3-gte` → `sparsevec`.

**Query**
1. *(optional)* Query rewrite/expansion for chat follow-ups only (Haiku). Skip for single-shot search.
2. Retrieve in parallel inside one SQL round-trip: BM25 top **100** + dense top **100** (`hnsw.ef_search=200`, `iterative_scan=relaxed_order` with filters) [+ sparse top 100].
3. Fuse: **RRF k=60** → top **50–100** unique chunks. Pluggable: convex combination with α tuned on evals.
4. Rerank: **`Qwen/Qwen3-Reranker-0.6B`** via `ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF` on `llama-server --reranking --pooling rank`, with a domain instruction → top **20**. Upgrade: Qwen3-Reranker-4B (GGUF Q8/Q5) if latency allows. Non-commercial/personal alternative: `jinaai/jina-reranker-v3.5`. Keep bge-reranker-v2-m3 as the fast fallback.
5. *(optional)* LLM listwise judge: `claude-haiku-5-5` over the top 20 → top **5–10**, plus a "no relevant result" signal.
6. Expand small-to-big: return parent sections/neighbours, deduplicated per document.

**Ops:** run two llama-server processes (embed :8080, rerank :8081) under launchd. Memory is about 1.5 GB total at Q8 for the 0.6B pair, or about 10 GB with the 4B pair, so either fits easily in 32 GB next to Postgres. Store `embedding_model`, `dim` and `chunker_version` per row so re-indexing is safe. Gate every optional stage behind config and measure it with the §7 harness.

---

## Sources
- Qwen3-Embedding model card: https://huggingface.co/Qwen/Qwen3-Embedding-0.6B
- Qwen3-Reranker model card: https://huggingface.co/Qwen/Qwen3-Reranker-0.6B
- ggml-org Qwen3-Reranker GGUF: https://huggingface.co/ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF
- Qwen3-Reranker llama.cpp notes / broken GGUF warning: https://huggingface.co/Voodisss/Qwen3-Reranker-0.6B-GGUF-llama_cpp
- llama.cpp server README: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- jina-embeddings-v5-text: https://huggingface.co/jinaai/jina-embeddings-v5-text-small , https://jina.ai/news/jina-embeddings-v5-text-distilling-4b-quality-into-sub-1b-multilingual-embeddings
- jina-reranker-v3.5: https://jina.ai/models/jina-reranker-v3.5/ ; v3 paper https://arxiv.org/abs/2509.25085v4
- EmbeddingGemma: https://huggingface.co/google/embeddinggemma-300m
- Qwen3-VL-Embedding/Reranker: https://arxiv.org/abs/2601.04720v1
- zerank-2: https://huggingface.co/zeroentropy/zerank-2
- mxbai-rerank-v2: https://mixedbread.com/blog/mxbai-rerank-v2
- Reranker roundup (secondary, low confidence): https://local-ai-zone.github.io/guides/best-ai-reranker-models-ultimate-ranking-2026.html ; https://presenc.ai/research/best-open-weight-reranker-models-2026
- OpenSearch neural sparse v3: https://huggingface.co/opensearch-project/opensearch-neural-sparse-encoding-doc-v3-gte
- pg_search vs pg_textsearch benchmark (Sep 2026): https://particula.tech/blog/pg-search-vs-pg-textsearch-bm25-postgres
- pg_textsearch: https://tigerdata.com/blog/pg-textsearch-bm25-full-text-search-postgres
- Neon pg_search deprecation: https://neon.com/docs/extensions/migrate-pg-search-to-lakebase-text
- VectorChord-bm25 comparison: https://docs.pgedge.com/vchord-bm25/v0-3-0/comparison-to-other-solution-in-postgres/
- Postgres FTS extension licences: https://pigsty.io/ext/cate/fts/
- pgvector: https://pgxn.org/dist/vector
- Fusion functions (Bruch et al.): https://arxiv.org/abs/2210.11934v2
- Anthropic Contextual Retrieval: https://www.anthropic.com/news/contextual-retrieval
- Claude models overview: https://platform.claude.com/docs/en/models/overview
- Parsers comparison (Jul 2026): https://www.danilchenko.dev/posts/markitdown-vs-docling-vs-marker/
- PyLate / ModernColBERT: https://arxiv.org/pdf/2508.03555 , https://lighton.ai/lighton-blogs/lighton-releases-gte-moderncolbert-first-state-of-the-art-late-interaction-model-trained-on-pylate
- Ollama rerank (no native endpoint found): https://www.codewords.ai/blog/ollama-reranker
