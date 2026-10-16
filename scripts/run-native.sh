#!/usr/bin/env bash
# Run a kb backend command on the host (fully-native install or `just dev`), e.g.
#   scripts/run-native.sh kb-api
#   scripts/run-native.sh kb-worker
#   scripts/run-native.sh uvicorn kb.api.app:create_app --factory --reload --port 8787
# Loads .env, rewrites host.docker.internal -> 127.0.0.1, then `uv run`s inside backend/.
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

(($# > 0)) || die "usage: $0 <command> [args...]"
native_env
cd "$KB_REPO/backend" || exit 1
exec uv run --frozen --extra parsing "$@"
