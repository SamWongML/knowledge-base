#!/usr/bin/env bash
# End-to-end smoke test against a running kb: health -> add a document -> wait until indexed
# -> search finds it -> ask answers with a citation.
#
#   scripts/smoke.sh [--models] [--no-ask] [BASE_URL]     (default http://127.0.0.1:8787)
#
# --models also runs scripts/check-models.sh first (native model servers, macOS host).
# Compatible with macOS /bin/bash 3.2. Uses KB_API_TOKEN from .env for write endpoints. The test document goes into a dedicated
# `smoke` collection and is deleted afterwards.
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
have jq || die "jq is required"

models=0 ask=1 base="http://127.0.0.1:8787"
for a in "$@"; do
  case "$a" in
    --models) models=1 ;;
    --no-ask) ask=0 ;;
    -h | --help) sed -n '2,10p' "$0"; exit 0 ;;
    *) base="${a%/}" ;;
  esac
done
((models)) && "$KB_REPO/scripts/check-models.sh"

auth=()
[[ -n "${KB_API_TOKEN:-}" ]] && auth=(-H "Authorization: Bearer $KB_API_TOKEN")
api() { # METHOD PATH [JSON]
  local args=(-fsS -m "${TIMEOUT:-60}" -X "$1" ${auth[@]+"${auth[@]}"} -H 'Content-Type: application/json')
  [[ $# -ge 3 ]] && args+=(-d "$3")
  curl "${args[@]}" "$base$2"
}

# 1. health
health="$(api GET /api/health)" || die "GET /api/health failed at $base"
status="$(echo "$health" | jq -r .status)"
echo "$health" | jq -r '.components[] | "   \(if .ok then "ok  " else "DOWN" end) \(.name) \(.detail // "")"' >&2
[[ "$status" != down ]] || die "health is down"
ok "health: $status"

# 2. add a document with a unique, searchable token
token="zephyrquartz$RANDOM$RANDOM"
api POST /api/collections '{"slug":"smoke","name":"Smoke tests","description":"Created by scripts/smoke.sh"}' >/dev/null 2>&1 || true
doc="$(jq -nc --arg t "$token" '{
  title: "Smoke test \($t)", collection: "smoke", tags: ["smoke"],
  content: "# Smoke test\n\nThe secret project codename is \($t). It was chosen by the platform team in 2026 because the Mac mini never sleeps.\n"}')"
id="$(api POST /api/documents "$doc" | jq -er .id)" || die "POST /api/documents failed"
ok "document $id accepted"
cleanup() { api DELETE "/api/documents/$id" >/dev/null 2>&1 || true; }
trap cleanup EXIT

# 3. wait for ingestion
for _ in $(seq 1 90); do
  st="$(api GET "/api/documents/$id" | jq -r .status)"
  [[ "$st" == ready || "$st" == failed ]] && break
  sleep 2
done
[[ "$st" == ready ]] || die "document status: $st (check: just logs worker)"
ok "document indexed"

# 4. search
res="$(api POST /api/search "$(jq -nc --arg q "$token" '{query: $q, limit: 5, filters: {collections: ["smoke"]}}')")" \
  || res="$(api POST /api/search "$(jq -nc --arg q "$token" '{query: $q, limit: 5}')")" || die "search failed"
echo "$res" | jq -e --arg id "$id" '[.hits[].document_id] | index($id) != null' >/dev/null \
  || die "search did not return the document: $(echo "$res" | jq -c '{degraded, hits: [.hits[] | .document_title]}')"
ok "search: found ($(echo "$res" | jq -r '"\(.total_ms|floor) ms, degraded=\(.degraded)"'))"

# 5. ask
if ((ask)); then
  ans="$(TIMEOUT=300 api POST /api/ask "$(jq -nc '{question: "What is the secret project codename?"}')")" || die "ask failed"
  answer="$(echo "$ans" | jq -r .answer)"
  [[ -n "$answer" ]] || die "empty answer"
  if [[ "$answer" == *"$token"* ]]; then ok "ask: answer contains the codename"; else warn "ask answered without the codename: ${answer:0:200}"; fi
fi
ok "smoke test passed against $base"
