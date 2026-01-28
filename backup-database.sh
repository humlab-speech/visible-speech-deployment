#!/bin/bash
# Quick MongoDB database backup script for VISP
#
# Usage:
#   ./backup-database.sh              # Backup to ./backups/
#   ./backup-database.sh /path/dir    # Backup to specific directory

set -e

BACKUP_DIR="${1:-./backups}"
DATE=$(date +%Y%m%d_%H%M%S)

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

echo "=== VISP Database Backup ==="
echo "Date: $(date)"
echo "Target directory: $BACKUP_DIR"
echo

# Run the backup
./visp-podman.py backup -o "$BACKUP_DIR/visp_db_${DATE}.tar.gz"

echo
echo "✓ Backup saved to: $BACKUP_DIR"
echo
echo "Backup strategy:"
echo "  • Database: Use this script (small, frequent)"
echo "  • Files: Use rsync/filesystem backup for mounts/repositories/ (large, infrequent)"
echo
echo "To restore:"
echo "  ./visp-podman.py restore $BACKUP_DIR/visp_db_${DATE}.tar.gz"
