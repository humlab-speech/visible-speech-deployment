#!/bin/sh
# Entrypoint for the VISP session proxy sidecar.
# Runs tinyproxy (TCP) + socat (TCP→UDS bridge) so that
# --network=none session containers can reach the internet
# via a shared Unix Domain Socket.
set -e

SOCKET_PATH="${PROXY_SOCKET_PATH:-/run/session/proxy.sock}"
TINYPROXY_PORT="${TINYPROXY_PORT:-8888}"
FILTER_FILE="/tmp/proxy-filter.txt"

# --- Generate blocklist filter file ---
# Tinyproxy Filter with FilterDefaultDeny=No means matching hosts are BLOCKED.
# We block all RFC 1918 private ranges, link-local, and loopback to prevent
# sessions from reaching internal infrastructure (MongoDB, session-manager, etc.)
echo "Generating network blocklist..."
cat > "$FILTER_FILE" <<'EOF'
# RFC 1918 private networks
^10\.
^172\.(1[6-9]|2[0-9]|3[01])\.
^192\.168\.
# Loopback (prevent proxy-to-localhost abuse)
^127\.
^localhost$
^localhost:
# Link-local
^169\.254\.
# All IPv6 — block entirely to prevent bypasses via IPv6 addresses
# that may be routable inside the university firewall but not outside.
# With FilterURLs Off, tinyproxy strips brackets so the host is bare
# e.g. "2607:f8b0::" or "::1". Match any host containing a colon
# (domains and IPv4 never have colons in the hostname).
^[0-9a-f]*:
# Umeå University internal network (130.239.0.0/16)
# The proxy runs inside the university firewall, so without this block
# sessions could reach internal-only services that are inaccessible from
# the public internet (e.g. management interfaces, dev servers).
^130\.239\.
EOF

# Append site-specific blocked patterns from env var (space-separated)
# Example: PROXY_BLOCKED_NETWORKS="^10\.18\. ^203\.0\.113\."
if [ -n "$PROXY_BLOCKED_NETWORKS" ]; then
    echo "# Site-specific blocks from PROXY_BLOCKED_NETWORKS" >> "$FILTER_FILE"
    for pattern in $PROXY_BLOCKED_NETWORKS; do
        echo "$pattern" >> "$FILTER_FILE"
    done
fi

echo "Blocklist entries:"
grep -v '^#' "$FILTER_FILE" | grep -v '^$' | while read -r line; do
    echo "  BLOCK: $line"
done

# Remove stale socket from previous run
rm -f "$SOCKET_PATH"

# Start tinyproxy in daemon mode (background)
echo "Starting tinyproxy on 127.0.0.1:${TINYPROXY_PORT}..."
tinyproxy -d -c /etc/tinyproxy/tinyproxy.conf &
TINYPROXY_PID=$!

# Give tinyproxy a moment to bind
sleep 0.5

# Bridge: UDS ← socat → tinyproxy TCP
# Session containers connect to the UDS; socat forwards to tinyproxy.
echo "Starting socat bridge: ${SOCKET_PATH} → 127.0.0.1:${TINYPROXY_PORT}"
socat UNIX-LISTEN:"${SOCKET_PATH}",fork,mode=777 TCP:127.0.0.1:${TINYPROXY_PORT} &
SOCAT_PID=$!

# Clean up on exit
cleanup() {
    kill $TINYPROXY_PID $SOCAT_PID 2>/dev/null || true
    rm -f "$SOCKET_PATH"
}
trap cleanup EXIT INT TERM

# Wait for either process to exit (portable — no wait -n needed)
while kill -0 $TINYPROXY_PID 2>/dev/null && kill -0 $SOCAT_PID 2>/dev/null; do
    sleep 1
done

echo "Proxy sidecar exiting."
