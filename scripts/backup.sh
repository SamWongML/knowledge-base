#!/usr/bin/env bash
# Nightly logical backup of the kb database (run by launchd: deploy/launchd/com.kb.backup.plist).
#
#   1. pg_dump -Fc (zstd) -> $KB_BACKUP_DIR/daily/kb-<timestamp>.dump, verified with pg_restore --list
#   2. Retention: KB_BACKUP_KEEP_DAILY (7) daily dumps; Sunday's dump is also kept as a weekly
#      (KB_BACKUP_KEEP_WEEKLY, 4).
#   3. Offsite (optional): if RESTIC_REPOSITORY is set, restic backs up the dump dir and .env,
#      then forgets/prunes (7 daily / 4 weekly / 6 monthly); `restic check` on Sundays.
#   4. Optional dead-man's switch: KB_BACKUP_PING_URL (healthchecks.io style; /fail on error).
#
# Works against the compose db container or a native Postgres (KB_DATABASE_URL); see lib.sh.
# Time Machine is NOT a database backup - these dumps are what you restore from.
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env

BACKUP_DIR="${KB_BACKUP_DIR:-$KB_HOME/backups}"
case "$BACKUP_DIR" in /*) ;; *) BACKUP_DIR="$KB_REPO/$BACKUP_DIR" ;; esac
KEEP_DAILY="${KB_BACKUP_KEEP_DAILY:-7}"
KEEP_WEEKLY="${KB_BACKUP_KEEP_WEEKLY:-4}"
PING_URL="${KB_BACKUP_PING_URL:-}"

ping_hc() {
  if [[ -n "$PING_URL" ]]; then curl -fsS -m 10 --retry 3 -o /dev/null "$PING_URL$1" || true; fi
}
on_error() { warn "backup FAILED (line $1)"; ping_hc /fail; }
trap 'on_error $LINENO' ERR

mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/weekly"
chmod 700 "$BACKUP_DIR"
mode="$(deploy_mode)"
[[ "$mode" == native ]] && native_env   # KB_DATABASE_URL -> 127.0.0.1
stamp="$(date +%Y%m%d-%H%M%S)"
out="$BACKUP_DIR/daily/kb-$stamp.dump"
tmp="$out.partial"
ping_hc /start

log "pg_dump ($mode) -> $out"
umask 077
if [[ "$mode" == compose ]]; then
  compose exec -T db pg_dump -U kb -d kb -Fc -Z zstd:3 >"$tmp"
  compose exec -T db pg_restore --list >/dev/null <"$tmp"
else
  pg_dump -d "$KB_DATABASE_URL" -Fc -Z zstd:3 -f "$tmp"
  pg_restore --list "$tmp" >/dev/null
fi
[[ -s "$tmp" ]] || die "empty dump"
mv "$tmp" "$out"
ok "dump verified ($(du -h "$out" | cut -f1))"

# Weekly copy on Sundays (or when there is no weekly yet).
if [[ "$(date +%u)" == 7 ]] || ! compgen -G "$BACKUP_DIR/weekly/kb-*.dump" >/dev/null; then
  ln -f "$out" "$BACKUP_DIR/weekly/" 2>/dev/null || cp "$out" "$BACKUP_DIR/weekly/"
fi

prune() { # dir keep
  local f
  # shellcheck disable=SC2012  # names are our own fixed-format timestamps
  ls -1t "$1"/kb-*.dump 2>/dev/null | tail -n +"$(($2 + 1))" | while read -r f; do rm -f -- "$f"; done
}
prune "$BACKUP_DIR/daily" "$KEEP_DAILY"
prune "$BACKUP_DIR/weekly" "$KEEP_WEEKLY"
rm -f "$BACKUP_DIR"/daily/*.partial

if [[ -n "${RESTIC_REPOSITORY:-}" ]]; then
  have restic || die "RESTIC_REPOSITORY is set but restic is not installed (brew install restic)"
  log "restic backup -> $RESTIC_REPOSITORY"
  paths=("$BACKUP_DIR")
  [[ -f "$KB_REPO/.env" ]] && paths+=("$KB_REPO/.env")
  restic backup --tag kb --host kb-mac-mini "${paths[@]}"
  restic forget --tag kb --host kb-mac-mini --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune
  [[ "$(date +%u)" == 7 ]] && restic check
fi

ping_hc ""
ok "backup complete"
