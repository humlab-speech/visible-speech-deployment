#!/usr/bin/env bash
set -euo pipefail

# Demo script for `visp.py fix-permissions`
#
# Running `visp.py fix-permissions` with no --path mirrors the permission
# maintenance done by `visp.py install`: container-writable directories,
# Mongo mount ownership/mode, and repository ownership normalization.
#
# This demo applies only an explicit --path repair on a disposable directory.
# The default dry-run now also shows Mongo's rootless UID/GID mapping fallback:
# Mongo runs as container uid/gid 999:999, which maps to a high host uid/gid
# that depends on the current server's /etc/subuid and /etc/subgid entries.
#
# Behavior:
# 1) show Mongo's container 999:999 -> host uid/gid mapping
# 2) show the default install-equivalent dry-run plan
# 3) create a demo directory and file as the current user
# 4) corrupt ownership using `podman unshare chown 1000:1000` and restrictive mode
# 5) show `ls`/`stat` so you can observe the broken state
# 6) show a dry-run plan for the explicit demo path
# 7) apply the explicit-path fix (no sudo used; uses podman unshare chown/chmod)
# 8) show `ls`/`stat` after fix and remove the demo dir

DEMO="mounts/apache/apache/uploads/demo-fix-perm-script"
VISP_CMD="$(pwd)/visp.py"

if [ ! -x "$VISP_CMD" ]; then
  echo "Error: $VISP_CMD not found or not executable. Run this script from the project root where visp.py is located."
  exit 1
fi

map_rootless_id() {
  local namespace_id="$1"
  local map_file="$2"
  podman unshare cat "$map_file" | awk -v id="$namespace_id" '
    {
      ns_start = $1
      host_start = $2
      length = $3
      if (id >= ns_start && id < ns_start + length) {
        print host_start + id - ns_start
        found = 1
        exit
      }
    }
    END {
      if (!found) {
        exit 1
      }
    }
  '
}

echo "=== Mongo rootless ownership mapping ==="
if mongo_host_uid="$(map_rootless_id 999 /proc/self/uid_map)" && mongo_host_gid="$(map_rootless_id 999 /proc/self/gid_map)"; then
  echo "Mongo container owner 999:999 maps to host owner ${mongo_host_uid}:${mongo_host_gid}"
else
  echo "Could not resolve Mongo's rootless mapping; check: podman unshare cat /proc/self/uid_map"
fi
echo

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
