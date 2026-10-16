#!/usr/bin/env bash
# Restore a pg_dump -Fc backup of the kb database.
#
#   scripts/restore.sh --drill [FILE]   restore into a scratch DB (kb_restore_drill), print row
#                                       counts, drop it. Never touches the live DB. Run monthly.
#   scripts/restore.sh [--yes] FILE     REPLACE the live kb DB: stops api + worker, drops and
#                                       recreates kb, pg_restore -j 4, starts api + worker.
#
# FILE defaults to the newest dump in $KB_BACKUP_DIR/daily. BM25 and HNSW indexes are rebuilt
# during restore, which dominates the runtime on large knowledge bases.
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env

drill=0 yes=0 file=""
while (($#)); do
  case "$1" in
    --drill) drill=1 ;;
    --yes | -y) yes=1 ;;
    -h | --help) sed -n '2,12p' "$0"; exit 0 ;;
    -*) die "unknown option $1" ;;
    *) file="$1" ;;
  esac
  shift
done

BACKUP_DIR="${KB_BACKUP_DIR:-$KB_HOME/backups}"
case "$BACKUP_DIR" in /*) ;; *) BACKUP_DIR="$KB_REPO/$BACKUP_DIR" ;; esac
if [[ -z "$file" ]]; then
  # shellcheck disable=SC2012
  file="$(ls -1t "$BACKUP_DIR"/daily/kb-*.dump 2>/dev/null | head -n1 || true)"
  [[ -n "$file" ]] || die "no dumps in $BACKUP_DIR/daily; pass a file"
fi
[[ -s "$file" ]] || die "not a file: $file"

mode="$(deploy_mode)"
[[ "$mode" == native ]] && native_env   # KB_DATABASE_URL -> 127.0.0.1
target=kb
((drill)) && target=kb_restore_drill

# --- primitives per deployment mode --------------------------------------------------------
sql() { # db sql...
  local db="$1"; shift
  local args=()
  for s in "$@"; do args+=(-c "$s"); done
  if [[ "$mode" == compose ]]; then
    compose exec -T db psql -U kb -d "$db" -v ON_ERROR_STOP=1 -qAt "${args[@]}"
  else
    psql "$(native_db_url "$db")" -v ON_ERROR_STOP=1 -qAt "${args[@]}"
  fi
}
restore_into() { # db
  if [[ "$mode" == compose ]]; then
    compose exec -T db sh -c 'cat > /tmp/kb-restore.dump' <"$file"
    compose exec -T db pg_restore -U kb -d "$1" -j 4 --no-owner --exit-on-error /tmp/kb-restore.dump
    compose exec -T db rm -f /tmp/kb-restore.dump
  else
    pg_restore -d "$(native_db_url "$1")" -j 4 --no-owner --exit-on-error "$file"
  fi
}
app() { # stop|start
  if [[ "$mode" == compose ]]; then
    compose "$1" api worker
  else
    "$KB_REPO/scripts/models.sh" "$1" com.kb.api com.kb.worker
  fi
}

log "restoring $file into '$target' ($mode)"
if ((drill)); then
  sql postgres "drop database if exists $target with (force)" "create database $target template template0"
  start=$(date +%s)
  restore_into "$target"
  counts="$(sql "$target" "select format('%s documents, %s chunks, %s collections', (select count(*) from documents), (select count(*) from chunks), (select count(*) from collections))")"
  sql postgres "drop database $target with (force)"
  ok "drill passed in $(($(date +%s) - start))s: $counts"
  exit 0
fi

if ((!yes)); then
  read -r -p "This REPLACES the live 'kb' database with $(basename "$file"). Type 'restore' to continue: " answer
  [[ "$answer" == restore ]] || die "aborted"
fi
app stop
trap 'warn "restore failed; api/worker left stopped. Fix, then rerun or: just up"' ERR
sql postgres "drop database if exists kb with (force)" "create database kb template template0"
restore_into kb
trap - ERR
app start
ok "restore complete"
