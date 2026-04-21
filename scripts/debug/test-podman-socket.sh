#!/bin/bash

# Test Podman socket compatibility with node-docker-api
# This validates Phase 1 of the Docker → Podman migration

set -e

echo "=== Podman Socket Compatibility Test ==="
echo ""

# Check if Podman is installed
if ! command -v podman &> /dev/null; then
    echo "❌ Podman is not installed"
    echo "   Install with: sudo dnf install podman (Fedora/RHEL)"
    echo "   or: sudo apt install podman (Ubuntu/Debian)"
    exit 1
fi

echo "✓ Podman is installed: $(podman --version)"

# Detect WSL
IS_WSL=false
if grep -qi microsoft /proc/version 2>/dev/null; then
    IS_WSL=true
    echo "ℹ️  WSL environment detected"
fi

# Set up socket path based on environment
SOCKET_PATH=""
SOCKET_DIR="$HOME/.podman"
MANUAL_SOCKET="$SOCKET_DIR/podman.sock"

# Check for existing Podman socket
if [ -S "/run/podman/podman.sock" ]; then
    SOCKET_PATH="/run/podman/podman.sock"
    echo "✓ Podman socket found at: $SOCKET_PATH"
elif [ -S "$XDG_RUNTIME_DIR/podman/podman.sock" ]; then
    SOCKET_PATH="$XDG_RUNTIME_DIR/podman/podman.sock"
    echo "✓ Podman socket found at: $SOCKET_PATH"
elif [ -S "$MANUAL_SOCKET" ]; then
    SOCKET_PATH="$MANUAL_SOCKET"
    echo "✓ Podman socket found at: $SOCKET_PATH"
else
    echo "⚠️  Podman socket not found"

    if [ "$IS_WSL" = true ]; then
        echo "   WSL detected - starting socket manually (no systemd required)"
        mkdir -p "$SOCKET_DIR"

        # Check if socket service is already running
        if pgrep -f "podman.*system.*service" > /dev/null; then
            echo "   ✓ Podman service already running"
            # Find the socket it created
            for sock in "$MANUAL_SOCKET" "$XDG_RUNTIME_DIR/podman/podman.sock" /tmp/podman*.sock; do
                if [ -S "$sock" ]; then
                    SOCKET_PATH="$sock"
                    echo "   ✓ Found socket at: $SOCKET_PATH"
                    break
                fi
            done
        else
            echo "   Starting Podman socket service..."
            echo "   Command: podman system service --time=0 unix://$MANUAL_SOCKET"

            # Start socket in background
            podman system service --time=0 "unix://$MANUAL_SOCKET" &
            PODMAN_PID=$!

            # Wait for socket to be created
            for i in {1..10}; do
                if [ -S "$MANUAL_SOCKET" ]; then
                    SOCKET_PATH="$MANUAL_SOCKET"
                    echo "   ✓ Socket started at: $SOCKET_PATH (PID: $PODMAN_PID)"
                    echo "   Note: Socket will stop when this script exits"
                    break
                fi
                sleep 1
            done
        fi
    else
        echo "   Enable with: systemctl --user enable --now podman.socket"
        echo "   Or system-wide: sudo systemctl enable --now podman.socket"
        echo ""
        echo "   Trying to enable user socket now..."
        systemctl --user enable --now podman.socket 2>/dev/null || true
        sleep 2

        if [ -S "$XDG_RUNTIME_DIR/podman/podman.sock" ]; then
            SOCKET_PATH="$XDG_RUNTIME_DIR/podman/podman.sock"
            echo "   ✓ Socket enabled successfully at: $SOCKET_PATH"
        fi
    fi

    if [ -z "$SOCKET_PATH" ]; then
        echo "❌ Failed to start or find Podman socket"
        echo ""
        echo "Manual start options:"
        echo "  1. With systemd: systemctl --user enable --now podman.socket"
        echo "  2. Without systemd (WSL): podman system service --time=0 unix://$MANUAL_SOCKET &"
        exit 1
    fi
fi

# Check if Node.js is available
if ! command -v node &> /dev/null; then
    echo "❌ Node.js is not installed"
    echo "   Install with: sudo dnf install nodejs (Fedora/RHEL)"
    echo "   or: sudo apt install nodejs (Ubuntu/Debian)"
    exit 1
fi

echo "✓ Node.js is installed: $(node --version)"
echo ""

# Check if node-docker-api is installed locally
if [ ! -d "node_modules/node-docker-api" ]; then
    echo "⚠️  node-docker-api not found, installing..."
    npm install node-docker-api
    echo ""
fi

echo "=== Running node-docker-api compatibility test ==="
echo ""

# Run the Node.js test script
node test-podman-socket.js "$SOCKET_PATH"

echo ""
echo "=== Test Summary ==="
echo "If all tests passed, node-docker-api is compatible with Podman socket!"
echo "Next steps:"
echo "  1. Update docker-compose files to mount Podman socket"
echo "  2. Update session-manager socket path configuration"
echo "  3. Test full session container spawning workflow"
