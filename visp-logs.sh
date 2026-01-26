#!/bin/bash
# VISP Log Viewer - View logs from systemd quadlet services
# Usage: ./visp-logs.sh [service|all] [options]

set -e

# Service names (without .service suffix)
SERVICES=(
    "mongo"
    "session-manager"
    "apache"
    "traefik"
    "whisper"
    "wsrng-server"
)

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

usage() {
    echo "VISP Log Viewer"
    echo ""
    echo "Usage: $0 [command] [options]"
    echo ""
    echo "Commands:"
    echo "  all                 Show logs from all VISP services (default)"
    echo "  <service>           Show logs from specific service"
    echo "  follow, f           Follow all logs in real-time"
    echo "  status, s           Show status of all services"
    echo "  list, l             List available services"
    echo ""
    echo "Services: ${SERVICES[*]}"
    echo ""
    echo "Options (passed to journalctl):"
    echo "  -f, --follow        Follow logs in real-time"
    echo "  -n, --lines=N       Show last N lines (default: 100)"
    echo "  --since=TIME        Show logs since TIME (e.g., '1 hour ago', '2024-01-01')"
    echo "  --until=TIME        Show logs until TIME"
    echo "  -p, --priority=     Filter by priority (emerg,alert,crit,err,warning,notice,info,debug)"
    echo ""
    echo "Examples:"
    echo "  $0                          # All logs, last 100 lines"
    echo "  $0 session-manager          # Session manager logs only"
    echo "  $0 session-manager -f       # Follow session manager logs"
    echo "  $0 all -f                   # Follow all VISP logs"
    echo "  $0 follow                   # Follow all VISP logs (shorthand)"
    echo "  $0 all --since='10 min ago' # Logs from last 10 minutes"
    echo "  $0 mongo -n 500             # Last 500 lines from mongo"
    echo "  $0 status                   # Show service status"
}

show_status() {
    echo -e "${CYAN}=== VISP Service Status ===${NC}"
    echo ""
    for svc in "${SERVICES[@]}"; do
        status=$(systemctl --user is-active "$svc.service" 2>/dev/null || echo "inactive")
        if [ "$status" = "active" ]; then
            echo -e "  ${GREEN}●${NC} $svc: ${GREEN}$status${NC}"
        elif [ "$status" = "inactive" ]; then
            echo -e "  ${YELLOW}○${NC} $svc: ${YELLOW}$status${NC}"
        else
            echo -e "  ${RED}✗${NC} $svc: ${RED}$status${NC}"
        fi
    done
    echo ""

    # Show container status
    echo -e "${CYAN}=== Container Status ===${NC}"
    podman ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E "(systemd-|NAMES)" || true
}

list_services() {
    echo "Available VISP services:"
    for svc in "${SERVICES[@]}"; do
        echo "  - $svc"
    done
}

view_logs() {
    local service="$1"
    shift
    local extra_args=("$@")

    # Default to 100 lines if no -n specified and not following
    local has_lines=false
    local has_follow=false
    for arg in "${extra_args[@]}"; do
        [[ "$arg" == "-n"* ]] || [[ "$arg" == "--lines"* ]] && has_lines=true
        [[ "$arg" == "-f" ]] || [[ "$arg" == "--follow" ]] && has_follow=true
    done

    if [ "$has_lines" = false ] && [ "$has_follow" = false ]; then
        extra_args+=("-n" "100")
    fi

    if [ "$service" = "all" ]; then
        # Build unit filter for all services
        local units=""
        for svc in "${SERVICES[@]}"; do
            units="$units -u $svc.service"
        done
        echo -e "${CYAN}=== Viewing logs for all VISP services ===${NC}"
        # shellcheck disable=SC2086
        journalctl --user $units "${extra_args[@]}"
    else
        echo -e "${CYAN}=== Viewing logs for $service ===${NC}"
        journalctl --user -u "$service.service" "${extra_args[@]}"
    fi
}

# Main
if [ $# -eq 0 ]; then
    view_logs "all"
    exit 0
fi

case "$1" in
    -h|--help|help)
        usage
        ;;
    status|s)
        show_status
        ;;
    list|l)
        list_services
        ;;
    follow|f)
        shift
        view_logs "all" -f "$@"
        ;;
    all)
        shift
        view_logs "all" "$@"
        ;;
    *)
        # Check if it's a known service
        service="$1"
        found=false
        for svc in "${SERVICES[@]}"; do
            if [ "$svc" = "$service" ]; then
                found=true
                break
            fi
        done

        if [ "$found" = true ]; then
            shift
            view_logs "$service" "$@"
        else
            # Maybe it's an option for "all"
            view_logs "all" "$@"
        fi
        ;;
esac
