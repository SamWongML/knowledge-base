# 0004. Agentic search: route first, bounded loop, grounded streaming answers

- Status: accepted
- Date: 2026-10-16
- Related: [research §2](../research/README.md#2-agentic-search-with-small-local-models), [raw/02](../research/raw/02-agentic-search.md), `kb.agent.loop`, `AskRequest` / `AgentStep` / `AskResponse` in `kb/schemas.py`

## Context

The local agent models are 4B and 9B, so their tool calling is "marginal" to "usable" (BFCL-v4 0.50 and 0.66). Evidence from 2026 shows that agentic query rewriting helps (+2.8 nDCG@10). Extra retrieval rounds add nothing over a single pass plus a reranker, and unnecessary retrieval hurts ([arXiv 2601.07711](https://alphaxiv.org/overview/2601.07711v1)). Small models are reliable as **schema-constrained routers and graders** ([Tiny-Critic RAG](https://arxiv.org/abs/2603.00846)) and fragile at free-form ReAct. Closed-book hallucination runs at about 80%.

## Decision

`ask` / `ask_stream` implement a fixed pipeline with a bounded escalation loop:

1. **Route** (router model, JSON schema). The route is one of `chitchat` (reply without retrieval), `lookup` (direct title or id), `simple` or `agentic`.
2. **Multi-query.** 2–3 rewrites plus the original query, each run through the [retrieval cascade](0002-hybrid-retrieval-cascade.md), then fused with RRF.
3. **Sufficiency check** (grader, JSON `{sufficient, missing}`) over the top evidence. If it says sufficient, go straight to synthesis.
4. Otherwise run a **bounded loop**. Each step is one schema-constrained next action (`search` with a new query, or `read` a document section).
   - Already-seen chunks are de-duplicated.
   - The loop stops when the grader says sufficient, at **`KB_AGENT_MAX_STEPS=4`** (the request may raise it, capped at 12), when two consecutive steps add no new chunks, or when the time budget runs out.
5. **Grounded synthesis.** Evidence is numbered `[1]..[n]` and every claim must cite. Citations are validated against the evidence set, and invalid ones are dropped. If evidence is insufficient, the answer is "not found in the knowledge base" plus the best sources.
6. **Streaming.** Each phase emits an `AgentStep` (`plan`, `search`, `read`, `reflect`, `answer_delta`), and the stream ends with `AskResponse`. Over SSE these are `event: step` … then `event: done`.

Guardrails:
- Non-thinking mode, with temperature at most 0.2.
- One repair retry on malformed JSON, then a deterministic fallback.
- Read-only, whitelisted tools.
- Retrieved text is treated as untrusted data and kept out of system prompts.
- About 6K evidence tokens per step.
- `engine=cloud` routes synthesis to `claude-haiku-5-5` (opt-in).

## Alternatives considered

| Option | Why not |
|---|---|
| Always-agentic ReAct loop | 2.7–3.9× the tokens for no recall gain, and fragile with 4–9B models. |
| Self-RAG / Search-R1 policies | Require specific fine-tuned checkpoints. |
| Open-ended deep research | Small models repeat searches and stop at the wrong time. |
| Native `tools` + `tool_choice` | Works on Ollama, but a single "next action" JSON schema is more portable and easier to validate. |
| Haiku for everything | Costs money, sends data off-box and adds a network dependency. Kept as opt-in. |

## Consequences

- Most questions cost one router call, one cascade run, one grader call and one synthesis. The loop runs only for multi-hop or comparative questions.
- Latency is bounded and predictable, and the UI can show a meaningful step trace.
- Answers are auditable: every sentence maps to a chunk the reader can open.
- We owe a 50–100 question golden set covering faithfulness, citation precision, steps per answer and route accuracy, plus A/B runs of the agent models.
