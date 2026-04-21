#!/bin/bash
#
# WSL Development Bridge Startup Script
# =====================================
# ⚠️  WSL DEVELOPMENT ONLY - DO NOT USE IN PRODUCTION
#
# This script starts an nginx container via DOCKER DESKTOP that bridges
# Windows networking to WSL2 Podman containers, allowing visp.local to
# work from Windows. Docker Desktop handles the Windows port forwarding magic.
#
# Architecture:
#   Windows Browser → Docker Desktop (80/443) → nginx container → Podman containers
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
CONTAINER_NAME="wsl-nginx-bridge"

echo "================================================"
echo "  WSL Development Bridge"
echo "  ⚠️  FOR WSL DEVELOPMENT ONLY"
echo "================================================"
echo ""

# Check if Docker is available
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not available. Install Docker Desktop for Windows."
    exit 1
fi

# Get WSL IP for nginx to reach Podman containers
WSL_IP=$(hostname -I | awk '{print $1}')
echo "WSL IP: ${WSL_IP}"
echo ""

# Check if already running
if docker ps --format "{{.Names}}" | grep -q "^${CONTAINER_NAME}$"; then
    echo "✓ Bridge already running"
    echo ""
    echo "To restart:"
    echo "  docker restart ${CONTAINER_NAME}"
    echo ""
    echo "To stop:"
    echo "  docker stop ${CONTAINER_NAME}"
    echo "  docker rm ${CONTAINER_NAME}"
    exit 0
fi

# Remove old container if exists
if docker ps -a --format "{{.Names}}" | grep -q "^${CONTAINER_NAME}$"; then
    echo "Removing old container..."
    docker rm -f ${CONTAINER_NAME}
fi

echo "Starting nginx bridge container via Docker Desktop..."
echo "(Using nginx template system for dynamic IP injection)"
echo ""

# Start nginx container via Docker with template processing
# The .template file will be processed and ${PODMAN_IP} substituted
docker run -d \
    --name ${CONTAINER_NAME} \
    -p 80:80 \
    -p 443:443 \
    -e PODMAN_IP=${WSL_IP} \
    -v "${SCRIPT_DIR}/nginx.conf.template:/etc/nginx/templates/default.conf.template:ro" \
    -v "${PROJECT_ROOT}/certs:/certs:ro" \
    --restart unless-stopped \
    nginx:alpine

echo "✓ Bridge started successfully!"
echo ""
echo "Container: ${CONTAINER_NAME}"
echo "Ports: 80 (HTTP) → 443 (HTTPS)"
echo "Backend: systemd-apache via ${WSL_IP}:8081"
echo ""
echo "You can now access from Windows:"
echo "  https://visp.local"
echo "  https://app.visp.local"
echo "  https://emu-webapp.visp.local"
echo "  (etc.)"
echo ""
echo "Make sure your Windows hosts file has:"
echo "  127.0.0.1  visp.local app.visp.local emu-webapp.visp.local ..."
echo ""
echo "To view logs:"
echo "  docker logs -f ${CONTAINER_NAME}"
echo ""
echo "Note: Main services run on Podman, only this bridge uses Docker."
echo ""
