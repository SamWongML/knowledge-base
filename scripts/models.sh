#!/usr/bin/env bash
# Manage the native launchd services (LaunchAgents in ~/Library/LaunchAgents).
#   scripts/models.sh start|stop|restart|status|logs [label...]
# Default labels are the model servers; pass e.g. `com.kb.api com.kb.worker` for native mode.
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

cmd="${1:-status}"
shift || true
labels=("$@")
((${#labels[@]})) || labels=(com.kb.llama-embed com.kb.llama-rerank com.kb.ollama)

if ! is_macos; then
  warn "launchd services only exist on macOS; skipping '$cmd'"
  exit 0
fi

domain="gui/$(id -u)"
loaded() { launchctl print "$domain/$1" >/dev/null 2>&1; }

case "$cmd" in
  start)
    for l in "${labels[@]}"; do
      plist="$LAUNCH_AGENTS/$l.plist"
      if loaded "$l"; then
        launchctl kickstart "$domain/$l" && ok "$l running"
      elif [[ -f "$plist" ]]; then
        launchctl bootstrap "$domain" "$plist" && ok "$l loaded"
      else
        warn "$l: $plist not installed (run scripts/macos-setup.sh)"
      fi
    done
    ;;
  stop)
    for l in "${labels[@]}"; do
      if loaded "$l"; then launchctl bootout "$domain/$l" && ok "$l stopped"; fi
    done
    ;;
  restart)
    for l in "${labels[@]}"; do
      if loaded "$l"; then launchctl kickstart -k "$domain/$l" && ok "$l restarted"; else warn "$l not loaded"; fi
    done
    ;;
  status)
    for l in "${labels[@]}"; do
      if loaded "$l"; then
        pid="$(launchctl print "$domain/$l" | awk '/^\tpid =/ {print $3}')"
        ok "$l loaded${pid:+ (pid $pid)}"
      else
        warn "$l not loaded"
      fi
    done
    for pair in embed:8081/health rerank:8082/health ollama:11434/api/version; do
      name="${pair%%:*}" path="${pair#*:}"
      if curl -fsS -m 3 -o /dev/null "http://127.0.0.1:$path"; then ok "$name http://127.0.0.1:${path%%/*} healthy"; else warn "$name http://127.0.0.1:${path%%/*} not answering"; fi
    done
    ;;
  logs)
    exec tail -n 100 -F "$KB_LOG_DIR"/*.log
    ;;
  *) die "usage: $0 start|stop|restart|status|logs [label...]" ;;
esac
