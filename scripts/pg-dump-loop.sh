#!/usr/bin/env bash
# In-container nightly pg_dump for the optional compose `backup` profile.
# Env: PGHOST PGUSER PGPASSWORD PGDATABASE BACKUP_DIR BACKUP_AT(HH:MM) KEEP_DAILY KEEP_WEEKLY
set -euo pipefail
: "${BACKUP_DIR:=/backups}" "${BACKUP_AT:=03:15}" "${KEEP_DAILY:=7}" "${KEEP_WEEKLY:=4}"
mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/weekly"

prune() { find "$1" -maxdepth 1 -name 'kb-*.dump' -printf '%T@ %p\n' | sort -rn | tail -n +"$(($2 + 1))" | cut -d' ' -f2- | xargs -r rm -f --; }

while true; do
  now=$(date +%s)
  next=$(date -d "today $BACKUP_AT" +%s)
  ((next > now)) || next=$(date -d "tomorrow $BACKUP_AT" +%s)
  echo "next backup at $(date -d "@$next")"
  sleep $((next - now))

  out="$BACKUP_DIR/daily/kb-$(date +%Y%m%d-%H%M%S).dump"
  if pg_dump -Fc -Z zstd:3 -f "$out.partial" && pg_restore --list "$out.partial" >/dev/null; then
    mv "$out.partial" "$out"
    echo "backup ok: $out ($(du -h "$out" | cut -f1))"
    [[ "$(date +%u)" == 7 ]] && cp "$out" "$BACKUP_DIR/weekly/"
    prune "$BACKUP_DIR/daily" "$KEEP_DAILY"
    prune "$BACKUP_DIR/weekly" "$KEEP_WEEKLY"
  else
    echo "backup FAILED" >&2
    rm -f "$out.partial"
  fi
done
