#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <image> <directory>"
  echo "Example: $0 localhost/wsrng-server:latest external/wsrng-server"
}

if [[ $# -ne 2 ]]; then usage; exit 2; fi

IMAGE="$1"
DIR="$2"

if [[ ${EUID:-0} -eq 0 ]]; then
  echo "Error: don't run this with sudo/root. Run as the same user that runs rootless podman." >&2
  exit 1
fi

if [[ ! -d "$DIR" ]]; then
  echo "Error: directory does not exist: $DIR" >&2
  exit 1
fi

# Make DIR absolute
if command -v realpath >/dev/null 2>&1; then
  DIR="$(realpath "$DIR")"
else
  DIR="$(cd "$DIR" && pwd)"
fi

# Determine runtime uid:gid (container view)
IMG_UID="$(podman run --rm --entrypoint id "$IMAGE" -u 2>/dev/null || true)"
IMG_GID="$(podman run --rm --entrypoint id "$IMAGE" -g 2>/dev/null || true)"

if [[ ! "$IMG_UID" =~ ^[0-9]+$ ]] || [[ ! "$IMG_GID" =~ ^[0-9]+$ ]]; then
  echo "Error: couldn't determine numeric UID/GID by running: $IMAGE" >&2
  echo "Tip: if it's distroless/minimal, you may need an image that has 'id' or set an explicit user in compose." >&2
  exit 1
fi

echo "Image:     $IMAGE"
echo "Dir:       $DIR"
echo "Userns chown target (container view): ${IMG_UID}:${IMG_GID}"

podman unshare chown -R "${IMG_UID}:${IMG_GID}" "$DIR"

echo "Host-side ownership after chown (expect subuid/subgid mapping):"
ls -ldn "$DIR"
