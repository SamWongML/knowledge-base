# shellcheck shell=bash
# Shared helpers for kb operator scripts. Source it; do not execute it.

set -euo pipefail

KB_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KB_HOME="${KB_HOME:-$HOME/.kb}"
KB_LOG_DIR="${KB_LOG_DIR:-$HOME/Library/Logs/kb}"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
export KB_REPO KB_HOME KB_LOG_DIR LAUNCH_AGENTS

if [[ -t 2 ]]; then
  _c_blue=$'\033[1;34m' _c_yellow=$'\033[1;33m' _c_red=$'\033[1;31m' _c_green=$'\033[1;32m' _c_off=$'\033[0m'
else
  _c_blue='' _c_yellow='' _c_red='' _c_green='' _c_off=''
fi
log()  { printf '%s==>%s %s\n' "$_c_blue" "$_c_off" "$*" >&2; }
ok()   { printf '%s ok%s %s\n' "$_c_green" "$_c_off" "$*" >&2; }
warn() { printf '%swarn:%s %s\n' "$_c_yellow" "$_c_off" "$*" >&2; }
die()  { printf '%serror:%s %s\n' "$_c_red" "$_c_off" "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }
is_macos() { [[ "$(uname -s)" == "Darwin" ]]; }

# Export every variable from the repo's .env (bash-compatible KEY=value lines).
load_env() {
  if [[ -f "$KB_REPO/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    . "$KB_REPO/.env"
    set +a
  fi
}

# Settings for processes that run on the host (native mode, `just dev`): the .env points model
# URLs at host.docker.internal for the containers; on the host they are plain localhost.
native_env() {
  load_env
  local var
  for var in KB_EMBEDDING_BASE_URL KB_RERANK_BASE_URL KB_LLM_BASE_URL; do
    if [[ -n "${!var:-}" ]]; then
      printf -v "$var" '%s' "${!var//host.docker.internal/127.0.0.1}"
      export "${var?}"
    fi
  done
  export KB_DATABASE_URL="${KB_NATIVE_DATABASE_URL:-postgresql://kb:${POSTGRES_PASSWORD:-kb}@127.0.0.1:${KB_PG_PORT:-5432}/kb}"
  export KB_HOST="${KB_NATIVE_HOST:-127.0.0.1}"
  export KB_STATIC_DIR="${KB_NATIVE_STATIC_DIR:-$KB_REPO/frontend/dist}"
}

# Honours COMPOSE_FILE (e.g. to add a compose.override.yaml) and otherwise uses the repo's file.
compose() {
  if [[ -n "${COMPOSE_FILE:-}" ]]; then
    docker compose --project-directory "$KB_REPO" "$@"
  else
    docker compose --project-directory "$KB_REPO" -f "$KB_REPO/compose.yaml" "$@"
  fi
}

# "compose" when the db container is running, otherwise "native" (Postgres.app).
# Override with KB_DEPLOY=compose|native.
deploy_mode() {
  if [[ -n "${KB_DEPLOY:-}" ]]; then
    echo "$KB_DEPLOY"
  elif have docker && [[ -n "$(compose ps --status running -q db 2>/dev/null)" ]]; then
    echo compose
  else
    echo native
  fi
}

# Connection URI for database $1 on the native server (KB_DATABASE_URL with the path swapped).
native_db_url() {
  local base="${KB_DATABASE_URL%%\?*}"
  echo "${base%/*}/$1"
}

# Wait until URL answers 2xx, up to $2 seconds (default 120).
wait_http() {
  local url="$1" timeout="${2:-120}" waited=0
  until curl -fsS -m 5 -o /dev/null "$url" 2>/dev/null; do
    ((waited >= timeout)) && return 1
    sleep 2
    waited=$((waited + 2))
  done
}
