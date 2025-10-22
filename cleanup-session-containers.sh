#!/bin/bash

# Script to stop and remove all session containers
# Handles both legacy "hsapp-session-" and current "visp-session-" naming patterns
# Usage: ./cleanup-session-containers.sh [--all|--stopped|--running]

set -e

MODE="${1:-all}"

echo "=== Session Container Cleanup ==="
echo "Mode: $MODE"
echo ""

case "$MODE" in
    --all)
        echo "Finding all session containers (legacy hsapp-session-* and current visp-session-*)..."
        # Get containers matching session patterns but exclude the session-manager service
        ALL_CONTAINERS=$(docker ps -a --filter "name=hsapp-session-" --filter "name=visp-session-" --format "{{.ID}} {{.Names}}")
        CONTAINERS=""
        while read -r line; do
            if [ -n "$line" ]; then
                ID=$(echo "$line" | awk '{print $1}')
                NAME=$(echo "$line" | awk '{print $2}')
                # Exclude session-manager service containers
                if [[ ! "$NAME" =~ session-manager ]]; then
                    CONTAINERS="${CONTAINERS} $ID"
                fi
            fi
        done <<< "$ALL_CONTAINERS"
        CONTAINERS=$(echo "$CONTAINERS" | xargs)
        ;;
    --stopped)
        echo "Finding stopped session containers..."
        # Get containers matching session patterns but exclude the session-manager service
        ALL_CONTAINERS=$(docker ps -a --filter "name=hsapp-session-" --filter "name=visp-session-" --filter "status=exited" --format "{{.ID}} {{.Names}}")
        CONTAINERS=""
        while read -r line; do
            if [ -n "$line" ]; then
                ID=$(echo "$line" | awk '{print $1}')
                NAME=$(echo "$line" | awk '{print $2}')
                # Exclude session-manager service containers
                if [[ ! "$NAME" =~ session-manager ]]; then
                    CONTAINERS="${CONTAINERS} $ID"
                fi
            fi
        done <<< "$ALL_CONTAINERS"
        CONTAINERS=$(echo "$CONTAINERS" | xargs)
        ;;
    --running)
        echo "Finding running session containers..."
        # Get containers matching session patterns but exclude the session-manager service
        ALL_CONTAINERS=$(docker ps --filter "name=hsapp-session-" --filter "name=visp-session-" --format "{{.ID}} {{.Names}}")
        CONTAINERS=""
        while read -r line; do
            if [ -n "$line" ]; then
                ID=$(echo "$line" | awk '{print $1}')
                NAME=$(echo "$line" | awk '{print $2}')
                # Exclude session-manager service containers
                if [[ ! "$NAME" =~ session-manager ]]; then
                    CONTAINERS="${CONTAINERS} $ID"
                fi
            fi
        done <<< "$ALL_CONTAINERS"
        CONTAINERS=$(echo "$CONTAINERS" | xargs)
        ;;
    *)
        echo "Usage: $0 [--all|--stopped|--running]"
        echo ""
        echo "  --all      Stop and remove all session containers (default)"
        echo "  --stopped  Remove only stopped session containers"
        echo "  --running  Stop and remove only running session containers"
        exit 1
        ;;
esac

if [ -z "$CONTAINERS" ]; then
    echo "No session containers found."
    exit 0
fi

echo "Found containers:"
docker ps -a --filter "name=hsapp-session-" --filter "name=visp-session-" --format "{{.ID}} {{.Names}}" | while read -r line; do
    if [ -n "$line" ]; then
        ID=$(echo "$line" | awk '{print $1}')
        NAME=$(echo "$line" | awk '{print $2}')
        # Only show non-session-manager containers
        if [[ ! "$NAME" =~ session-manager ]]; then
            docker ps -a --filter "id=$ID" --format "table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.CreatedAt}}"
        fi
    fi
done
echo ""

COUNT=$(echo "$CONTAINERS" | wc -l)
echo "Total: $COUNT container(s)"
echo ""

read -p "Proceed with cleanup? (y/N) " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 0
fi

echo ""
echo "Stopping and removing containers..."
for container in $CONTAINERS; do
    echo "Processing: $container"

    # Stop if running
    if docker ps -q --filter "id=$container" | grep -q .; then
        echo "  - Stopping..."
        docker stop "$container" > /dev/null 2>&1 || true
    fi

    # Remove
    echo "  - Removing..."
    docker rm "$container" > /dev/null 2>&1 || true
    echo "  ✓ Done"
done

echo ""
echo "=== Cleanup Complete ==="
echo "Removed $COUNT container(s)"
