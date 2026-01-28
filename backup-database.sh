#!/usr/bin/env bash
# Robust MongoDB backup wrapper for VISP
# Usage: ./backup-database.sh [target_dir]

set -euo pipefail

BACKUP_DIR="${1:-./backups}"
DATE=$(date +%Y%m%d_%H%M%S)
OUT="$BACKUP_DIR/visp_db_${DATE}.tar.gz"

# Ensure backup directory exists and is writable
mkdir -p "$BACKUP_DIR"
if [ ! -w "$BACKUP_DIR" ]; then
  echo "✗ Backup directory not writable: $BACKUP_DIR" >&2
  exit 1
fi

echo "=== VISP Database Backup ==="
echo "Date: $(date)"
echo "Target file: $OUT"
echo

# Locate visp-podman.py (allow running via python3 if not executable)
PODMAN_SCRIPT="./visp-podman.py"
if [ -x "$PODMAN_SCRIPT" ]; then
  BACKUP_CMD=("$PODMAN_SCRIPT" backup -o "$OUT")
elif [ -f "$PODMAN_SCRIPT" ] && command -v python3 >/dev/null 2>&1; then
  BACKUP_CMD=(python3 "$PODMAN_SCRIPT" backup -o "$OUT")
elif command -v visp-podman.py >/dev/null 2>&1; then
  BACKUP_CMD=(visp-podman.py backup -o "$OUT")
else
  echo "✗ Could not find runnable visp-podman.py in repo root or PATH." >&2
  echo "  Run this script from the repository root or ensure visp-podman.py is installed." >&2
  exit 1
fi

# Run the backup command
echo "Running backup command: ${BACKUP_CMD[*]}"
("${BACKUP_CMD[@]}")
rc=$?
if [ $rc -ne 0 ]; then
  echo "✗ Backup failed (exit $rc)" >&2
  exit $rc
fi

if [ -f "$OUT" ]; then
  size_mb=$(du -m "$OUT" | cut -f1)
  echo
  echo "✓ Backup complete: $OUT (${size_mb} MB)"
  echo
  echo "To restore: ./visp-podman.py restore $OUT"
  exit 0
else
  echo "✗ Backup command completed but $OUT not found" >&2
  exit 1
fi
