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

# --- nftables CIDR blocks ---
# Tinyproxy's Filter matches hostnames as written in the CONNECT request,
# including raw IP-literal addresses (e.g. CONNECT 10.1.2.3:443 is caught by
# the ^10\. pattern). However it never resolves DNS — so "umu.se" is not matched
# by ^130\.239\. even though umu.se resolves to 130.239.34.63.
#
# We close both gaps (raw-IP and DNS-resolved-to-blocked-IP) with nftables
# OUTPUT DROP rules, which operate after DNS resolution at the TCP/IP layer.
#
# The proxy container itself runs on the Podman bridge network (typically
# 10.88.0.0/16, inside RFC 1918 10.0.0.0/8). It must reach the bridge gateway
# to route traffic to the internet. We detect the gateway at startup and add an
# explicit ACCEPT rule for that single IP before the broad RFC 1918 DROP rules.
#
# This requires CAP_NET_ADMIN (granted in Session.class.js). The capability is
# limited to this container's own network namespace.
#
# nftables is used instead of iptables because:
#   - iptables (nft backend) requires CAP_NET_ADMIN in the root user namespace
#   - iptables-legacy can't find its xtables .so plugins in the Alpine image
#   - nftables works correctly in rootless Podman user namespaces with NET_ADMIN
echo "Installing nftables OUTPUT blocks..."
# Detect the default gateway — the proxy container runs on the Podman bridge
# network (typically 10.88.0.0/16) and needs to reach this single IP to route
# traffic to the internet. We exempt it before blocking the rest of RFC 1918.
GATEWAY=$(ip route | awk '/^default/ {print $3; exit}')
echo "  Detected bridge gateway: ${GATEWAY:-none}"

if nft add table ip visp_filter 2>&1 && \
   nft add chain ip visp_filter output '{ type filter hook output priority 0 ; policy accept ; }' 2>&1; then

    # Exempt the gateway first (must come before the broad DROP rules)
    if [ -n "$GATEWAY" ]; then
        nft add rule ip visp_filter output ip daddr "$GATEWAY" accept && \
            echo "  ALLOW (nftables): $GATEWAY (bridge gateway)"
    fi

    # Exempt loopback — socat (running inside this container) connects to
    # tinyproxy on 127.0.0.1:8888 to bridge the UDS socket. Without this,
    # the 127.0.0.0/8 DROP rule would cut that internal connection.
    nft add rule ip visp_filter output ip daddr 127.0.0.1 accept && echo "  ALLOW (nftables): 127.0.0.1 (socat→tinyproxy)"

    # Exempt DNS (UDP+TCP port 53) — the nameserver may be a different RFC 1918
    # address (e.g. 10.255.255.254) that would otherwise be caught by the 10/8 rule.
    nft add rule ip visp_filter output udp dport 53 accept && echo "  ALLOW (nftables): UDP/53 (DNS)"
    nft add rule ip visp_filter output tcp dport 53 accept && echo "  ALLOW (nftables): TCP/53 (DNS)"

    for subnet in \
        10.0.0.0/8 \
        172.16.0.0/12 \
        192.168.0.0/16 \
        127.0.0.0/8 \
        169.254.0.0/16
    do
        nft add rule ip visp_filter output ip daddr "$subnet" drop && \
            echo "  BLOCK (nftables): $subnet" || \
            echo "  WARNING: failed to add nftables rule for $subnet"
    done

    # Append site-specific CIDR blocks from env var (space-separated CIDR notation)
    # Example: PROXY_BLOCKED_CIDRS="203.0.113.0/24 198.51.100.0/24"
    if [ -n "$PROXY_BLOCKED_CIDRS" ]; then
        echo "# Site-specific nftables blocks from PROXY_BLOCKED_CIDRS"
        for cidr in $PROXY_BLOCKED_CIDRS; do
            nft add rule ip visp_filter output ip daddr "$cidr" drop && \
                echo "  BLOCK (nftables): $cidr" || \
                echo "  WARNING: failed to add nftables rule for $cidr"
        done
    fi
else
    echo "  WARNING: nftables table/chain setup failed — IP-level blocking inactive"
fi

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
