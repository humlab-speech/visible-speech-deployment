#!/bin/bash
# Fix permissions for VISP container access
# Run this script when containers have permission errors accessing logs

set -e

echo "🔒 Fixing repository and mount permissions for container access..."

# Fix wsrng-server logs
if [ -d "wsrng-server/logs" ]; then
    echo "  → wsrng-server/logs"
    chmod 777 wsrng-server/logs
    touch wsrng-server/logs/wsrng-server.log
    chmod 666 wsrng-server/logs/wsrng-server.log
fi

# Fix session-manager logs
if [ -d "session-manager/logs" ]; then
    echo "  → session-manager/logs"
    chmod 777 session-manager/logs
    if [ -f "session-manager/logs/session-manager.log" ]; then
        chmod 666 session-manager/logs/session-manager.log
    fi
fi

# Fix emu-webapp-server logs
if [ -d "emu-webapp-server/logs" ]; then
    echo "  → emu-webapp-server/logs"
    chmod 777 emu-webapp-server/logs
    if [ -f "emu-webapp-server/logs/emu-webapp-server.log" ]; then
        chmod 666 emu-webapp-server/logs/emu-webapp-server.log
    fi
fi

# Fix mounts directory
if [ -d "mounts" ]; then
    echo "  → mounts/"
    find mounts -type d -name "logs" -exec chmod 777 {} \;
    find mounts -type f -name "*.log" -exec chmod 666 {} \;
fi

echo "✅ Permissions fixed!"
echo ""
echo "Now you can restart services:"
echo "  docker compose restart"
