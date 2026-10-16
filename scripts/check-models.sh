#!/usr/bin/env bash
# Functional checks of the native model servers (run on the Mac host).
#  - embeddings: vector of KB_EMBEDDING_DIM non-zero floats
#  - reranker:   the relevant passage scores > 0.5 AND beats the irrelevant ones by > 0.5.
#                Guards against GGUF conversions that lost the classification head: those
#                answer HTTP 200 with ~0.0 for everything and silently ruin ranking.
#  - chat:       both Ollama models are present and the router model answers.
# Usage: scripts/check-models.sh [--no-chat]
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
have jq || die "jq is required (brew install jq)"

EMBED_URL="${KB_CHECK_EMBED_URL:-http://127.0.0.1:8081}"
RERANK_URL="${KB_CHECK_RERANK_URL:-http://127.0.0.1:8082}"
LLM_URL="${KB_CHECK_LLM_URL:-http://127.0.0.1:11434}"
check_chat=1
[[ "${1:-}" == "--no-chat" ]] && check_chat=0
failed=0

post() { curl -fsS -m "${3:-60}" -H 'Content-Type: application/json' -d "$2" "$1"; }

# --- embeddings ----------------------------------------------------------------------------
dim="${KB_EMBEDDING_DIM:-1024}"
if resp="$(post "$EMBED_URL/v1/embeddings" '{"model":"Qwen3-Embedding-0.6B","input":["hello world"]}')" \
  && echo "$resp" | jq -e --argjson d "$dim" '.data[0].embedding as $e | ($e|length) == $d and ([$e[]|fabs]|add) > 0' >/dev/null; then
  ok "embeddings: $dim-dim vector from $EMBED_URL"
else
  warn "embeddings FAILED at $EMBED_URL (expected $dim dims). Response: ${resp:-<none>}" ; failed=1
fi

# --- reranker ------------------------------------------------------------------------------
req='{"model":"Qwen3-Reranker-0.6B","query":"What is the capital of France?","documents":[
  "Paris is the capital and most populous city of France.",
  "Mitochondria generate most of the chemical energy needed by a cell.",
  "Bananas are an excellent source of potassium."]}'
if resp="$(post "$RERANK_URL/v1/rerank" "$req")"; then
  scores="$(echo "$resp" | jq -c '[.results | sort_by(.index)[] | .relevance_score]')"
  if echo "$scores" | jq -e '.[0] > 0.5 and (.[0] - (.[1:] | max)) > 0.5' >/dev/null; then
    ok "reranker: discriminates (scores $scores)"
  else
    warn "reranker returned NON-DISCRIMINATIVE scores $scores - the GGUF probably lost its"
    warn "classification head. Re-download a verified conversion (KB_RERANK_GGUF_REPO, default"
    warn "ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF) and check llama.cpp is recent (brew upgrade llama.cpp)."
    failed=1
  fi
else
  warn "reranker FAILED: no answer from $RERANK_URL/v1/rerank"; failed=1
fi

# --- chat ----------------------------------------------------------------------------------
if ((check_chat)); then
  chat_model="${KB_LLM_MODEL:-qwen3.5:9b}" router_model="${KB_LLM_ROUTER_MODEL:-qwen3.5:4b}"
  if tags="$(curl -fsS -m 10 "$LLM_URL/api/tags")"; then
    for m in "$chat_model" "$router_model"; do
      if echo "$tags" | jq -e --arg m "$m" '[.models[].name] | index($m) != null' >/dev/null; then
        ok "ollama: model $m present"
      else
        warn "ollama: model $m missing (ollama pull $m)"; failed=1
      fi
    done
    body="$(jq -nc --arg m "$router_model" '{model:$m, max_tokens:256, messages:[{role:"user",content:"Reply with the single word: pong"}]}')"
    if post "$LLM_URL/v1/chat/completions" "$body" 180 | jq -e '.choices[0].message' >/dev/null; then
      ok "chat: $router_model answers"
    else
      warn "chat FAILED for $router_model at $LLM_URL"; failed=1
    fi
  else
    warn "ollama not answering at $LLM_URL"; failed=1
  fi
fi

((failed == 0)) || die "model checks failed"
ok "all model checks passed"
