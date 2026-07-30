#!/usr/bin/env bash
#
# Back up the Praxis Postgres database to a timestamped custom-format dump and
# verify the result is a restorable archive before trusting it.
#
# Reads DATABASE_URL from the environment (the same connection string the
# running FastAPI container uses) — never hardcodes credentials.
#
# Usage:
#   DATABASE_URL=postgresql://... ./scripts/backup_db.sh
#
# Exits non-zero if DATABASE_URL is unset, pg_dump fails, or the produced
# file is not a valid pg_restore-able archive (e.g. a 0-byte/corrupt dump,
# which is exactly the failure mode the old ~/praxis_backup.dump hit).

set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "ERROR: DATABASE_URL is not set. Export the Azure Postgres connection" >&2
  echo "       string (the same one the backend container uses) and re-run." >&2
  exit 2
fi

if ! command -v pg_dump >/dev/null 2>&1; then
  echo "ERROR: pg_dump not found on PATH. Install the postgresql-client package." >&2
  exit 2
fi
if ! command -v pg_restore >/dev/null 2>&1; then
  echo "ERROR: pg_restore not found on PATH. Install the postgresql-client package." >&2
  exit 2
fi

# Resolve repo root (one level up from this script) so the script works from
# any working directory and always writes under <repo>/backups/.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-${REPO_ROOT}/backups}"
mkdir -p "$BACKUP_DIR"

STAMP="$(date -u +%Y%m%d_%H%M%S)"
DUMP_FILE="${BACKUP_DIR}/praxis_${STAMP}.dump"

# --no-owner --no-acl keep the dump portable across Azure Database for
# PostgreSQL roles (e.g. restoring with azure_pg_admin). -Fc = custom format.
echo "Backing up ${DATABASE_URL#*@} -> ${DUMP_FILE}"
if ! pg_dump -Fc --no-owner --no-acl --dbname="$DATABASE_URL" --file="$DUMP_FILE"; then
  rm -f "$DUMP_FILE"
  echo "ERROR: pg_dump failed. Removed incomplete dump: ${DUMP_FILE}" >&2
  exit 1
fi

# Critical verification: the old backup was a 0-byte/corrupt custom-format
# dump that pg_restore -l rejected ("input file is too short"). Validate the
# file we just produced can actually be listed/restored before declaring
# success, so a broken dump is caught now — not months later.
if ! pg_restore -l "$DUMP_FILE" >/dev/null 2>&1; then
  echo "ERROR: produced dump is not a valid pg_restore archive:" >&2
  echo "       ${DUMP_FILE}" >&2
  pg_restore -l "$DUMP_FILE" >&2 | head -20 || true
  rm -f "$DUMP_FILE"
  echo "       Removed invalid dump so it can't be mistaken for a good backup." >&2
  exit 1
fi

echo "OK: dump is a valid restorable archive."
echo
echo "Tables captured in this dump (pg_restore -l):"
# Show table-data TOC entries (the per-table row payloads) plus their OID.
pg_restore -l "$DUMP_FILE" | grep -i -E "TABLE DATA|^\d+ .* \[" || true
echo
echo "Size: $(du -h "$DUMP_FILE" | cut -f1)  ->  ${DUMP_FILE}"
echo "Verify/restore later with: pg_restore -l \"${DUMP_FILE}\""