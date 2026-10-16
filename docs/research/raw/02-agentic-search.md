# 02 — Agentic Search with a Small Local Model (Mac mini M4 / M4 Pro)

Research date: 2026-10-16. Third-party benchmark numbers vary a lot between sources. Items marked *(verify)* come from secondary sources or could not be confirmed against a primary page.

## 1. Small local LLMs for tool calling (late 2026)

**What has shipped (open weights).**
- **Qwen3.5** small dense models (0.8B/2B/4B/9B) came out in early March 2026. They are Apache-2.0, have 262K context, hybrid thinking/non-thinking modes and native vision. Larger siblings: 27B dense and 35B-A3B MoE. ([Artificial Analysis](https://artificialanalysis.ai/articles/qwen3-5-small-models))
- **Qwen3.6** (April 2026) shipped only 27B dense and 35B-A3B MoE as open weights. Ollama tags are `qwen3.6:35b` (24 GB, the default/latest) and `qwen3.6:27b` (17 GB), plus MLX-native variants such as `qwen3.6:35b-a3b-coding-nvfp4` (22 GB). Context is 256K. ([Ollama library](https://registry.ollama.ai/library/qwen3.6), [dsebastien](https://www.dsebastien.net/qwen3-6-35b-a3b/))
- **Qwen3.8-27B** (dense) was released 2026-08-14 as `Qwen/Qwen3.8-27B`. Its card claims stronger agent planning but gives no tool-calling numbers. Qwen3.7 is API-only. ([QwenLM/Qwen3.8](https://github.com/QwenLM/Qwen3.8), [betterclaw](https://www.betterclaw.io/blog/qwen-3-7-ollama-setup-config))
- **Gemma 4** (2026-04-02): E2B, E4B (`gemma4`, the default tag), 26B MoE with about 4B active (`gemma4:26b`), and 31B dense. The release claims native function calling. ([Codersera](https://codersera.com/blog/how-to-run-gemma-3-on-a-mac-a-comprehensive-guide/amp/)) Gemma 3 and 3n have **no native tool template**, so avoid them for tools unless you use grammar decoding. ([d-central](https://d-central.tech/local-llm-agent-capability/))
- **gpt-oss-20b** (`gpt-oss:20b`, 14 GB, Apache-2.0) is a reasoning/agentic MoE. **Phi-4-mini** (3.8B, MIT) has native tool tokens. **IBM Granite 4.x** 8B scores BFCL-v3 68.3%. ([d-central](https://d-central.tech/local-llm-agent-capability/), [morphllm](https://www.morphllm.com/best-ollama-models))

**Tool-calling reliability.** BFCL-v4 as of 2026-07-18 ([d-central](https://d-central.tech/local-llm-agent-capability/)):

| Model | BFCL-v4 |
|---|---|
| Qwen3.5-27B | 0.685 |
| Qwen3.5-9B | 0.661 |
| Qwen3.5-4B | 0.503 |
| Qwen3.5-2B | 0.436 |

The same source rates 9B as "usable", 4B as "marginal" and ≤2B as "unreliable". It names **Qwen3-4B-Instruct-2507** as the smallest Qwen it trusts for tools. Its advice is to use the native tool template **plus** grammar-constrained decoding (GBNF/XGrammar/Outlines). Grammar only guarantees valid structure, not a correct choice of tool, but it is what makes small models usable at all.

A Chinese May-2026 ranking of models under 10B active parameters puts Qwen3.6-35B-A3B first, then Qwen3.5-9B, Qwen3.5-4B, Gemma 3n E4B, GLM-4-9B and Phi-4-mini ([CSDN](https://gitcode.csdn.net/69ff36a90a2f6a37c5a8c614.html)). The ranking is subjective. Small Qwen3.5 models also burn many reasoning tokens and hallucinate badly on closed-book facts (AA-Omniscience: about 80% hallucination rate for 4B/9B). That makes grounding plus non-thinking mode important.

**Speed on M4-class Macs.** Decode speed is memory-bandwidth bound: M4 has 120 GB/s, M4 Pro 273 GB/s.

| Model / setup | Hardware | Speed | Source |
|---|---|---|---|
| Qwen3.5-35B-A3B, oMLX 4-bit | M4 Pro 48 GB | 85 tok/s decode at 1K ctx, 77 at 4K; prefill about 900 tok/s; 20 GB peak RAM | [oMLX](https://omlx.ai/benchmarks/tf6eke0i) |
| Qwen3.6-35B-A3B, 4-bit | M4 Pro | 47–71 tok/s decode | [hardware-corner](https://www.hardware-corner.net/is-mac-mini-m4-pro-good-for-llms/) |
| gpt-oss-20b, 4-bit | M4 Pro | 54 tok/s decode at 16K ctx (43 at 32K); prefill 713 tok/s | [hardware-corner](https://www.hardware-corner.net/is-mac-mini-m4-pro-good-for-llms/) |
| Qwen3.6-27B dense, 8-bit | M4 Pro | about 8 tok/s | [hardware-corner](https://www.hardware-corner.net/is-mac-mini-m4-pro-good-for-llms/) |
| Qwen3.5-9B, Q4_K_M | M4 / M4 Pro | theoretical ceiling about 22 / 50 tok/s; MLX 15–30% faster decode than llama.cpp | [techplained](https://www.techplained.com/qwen-3-5-apple-silicon) |

The 27B dense model is too slow for an interactive loop. **On M4 Pro, MoE models with about 3B active parameters (Qwen3.6-35B-A3B, Gemma-4-26B, gpt-oss-20b) are the sweet spot**: near-27B quality at 4B-class speed. They need about 20–24 GB resident, so on a 32 GB base M4 use a 4B–9B dense model instead.

Prefill speed matters more than decode for RAG. At about 900 tok/s, an 8K-token evidence prompt costs about 9 s, so keep per-step context small.

## 2. Serving on macOS

| Server | OpenAI `/v1/chat/completions` | Tool calls | JSON-schema structured output | Notes |
|---|---|---|---|---|
| **Ollama** | yes (`:11434/v1`) | yes (OpenAI format) | yes (`format`/`response_format`, grammar-constrained) | MLX backend since 0.19 (Mar 2026). On M5: 58→112–134 tok/s decode vs llama.cpp for Qwen3.5-35B-A3B; MLX path wants ≥32 GB. Default context is only 4096, so set `OLLAMA_CONTEXT_LENGTH`. Streaming chunk shape changed in 0.32.6 (Aug 2026). ([lilting](https://lilting.ch/en/articles/ollama-mlx-apple-silicon-preview), [morphllm](https://www.morphllm.com/best-ollama-models), [Ollama blog](https://registry.ollama.ai/blog/structured-outputs)) |
| **LM Studio** (MLX + GGUF) | yes | yes | yes. GGUF uses llama.cpp grammars; MLX uses Outlines | Headless via `lms server start` / llmster. Warns that models under 7B are often poor at structured output. ([LM Studio docs](https://www.lmstudio.ai/docs/developer/openai-compat/structured-output)) |
| **llama.cpp `llama-server`** | yes | yes, with `--jinja` (model chat template) | yes (`response_format: json_schema` → GBNF). Complex schema features may not convert. | Most control: grammar, `chat_template_kwargs: {enable_thinking:false}`, slots/prompt cache. ([simplified.guide](https://www.simplified.guide/_export/xhtml/llama-cpp/server-constrain-json-schema), [Unsloth](https://unsloth.ai/docs/jp/ji-ben/inference-and-deployment/llama-server-and-openai-endpoint.md)) |
| **mlx-lm server** | yes | basic tool-call parsing | no native schema constraint (needs add-ons such as oMLX/Outlines) | Fastest raw MLX, but the thinnest API. ([oMLX](https://instagit.com/jundot/omlx/how-omlx-implements-tool-calling-structured-output)) |

**Recommendation:** use **Ollama** as the always-on service.
- Run it as a launchd service (`brew services start ollama`).
- Set `OLLAMA_KEEP_ALIVE=-1`, `OLLAMA_CONTEXT_LENGTH=16384`, `OLLAMA_NUM_PARALLEL=2` and `OLLAMA_FLASH_ATTENTION=1`.
- It gives the MLX speedup, schema-constrained JSON, OpenAI-compatible tools and simple model management.
- Keep llama-server as a fallback when you need exact GBNF control or a model Ollama mis-templates.
- **Pin the Ollama version.** The tool-call streaming format has changed between releases.

## 3. Agentic RAG techniques: what holds up with 4–9B models

**Evidence on when agentic RAG pays off.**
- *Is Agentic RAG worth it?* (Jan 2026, Qwen3 0.6–32B generators):
  - Agentic query rewriting gave **+2.8 NDCG@10** over enhanced RAG.
  - Extra retrieval rounds gave **no gain** over a single pass plus a reranker.
  - Unnecessary retrieval cost 49 F1 points on FEVER.
  - Cost was 2.7–3.9× the input tokens and 1.4–1.5× the latency.

  Conclusion: **route first and loop rarely**. ([arXiv 2601.07711](https://alphaxiv.org/overview/2601.07711v1))
- *Tiny-Critic RAG* (2026): a LoRA-tuned SLM used as a binary **sufficiency/routing gate**, with constrained decoding and no thinking, matches GPT-4o-mini routing at about 10× lower latency. Small models are good **graders and routers**. ([arXiv 2603.00846](https://arxiv.org/abs/2603.00846))
- *Test-time strategies for agentic RAG* (ACL SRW 2026), on Search-R1:
  - **De-duplicating already-seen documents** and a contextualization step gave +5.6% EM and −10.5% retrieval turns.

  ([Adobe Research](https://research.adobe.com/publication/test-time-strategies-for-more-efficient-and-accurate-agentic-rag/))

**Robustness by technique with 4–9B models.**
- **Robust:**
  - Multi-query expansion (3–4 paraphrases plus keyword variants), fused with RRF.
  - Query decomposition into ≤3 sub-questions as a JSON list.
  - Binary or 3-way evidence grading per chunk (CRAG-style: correct / ambiguous / incorrect).
  - A yes/no **sufficiency check** with a "missing" field.
  - Routing classification.

  All of these are short, schema-constrained, non-thinking outputs.
- **Moderately robust:**
  - HyDE: helps on vocabulary mismatch, but the hallucinated passage can drift. Use it only as one extra query in the fusion set, never alone.
  - An iterative retrieve → read → reflect loop with a **hard step budget** (≤4).
- **Fragile:**
  - Free-form ReAct with long scratchpads.
  - Self-RAG reflection tokens, which need a fine-tuned model.
  - Search-R1/ReSearch-style RL policies, unless you run those exact fine-tuned checkpoints.
  - Open-ended DeepResearch loops: small models repeat searches, lose track of the goal and stop too early or too late.
- **Citation-grounded synthesis:** pass numbered evidence snippets `[1]..[n]` and require every sentence to cite. Validate citations programmatically (each cited id exists and the quoted span is in the chunk); drop or flag failures. The 9B/35B-A3B models handle this. The 4B models are borderline, so escalate synthesis to Haiku when quality matters.
- **Stop criteria:** stop when any of these holds:
  - The sufficiency grader says yes.
  - The step budget is hit.
  - The token/time budget is hit.
  - Two consecutive steps add no new document ids (novelty = 0).
  - The model emits `answer`.
- **When NOT to do agentic search:**
  - Navigational or known-item queries (a title or ID): use direct lookup.
  - Short keyword queries where the top-1 hybrid score is high and the margin is large.
  - Chit-chat and meta questions.

  Default to **single-shot hybrid retrieval plus rerank**. Escalate to the loop only for multi-hop, comparative or "summarize everything about X" questions, or when the first-pass sufficiency check fails.

## 4. Concrete loop design

**Tools exposed to the local agent.** Keep them few, with flat JSON arguments.
- `search(query: str, k: int=8, filters?: {tags, date_from, date_to, source})` → `[{doc_id, chunk_id, title, snippet(≤300 chars), score}]`. Hybrid BM25 + vector with RRF; already-seen chunks are de-duplicated server-side.
- `open_document(doc_id, section?: str, max_chars=4000)` → text window plus outline.
- `list_related(doc_id, k=5)` → backlinks, links and nearest neighbours by embedding.
- `answer(text, citations: [chunk_id])`, which is terminal.
- Optional: `lookup(title_or_id)` for known-item queries.

**Planning format.** Use non-thinking mode and constrain every step with a JSON schema rather than free-text ReAct:

```json
{"thought": "<=40 words", "action": "search|open_document|list_related|answer",
 "args": {...}, "sufficient": false, "missing": "what is still unknown"}
```

Native `tools` with `tool_choice: "required"` also works on Ollama and llama-server. A single "next action" schema is the most portable option and is easier to validate. On a parse error, retry once with the error message, then fall back to a deterministic `search(original_query)`.

**Budgets:**
- `max_steps=4` (6 for a "deep" mode).
- `max_open_docs=3`.
- Context packer ≤6K tokens of evidence: keep the top chunks by reranker score and summarize older observations into one line each.
- Wall-clock ≤20 s local; Haiku fallback allowed ≤1 call per query.

**Streaming to the UI (SSE).** Use `text/event-stream` with typed events:
- `event: plan` (sub-questions)
- `event: step` `{n, action, args}`
- `event: results` `{doc ids, titles, scores}`
- `event: grade` `{sufficient, missing}`
- `event: token` (answer deltas)
- `event: citations`
- `event: done` `{steps, ms, model, escalated}`
- `event: error`

Send a heartbeat comment every 15 s. Each event carries an `id` so the client can resume with `Last-Event-ID`.

**Caching (layered):**
1. Embedding cache keyed by `sha256(model + text)`.
2. Search-result cache keyed by `(normalized_query, filters, index_version)`, with a 24 h TTL or invalidation on reindex.
3. LLM step cache keyed by `hash(model, prompt, schema)`, for rewrites and grades at temperature 0.
4. Final-answer cache keyed by `(normalized_question, index_version)`.
5. Ollama/llama-server KV prompt-prefix reuse: keep the system prompt and tool schemas byte-identical and first in every call.
6. Anthropic prompt caching for the Haiku system prompt and tools (cache reads are $0.01/MTok on Haiku 5.5).

## 5. Claude Haiku (from Anthropic docs)

- **Current model: Claude Haiku 5.5**, id **`claude-haiku-5-5`** (no date suffix). 1M context, 128K max output, Jun-2026 knowledge cutoff, default effort `medium`.
- **Pricing per MTok:**

  | Prompt size | Input | Output | Cache write (5 min) | Cache read | Batch |
  |---|---|---|---|---|---|
  | ≤100K prompt tokens | $0.10 | $0.50 | $0.125 | $0.01 | 50% off |
  | >100K prompt tokens | $0.50 | $2.50 | — | — | — |

  ([models overview](https://platform.claude.com/docs/en/models/overview), [pricing](https://platform.claude.com/docs/en/about-claude/pricing))
- **Migration notes:**
  - Uses adaptive thinking (`thinking: {type: "adaptive"}` plus `output_config.effort`). `budget_tokens` returns a 400 error.
  - `temperature` must be 1 if sent. `top_k` is rejected. Assistant prefill is rejected.
  - Forced `tool_choice` returns no thinking block.
  - The tokenizer produces about 30% more tokens than Haiku 4.5.
  - Handle `stop_reason: "refusal"`.

  ([migration guide](https://platform.claude.com/docs/en/models/haiku-5-5/migration-guide))
- **Haiku 4.5** (`claude-haiku-4-5-20251001`, $1/$5) is legacy, with retirement "not sooner than Oct 15, 2026". Don't build on it. ([Haiku 4.5 overview](https://platform.claude.com/docs/en/models/haiku-4-5/overview))
- **Fit:** at these prices Haiku 5.5 is cheap enough to act as a **listwise reranker** (30 snippets ≈ 4K tokens ≈ $0.0004) and as the **escalation answerer**. A typical 8K-in/600-out synthesis costs about $0.001. Use `effort: "low"` for reranking and grading and `"medium"` for synthesis. Use structured outputs or forced tool use for rerank JSON.

---

## RECOMMENDED DESIGN

**Models.** Ollama tags and HF ids; verify tag names with `ollama show` before pinning.

| Role | M4 Pro 48–64 GB | Base M4 32 GB |
|---|---|---|
| Agent / synthesizer | `qwen3.6:35b` (HF `Qwen/Qwen3.6-35B-A3B`; MLX `qwen3.6:35b-a3b-coding-nvfp4` *(verify non-coding MLX tag)*) | `qwen3.5:9b` (HF `Qwen/Qwen3.5-9B`) |
| Router / grader / rewriter | `qwen3.5:4b` (HF `Qwen/Qwen3.5-4B`) or `qwen3:4b-instruct-2507` (HF `Qwen/Qwen3-4B-Instruct-2507`) *(verify tag)* | same |
| Alternates to A/B | `gpt-oss:20b` (HF `openai/gpt-oss-20b`), `gemma4:26b` | `gemma4` (E4B), `phi4-mini` |
| Cloud fallback | `claude-haiku-5-5` (rerank + hard synthesis) | same |

Run all local calls in **non-thinking mode** (`/no_think` or `enable_thinking:false`) at temperature 0–0.2. The JSON schema is enforced by the server.

**Serving.** Ollama on launchd, pinned version, MLX backend, `OLLAMA_KEEP_ALIVE=-1`, `OLLAMA_CONTEXT_LENGTH=16384`, `OLLAMA_NUM_PARALLEL=2`. Keep both the agent and router models resident (about 24 GB + 3 GB). The app talks only to `/v1/chat/completions` through one OpenAI-compatible client, so swapping to llama-server or LM Studio is a config change.

**Loop pseudocode.**
```
def ask(q, mode="auto"):
    emit("start")
    if cached := answer_cache.get(norm(q), index_ver): return stream(cached)
    route = router.json(ROUTE_SCHEMA, q)        # lookup | simple | agentic | chitchat
    if route == "lookup":   return direct_lookup(q)
    hits = hybrid_search(multi_query(q) + [q])   # 3 rewrites (+HyDE opt.), RRF, dedup
    hits = rerank(q, hits)                       # local cross-encoder; Haiku if enabled
    emit("results", hits)
    g = grader.json(SUFFICIENCY_SCHEMA, q, top(hits, 8))
    if route == "simple" or g.sufficient:
        return synthesize(q, hits)
    seen, evidence, steps = ids(hits), top(hits, 8), 0
    while steps < MAX_STEPS and time_left() and tokens_left():
        act = agent.json(NEXT_ACTION_SCHEMA, q, g.missing, pack(evidence, 6000))
        emit("step", act); steps += 1
        if act.action == "answer": break
        obs = TOOLS[act.action](**act.args, exclude=seen)
        new = [o for o in obs if o.id not in seen]
        if not new: stall += 1
        if stall >= 2: break
        seen |= ids(new); evidence = rerank(q, evidence + new)[:12]
        g = grader.json(SUFFICIENCY_SCHEMA, q, evidence); emit("grade", g)
        if g.sufficient: break
    if not g.sufficient and haiku_enabled and budget_ok(): escalate = True
    return synthesize(q, evidence, model="claude-haiku-5-5" if escalate else LOCAL)

def synthesize(q, ev, model):
    stream answer with [n] citations; validate each cite ∈ ev and span overlap;
    if no valid citations or grader says insufficient -> answer "not found in KB" + best sources
    answer_cache.put(...); emit("citations"); emit("done")
```

**Guardrails.**
1. Hard caps: 4 steps (6 in deep mode), 3 opened docs, about 6K evidence tokens per step, 20 s local wall-clock, ≤1 Haiku call per query with a daily $ ceiling.
2. Schema-constrained JSON on every model call. One repair retry, then a deterministic fallback.
3. Tool arguments are validated and whitelisted. Tools are read-only. `doc_id` must exist. No filesystem paths or URLs from the model.
4. Retrieved text is untrusted data. Wrap it in delimiters and strip or ignore instructions inside documents (prompt-injection defense). Tool outputs never reach system prompts.
5. Answers must cite. Uncited claims are removed or flagged. If evidence is insufficient, say "not found in the knowledge base" rather than drawing on parametric memory; small Qwen models hallucinate about 80% closed-book.
6. Loop detectors: an identical query twice, novelty = 0 for two steps, or alternating opens of the same doc all force synthesis.
7. Log every trace (route, steps, ids, latencies, escalations) for offline eval. Build a 50–100 question golden set and A/B the agent models and the route thresholds, since published small-model benchmarks are noisy.
8. Privacy toggle: Haiku escalation is off by default per collection, and only snippets (never whole docs) are sent.
