#!/usr/bin/env bash
set -euo pipefail

# Demo script for `visp.py fix-permissions`
# Behavior:
# 1) create a demo directory and file as the current user
# 2) corrupt ownership using `podman unshare chown 1000:1000` and restrictive mode
# 3) show `ls`/`stat` so you can observe the broken state
# 4) show a dry-run plan from `visp.py fix-permissions`
# 5) apply the fix (no sudo used; uses podman unshare chown/chmod)
# 6) show `ls`/`stat` after fix and remove the demo dir

DEMO="mounts/apache/apache/uploads/demo-fix-perm-script"
VISP_CMD="$(pwd)/visp.py"

if [ ! -x "$VISP_CMD" ]; then
  echo "Error: $VISP_CMD not found or not executable. Run this script from the project root where visp.py is located."
  exit 1
fi

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

echo "\n--- Dry-run: show fix plan (no changes) ---"
"$VISP_CMD" fix-permissions -p "$DEMO" -r

read -p $'\nApply fixes now? This will run podman unshare chown/chmod (no sudo will be used). [y/N]: ' -r
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
  echo "Aborting — nothing changed. You can run the above commands yourself when ready."
  exit 0
fi

echo "\n--- APPLY: running fix-permissions ---"
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
