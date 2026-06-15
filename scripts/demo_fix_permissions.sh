#!/usr/bin/env bash
set -euo pipefail

# Demo script for `visp.py fix-permissions`
#
# Running `visp.py fix-permissions` with no --path mirrors the permission
# maintenance done by `visp.py install`: container-writable directories,
# Mongo mount ownership/mode, and repository ownership normalization.
#
# This demo applies only an explicit --path repair on a disposable directory.
# Behavior:
# 1) show the default install-equivalent dry-run plan
# 2) create a demo directory and file as the current user
# 3) corrupt ownership using `podman unshare chown 1000:1000` and restrictive mode
# 4) show `ls`/`stat` so you can observe the broken state
# 5) show a dry-run plan for the explicit demo path
# 6) apply the explicit-path fix (no sudo used; uses podman unshare chown/chmod)
# 7) show `ls`/`stat` after fix and remove the demo dir

DEMO="mounts/apache/apache/uploads/demo-fix-perm-script"
VISP_CMD="$(pwd)/visp.py"

if [ ! -x "$VISP_CMD" ]; then
  echo "Error: $VISP_CMD not found or not executable. Run this script from the project root where visp.py is located."
  exit 1
fi

echo "=== Default install-equivalent dry-run (no changes) ==="
"$VISP_CMD" fix-permissions

echo "=== Demo: create demo dir and file as user $(id -un) ==="
# ensure previous demo dir removed (use namespace removal to avoid sudo requirements)
if [ -d "$DEMO" ]; then
  podman unshare rm -rf "$DEMO" || rm -rf "$DEMO" || true
fi
mkdir -p "$DEMO"
echo "secret-$(date +%s)" > "$DEMO/hello.txt"

echo "\n--- BEFORE ---"
stat -c 'PATH:%n Mode:%a Uid:%u Gid:%g Owner:%U Group:%G' "$DEMO" "$DEMO/hello.txt"
ls -lah "$(dirname "$DEMO")"

echo "\n--- CORRUPT: change owner inside namespace and make dir restrictive ---"
# set to a different uid inside namespace (simulate container-created owner)
podman unshare chown 1000:1000 "$DEMO" || true
podman unshare chmod 700 "$DEMO" || true

echo "\n--- AFTER CORRUPT ---"
stat -c 'PATH:%n Mode:%a Uid:%u Gid:%g Owner:%U Group:%G' "$DEMO" "$DEMO/hello.txt" || true
ls -lah "$(dirname "$DEMO")"

echo "\n--- Dry-run: explicit demo path only (no changes) ---"
"$VISP_CMD" fix-permissions -p "$DEMO" -r

read -p $'\nApply fixes now? This will run podman unshare chown/chmod (no sudo will be used). [y/N]: ' -r
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
  echo "Aborting - nothing changed. You can run the above commands yourself when ready."
  exit 0
fi

echo "\n--- APPLY: running explicit-path fix-permissions ---"
"$VISP_CMD" fix-permissions -p "$DEMO" -r --apply || true

echo "\n--- AFTER APPLY ---"
stat -c 'PATH:%n Mode:%a Uid:%u Gid:%g Owner:%U Group:%G' "$DEMO" "$DEMO/hello.txt" || true
ls -lah "$(dirname "$DEMO")"

echo "\n--- CLEANUP: removing demo directory ---"
if rm -rf "$DEMO"; then
  echo "Removed $DEMO"
else
  echo "Could not remove $DEMO as current user; attempting namespace removal with 'podman unshare rm -rf'"
  if podman unshare rm -rf "$DEMO"; then
    echo "Removed $DEMO inside namespace (no sudo used)"
  else
    echo "Failed to remove $DEMO — manual intervention needed"
  fi
fi

echo "\nDemo complete."
