#!/bin/bash
# Fix permissions for VISP container access with podman rootless user namespace
# This script uses 'podman unshare' to set ownership that containers can access
#
# Container user mappings (podman rootless):
#   - Host user (1000) → Container root (0)
#   - Container www-data (33) for Apache
#   - Container node user (1000) → Container root in namespace (0)
#   - Container mongodb (999) for MongoDB
#
# IMPORTANT: Do NOT run this script with sudo when using podman!
# Podman unshare must run as your regular user to work correctly.

set -e

# Check if running as root with podman (this is wrong!)
if [ "$(id -u)" -eq 0 ] && command -v podman &> /dev/null; then
    echo "❌ ERROR: Do not run this script with sudo when using podman!"
    echo ""
    echo "Podman unshare needs to run as your regular user to access the user namespace."
    echo "Run this script without sudo:"
    echo "  ./fix-permissions.sh"
    echo ""
    exit 1
fi

# Detect if we're using podman or docker
if command -v podman &> /dev/null; then
    RUNTIME="podman"
    USE_UNSHARE=true
elif command -v docker &> /dev/null; then
    RUNTIME="docker"
    USE_UNSHARE=false
else
    echo "❌ Neither podman nor docker found!"
    exit 1
fi

echo "🔒 Fixing VISP permissions for container access..."
echo "   Runtime: $RUNTIME"
echo ""

# Function to fix ownership using podman unshare
fix_ownership() {
    local path="$1"
    local uid="$2"
    local gid="$3"
    local description="$4"
    
    if [ ! -e "$path" ]; then
        echo "  ⚠️  Skipping $path (does not exist)"
        return
    fi
    
    echo "  → $description"
    
    if [ "$USE_UNSHARE" = true ]; then
        # Use podman unshare to set ownership in the user namespace
        podman unshare chown -R "$uid:$gid" "$path"
    else
        # For docker, just use regular chown (requires sudo for non-current user)
        if [ "$uid" -eq "$(id -u)" ] && [ "$gid" -eq "$(id -g)" ]; then
            chown -R "$uid:$gid" "$path"
        else
            echo "    ⚠️  Skipping (docker requires sudo for non-current user ownership)"
        fi
    fi
}

# Apache container runs as www-data (33:33)
echo "📁 Apache (www-data 33:33):"
fix_ownership "external/webclient" 33 33 "external/webclient"
fix_ownership "mounts/apache" 33 33 "mounts/apache (logs, uploads, configs)"
fix_ownership "mounts/webapi" 33 33 "mounts/webapi (logs)"
echo ""

# Node.js services run as node user (1000:1000 in container = 0:0 in namespace)
# In podman rootless, host UID 1000 maps to container root (0)
# So we want these owned by 0:0 in the namespace (which is our user on the host)
echo "📁 Node.js services (root/node 0:0 in namespace):"
fix_ownership "external/session-manager" 0 0 "external/session-manager"
fix_ownership "external/emu-webapp-server" 0 0 "external/emu-webapp-server"
fix_ownership "external/wsrng-server" 0 0 "external/wsrng-server"
fix_ownership "external/EMU-webApp" 0 0 "external/EMU-webApp"
fix_ownership "external/container-agent" 0 0 "external/container-agent"
fix_ownership "mounts/session-manager" 0 0 "mounts/session-manager"
fix_ownership "mounts/emu-webapp-server" 0 0 "mounts/emu-webapp-server"
echo ""

# Shared repositories - needs to be accessible by both Apache and Node services
# Set to www-data since Apache is more restrictive
echo "📁 Shared resources (www-data 33:33):"
fix_ownership "mounts/repositories" 33 33 "mounts/repositories"
fix_ownership "mounts/repository-template" 33 33 "mounts/repository-template"
fix_ownership "mounts/transcription-queued" 33 33 "mounts/transcription-queued"
echo ""

# MongoDB runs as mongodb user (999:999)
echo "📁 MongoDB (999:999):"
fix_ownership "mounts/mongo" 999 999 "mounts/mongo (data, logs)"
echo ""

# Other services
echo "📁 Other services:"
fix_ownership "mounts/octra" 0 0 "mounts/octra"
fix_ownership "mounts/whisper" 0 0 "mounts/whisper"
echo ""

# Certs should be readable by all
echo "📁 Certificates:"
fix_ownership "certs" 0 0 "certs"
echo ""

echo "✅ Permissions fixed!"
echo ""
if [ "$USE_UNSHARE" = true ]; then
    echo "Note: Using 'podman unshare' for proper rootless container ownership"
else
    echo "Note: Using regular chown (docker mode)"
fi
echo ""
echo "Now you can restart services:"
echo "  podman compose restart"
echo "  # or"
echo "  docker compose restart"
