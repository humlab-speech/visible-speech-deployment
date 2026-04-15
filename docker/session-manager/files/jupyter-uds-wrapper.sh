#!/bin/bash
# jupyter-uds-wrapper.sh — Entrypoint wrapper for VISP Jupyter UDS isolation mode.
#
# When Jupyter runs with --network=none, this script starts a socat bridge
# that connects a local TCP port to the proxy sidecar's Unix Domain Socket,
# enabling pip/conda/CRAN to work through the tinyproxy proxy.
#
# After starting the bridge, it exec's the standard Jupyter start-notebook.py
# with all arguments passed by the container command (including UDS args).

set -e

PROXY_SOCK="/run/session/proxy.sock"
PROXY_PORT="${VISP_PROXY_PORT:-3128}"

if [ "${VISP_PROXY_ENABLED}" = "1" ]; then
    echo "[visp] Waiting for proxy socket at ${PROXY_SOCK}..."
    # Wait up to 15 seconds for the proxy sidecar to create the socket
    for i in $(seq 1 30); do
        [ -S "$PROXY_SOCK" ] && break
        sleep 0.5
    done

    if [ -S "$PROXY_SOCK" ]; then
        socat TCP-LISTEN:${PROXY_PORT},fork,reuseaddr,bind=127.0.0.1 \
              UNIX-CONNECT:${PROXY_SOCK} &
        SOCAT_PID=$!
        echo "[visp] Proxy bridge started: 127.0.0.1:${PROXY_PORT} → ${PROXY_SOCK} (PID: ${SOCAT_PID})"
    else
        echo "[visp] WARNING: Proxy socket not found at ${PROXY_SOCK} after 15s, outgoing proxy disabled"
    fi
fi

# Exec Jupyter — UDS args (--ServerApp.sock=...) are passed as "$@"
exec start-notebook.py "$@"
