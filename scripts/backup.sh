#!/usr/bin/env bash
# Backup mail-dashboard SQLite DB.
# Uruchamiane przez systemd timer codziennie o 3:30 UTC.
# Trzyma 14 ostatnich backupów, starsze usuwa.
set -euo pipefail

DB="/opt/mail-dashboard/data/mails.db"
BACKUP_DIR="/opt/mail-dashboard/backups"
KEEP_DAYS=14

mkdir -p "$BACKUP_DIR"

TS=$(date -u +%Y%m%d-%H%M)
DEST="$BACKUP_DIR/mails-$TS.db"

# SQLite online backup (bezpieczne mimo aktywnych zapisów dzięki WAL)
sqlite3 "$DB" ".backup '$DEST'"
gzip -f "$DEST"

# Usuń stare backupy (starsze niż KEEP_DAYS dni)
find "$BACKUP_DIR" -name "mails-*.db.gz" -mtime "+$KEEP_DAYS" -delete

# Log + statystyka
SIZE=$(du -h "$DEST.gz" | cut -f1)
COUNT=$(find "$BACKUP_DIR" -name "mails-*.db.gz" | wc -l)
echo "$(date -u +%FT%TZ) backup OK: $DEST.gz ($SIZE) total=$COUNT" >> /opt/mail-dashboard/logs/backup.log
