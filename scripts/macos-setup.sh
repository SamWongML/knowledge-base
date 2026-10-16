#!/usr/bin/env bash
# One-time (and idempotent: safe to re-run) host setup for kb on an Apple Silicon Mac mini.
#
#   scripts/macos-setup.sh [--native] [--skip-pmset] [--skip-models] [--no-ollama-agent]
#
#   --native           fully-native install: also installs com.kb.api / com.kb.worker LaunchAgents
#                      (uv run kb-api / kb-worker against Postgres.app 18 + pg_search); no Docker.
#   --skip-pmset       do not touch power settings (no sudo).
#   --skip-models      do not download GGUFs / pull Ollama models.
#   --no-ollama-agent  you run Ollama.app or `brew services start ollama` yourself.
#
# Steps: checks -> brew packages -> power settings -> ~/.kb dirs + .env -> GGUF downloads ->
# launchd agents -> Ollama models -> model smoke test (reranker must discriminate).
# Runs with macOS /bin/bash 3.2.
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

native=0 pmset=1 models=1 ollama_agent=1
for a in "$@"; do
  case "$a" in
    --native) native=1 ;;
    --skip-pmset) pmset=0 ;;
    --skip-models) models=0 ;;
    --no-ollama-agent) ollama_agent=0 ;;
    -h | --help) sed -n '2,14p' "$0"; exit 0 ;;
    *) die "unknown option: $a" ;;
  esac
done

EMBED_REPO="${KB_EMBED_GGUF_REPO:-Qwen/Qwen3-Embedding-0.6B-GGUF}"
RERANK_REPO="${KB_RERANK_GGUF_REPO:-ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF}"
GGUF_QUANT="${KB_GGUF_QUANT:-Q8_0}"

# --- 1. platform checks ----------------------------------------------------------------------
is_macos || die "this script is for macOS"
[[ "$(uname -m)" == arm64 ]] || die "Apple Silicon (arm64) required: Metal inference is the point of this box"
have brew || die "Homebrew missing: https://brew.sh (then re-run)"
BREW="$(brew --prefix)"
ram_gb=$(($(sysctl -n hw.memsize) / 1024 / 1024 / 1024))
log "$(sw_vers -productName) $(sw_vers -productVersion), $(sysctl -n machdep.cpu.brand_string), ${ram_gb} GB RAM"

# --- 2. packages -----------------------------------------------------------------------------
log "Homebrew packages"
for pkg in llama.cpp ollama just uv jq restic; do
  if brew list --formula "$pkg" >/dev/null 2>&1; then ok "$pkg installed"; else brew install "$pkg"; fi
done
if ((native)); then
  [[ -d /Applications/Postgres.app ]] || warn "Postgres.app not found: install Postgres.app (PostgreSQL 18) and the ParadeDB pg_search .pkg, see deploy/README.md"
else
  if have docker; then ok "docker CLI present ($(docker context show 2>/dev/null || echo '?'))"
  else warn "no Docker runtime: brew install --cask orbstack (recommended) or brew install colima docker docker-compose"; fi
fi

# --- 3. power: an always-on server must never sleep and must boot after a power cut ---------
if ((pmset)); then
  log "Power settings (sudo). sleep 0/disksleep 0: never sleep or spin down; displaysleep 10: the"
  log "display may sleep; powernap 0: no dark-wake churn; womp 1: wake on network; autorestart 1:"
  log "boot automatically after a power failure; restartfreeze: reboot if the system hangs."
  sudo pmset -a sleep 0 disksleep 0 displaysleep 10 powernap 0 womp 1 autorestart 1
  sudo systemsetup -setrestartfreeze on >/dev/null 2>&1 || true
  pmset -g | grep -E ' (sleep|disksleep|autorestart|womp) ' >&2 || true
fi
if fdesetup status 2>/dev/null | grep -q "FileVault is On"; then
  warn "FileVault is ON: after a power loss the Mac waits at the unlock screen and NOTHING starts"
  warn "until someone types the password. Disable it on this dedicated box (encrypt backups with"
  warn "restic instead) or accept manual unlock. Also enable automatic login for LaunchAgents."
fi

# --- 4. directories and .env -------------------------------------------------------------------
mkdir -p "$KB_HOME/models" "$KB_HOME/backups" "$KB_LOG_DIR" "$LAUNCH_AGENTS"
chmod 700 "$KB_HOME/backups"
if [[ ! -f "$KB_REPO/.env" ]]; then
  log "creating .env from .env.example with random secrets"
  sed -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 24)|" \
      -e "s|^KB_API_TOKEN=.*|KB_API_TOKEN=$(openssl rand -hex 32)|" \
      "$KB_REPO/.env.example" >"$KB_REPO/.env"
  if ((ram_gb < 24)); then
    warn "${ram_gb} GB RAM: using qwen3.5:4b for chat (see sizing in deploy/README.md)"
    sed -i '' -e 's|^KB_LLM_MODEL=.*|KB_LLM_MODEL=qwen3.5:4b|' -e 's|^OLLAMA_CONTEXT_LENGTH=.*|OLLAMA_CONTEXT_LENGTH=16384|' "$KB_REPO/.env"
  fi
  chmod 600 "$KB_REPO/.env"
fi
load_env

# --- 5. GGUF models -> ~/.kb/models/{embed,rerank}.gguf -------------------------------------------
fetch_gguf() { # repo link-name
  local repo="$1" link="$KB_HOME/models/$2.gguf" dir="$KB_HOME/models/${1//\//__}" file
  if [[ -e "$link" ]]; then ok "$2.gguf present -> $(readlink "$link")"; return; fi
  log "downloading $repo ($GGUF_QUANT)"
  # ggml-org names files in lower case (qwen3-reranker-0.6b-q8_0.gguf), Qwen in upper case.
  uvx --from huggingface_hub hf download "$repo" --include "*${GGUF_QUANT}*.gguf" \
    --include "*$(tr '[:upper:]' '[:lower:]' <<<"$GGUF_QUANT")*.gguf" --local-dir "$dir" >&2
  file="$(find "$dir" -name '*.gguf' -type f | head -n1)"
  [[ -n "$file" ]] || die "no $GGUF_QUANT .gguf found in $repo"
  ln -sfn "$file" "$link"
  ok "$2.gguf -> $file"
}
if ((models)); then
  fetch_gguf "$EMBED_REPO" embed
  fetch_gguf "$RERANK_REPO" rerank
fi

# --- 6. launchd agents ---------------------------------------------------------------------------
render() { # template -> ~/Library/LaunchAgents/<name>
  local src="$KB_REPO/deploy/launchd/$1" dst="$LAUNCH_AGENTS/$1"
  sed -e "s|__HOME__|$HOME|g" -e "s|__REPO__|$KB_REPO|g" -e "s|__BREW__|$BREW|g" \
      -e "s|__OLLAMA_CONTEXT_LENGTH__|${OLLAMA_CONTEXT_LENGTH:-32768}|g" "$src" >"$dst.tmp"
  plutil -lint "$dst.tmp" >/dev/null || die "invalid plist rendered from $src"
  if cmp -s "$dst.tmp" "$dst"; then rm "$dst.tmp"; return 1; fi
  mv "$dst.tmp" "$dst"
}
install_agent() { # file.plist
  local label="${1%.plist}" domain changed=0
  domain="gui/$(id -u)"
  render "$1" && changed=1
  if launchctl print "$domain/$label" >/dev/null 2>&1; then
    if ((changed)); then launchctl bootout "$domain/$label" || true; sleep 1; else ok "$label up to date"; return; fi
  fi
  launchctl bootstrap "$domain" "$LAUNCH_AGENTS/$1"
  ok "$label loaded"
}

log "launchd agents ($LAUNCH_AGENTS)"
install_agent com.kb.llama-embed.plist
install_agent com.kb.llama-rerank.plist
if ((ollama_agent)); then
  if pgrep -xq Ollama; then
    warn "Ollama.app is running: not installing com.kb.ollama (two servers would fight over :11434)."
    warn "Set its context with: launchctl setenv OLLAMA_CONTEXT_LENGTH ${OLLAMA_CONTEXT_LENGTH:-32768}; then restart Ollama.app"
  else
    brew services list 2>/dev/null | grep -qE '^ollama +started' && { log "stopping brew services ollama (replaced by com.kb.ollama)"; brew services stop ollama; }
    install_agent com.kb.ollama.plist
  fi
fi
install_agent com.kb.backup.plist
if ((native)); then
  install_agent com.kb.api.plist
  install_agent com.kb.worker.plist
fi

# --- 7. wait for servers, pull chat models ----------------------------------------------------------
log "waiting for model servers"
for spec in "embed 8081/health llama-embed" "rerank 8082/health llama-rerank" "ollama 11434/api/version ollama"; do
  read -r name path logname <<<"$spec"
  if wait_http "http://127.0.0.1:$path" 180; then ok "$name :${path%%/*} up"; else warn "$name :${path%%/*} not up: tail $KB_LOG_DIR/$logname.log"; fi
done
if ((models)); then
  for m in "${KB_LLM_MODEL:-qwen3.5:9b}" "${KB_LLM_ROUTER_MODEL:-qwen3.5:4b}"; do
    log "ollama pull $m"
    "$BREW/bin/ollama" pull "$m"
  done
fi

# --- 8. smoke test the models (reranker zero-score guard) ---------------------------------------------
"$KB_REPO/scripts/check-models.sh"

log "host ready. Next:"
if ((native)); then
  echo "  scripts/models.sh status com.kb.api com.kb.worker && scripts/smoke.sh" >&2
else
  echo "  just up && just smoke        (OrbStack: enable 'Start at login'; System Settings > Users: automatic login)" >&2
fi
