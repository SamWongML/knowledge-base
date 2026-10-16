# 0003. Model choices and native local serving (llama.cpp for embed/rerank, Ollama for chat)

- Status: accepted
- Date: 2026-10-16
- Related: [research §1, §2, §6](../research/README.md), [raw/01 §2, §5](../research/raw/01-retrieval-reranking.md), [raw/02 §1–2](../research/raw/02-agentic-search.md), [raw/06 §1–2](../research/raw/06-deployment.md), `kb/config.py`, `kb/providers/`

## Context

Every model stage must run locally on Apple Silicon (32 GB minimum) with permissive licences. A cloud model may be used optionally, never as a requirement. macOS container runtimes are Linux VMs with no Metal access: Vulkan-in-krunkit reaches only about 50–57% of native llama.cpp throughput ([llama.cpp #12985](https://github.com/ggml-org/llama.cpp/discussions/12985)). Ollama has no rerank endpoint.

## Decision

| Role | Model | Server | Endpoint |
|---|---|---|---|
| Embeddings | **Qwen3-Embedding-0.6B** Q8_0 GGUF (Apache-2.0), 1024-d stored as `halfvec`, query instruction prefix | `llama-server --embedding --pooling last` | `127.0.0.1:8081/v1/embeddings` |
| Rerank | **Qwen3-Reranker-0.6B**, the ggml-org Q8_0 GGUF (has the classifier tensors) | `llama-server --reranking --pooling rank` | `127.0.0.1:8082/v1/rerank` |
| Agent / synthesis | **qwen3.5:9b** (BFCL-v4 0.661) | **Ollama** (MLX backend, `KEEP_ALIVE=-1`, `CONTEXT_LENGTH=16384`, `NUM_PARALLEL=2`) | `127.0.0.1:11434/v1/chat/completions` |
| Router / grader / rewriter | **qwen3.5:4b** | Ollama | same |
| Optional cloud | **claude-haiku-5-5** | Anthropic API | LLM-judge rerank, contextual chunk headers, `engine=cloud` answers |

- Model servers run **natively under launchd**, not in containers, so they get Metal.
- The app talks only to OpenAI-compatible HTTP (plus `/v1/rerank`). Every endpoint, model name and dimension is a `KB_*` setting, so switching to LM Studio, mlx-lm, vllm-mlx or a third llama-server is a configuration change. Tests use deterministic fakes.
- Every provider has a timeout and a circuit breaker ([architecture §6](../architecture.md#6-concurrency-and-availability)).
- A startup smoke test should check that the reranker scores a known relevant pair above 0.5. Broken GGUFs return 200 with scores near 0.

## Alternatives considered

| Option | Why not |
|---|---|
| Chat on a third llama-server (deployment report) | Viable and gives more GBNF control. Ollama gives faster MLX decode, schema-constrained JSON, OpenAI tool calls and simpler model management. Kept as the fallback. |
| Ollama for embeddings and rerank too | No rerank endpoint, and its embedding pooling is less controllable. |
| Models in containers (Podman/krunkit) | About half the throughput. |
| jina-embeddings-v5-small / jina-reranker-v3.5 | Higher scores, but CC-BY-NC. |
| Qwen3-Embedding-4B / Qwen3-Reranker-4B | +6 retrieval points / +4 MTEB-R at 3–7× the cost. These are the upgrade path, via MRL truncation to 1024 for the embedder. |
| qwen3.6:35b (MoE, about 3B active) | Better agent at similar speed, but needs about 24 GB resident. Recommended on 48 GB+ M4 Pro machines. |
| EmbeddingGemma-300m | Gated licence and 2K context. |

## Consequences

- Resident memory is about 3 GB for embed and rerank plus about 10 GB for the two chat models. That fits 32 GB alongside Postgres.
- launchd plists, model downloads and Ollama version pinning are now ours to own; the Ollama tool-streaming format has changed between releases.
- Changing the embedding model requires re-embedding and possibly a dimension migration. Store the model id with the data when that happens.
- Small models hallucinate closed-book, which motivates the grounding rules in [ADR 0004](0004-agentic-search.md).
