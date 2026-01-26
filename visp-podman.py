#!/usr/bin/env python3
"""
VISP Control - Unified management tool for VISP Podman deployment

Commands:
  status      Show status of all services and containers
  logs        View logs (replaces visp-logs.sh)
  start       Start service(s)
  stop        Stop service(s)
  restart     Restart service(s) or entire cluster
  install     Link quadlet files to systemd directory
  uninstall   Remove quadlet links from systemd directory
  reload      Reload systemd daemon (after quadlet changes)
  build       Build container images
  exec        Execute command in container
  shell       Open shell in container
"""

import argparse
import subprocess
import sys
from pathlib import Path

# Configuration
QUADLETS_DIR = Path(__file__).parent / "quadlets"
SYSTEMD_QUADLETS_DIR = Path.home() / ".config/containers/systemd"

# Service definitions - order matters for startup
SERVICES = [
    # Networks first
    {"name": "visp-net", "type": "network", "file": "visp-net.network"},
    {"name": "whisper-net", "type": "network", "file": "whisper-net.network"},
    {"name": "octra-net", "type": "network", "file": "octra-net.network"},
    # Then containers in dependency order
    {"name": "mongo", "type": "container", "file": "mongo.container"},
    {"name": "traefik", "type": "container", "file": "traefik.container"},
    {"name": "whisper", "type": "container", "file": "whisper.container"},
    {"name": "wsrng-server", "type": "container", "file": "wsrng-server.container"},
    {
        "name": "session-manager",
        "type": "container",
        "file": "session-manager.container",
    },
    {"name": "apache", "type": "container", "file": "apache.container"},
]

CONTAINER_SERVICES = [s for s in SERVICES if s["type"] == "container"]
NETWORK_SERVICES = [s for s in SERVICES if s["type"] == "network"]


# Colors
class Colors:
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    BLUE = "\033[0;34m"
    CYAN = "\033[0;36m"
    MAGENTA = "\033[0;35m"
    NC = "\033[0m"  # No Color
    BOLD = "\033[1m"


def color(text: str, c: str) -> str:
    """Wrap text in color codes."""
    return f"{c}{text}{Colors.NC}"


def run(
    cmd: list[str], capture: bool = False, check: bool = True
) -> subprocess.CompletedProcess:
    """Run a command."""
    if capture:
        return subprocess.run(cmd, capture_output=True, text=True, check=check)
    return subprocess.run(cmd, check=check)


def run_quiet(cmd: list[str]) -> tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def systemctl(*args) -> subprocess.CompletedProcess:
    """Run systemctl --user command."""
    return run(["systemctl", "--user", *args], capture=True, check=False)


def journalctl(*args) -> None:
    """Run journalctl --user command."""
    subprocess.run(["journalctl", "--user", *args])


# === Status Commands ===


def cmd_status(args):
    """Show status of all services and containers."""
    print(color("=== VISP Service Status ===", Colors.CYAN))
    print()

    # Check systemd services
    for svc in CONTAINER_SERVICES:
        service_name = f"{svc['name']}.service"
        rc, stdout, _ = run_quiet(["systemctl", "--user", "is-active", service_name])
        status = stdout if rc == 0 else "inactive"

        if status == "active":
            symbol = color("●", Colors.GREEN)
            status_text = color(status, Colors.GREEN)
        elif status == "inactive":
            symbol = color("○", Colors.YELLOW)
            status_text = color(status, Colors.YELLOW)
        else:
            symbol = color("✗", Colors.RED)
            status_text = color(status, Colors.RED)

        print(f"  {symbol} {svc['name']}: {status_text}")

    print()
    print(color("=== Quadlet Links ===", Colors.CYAN))

    for svc in SERVICES:
        link_path = SYSTEMD_QUADLETS_DIR / svc["file"]
        target_path = QUADLETS_DIR / svc["file"]

        if link_path.is_symlink():
            actual_target = link_path.resolve()
            if actual_target == target_path.resolve():
                symbol = color("✓", Colors.GREEN)
                status = color("linked", Colors.GREEN)
            else:
                symbol = color("!", Colors.YELLOW)
                status = color(f"linked (wrong target: {actual_target})", Colors.YELLOW)
        elif link_path.exists():
            symbol = color("!", Colors.YELLOW)
            status = color("exists (not a symlink)", Colors.YELLOW)
        else:
            symbol = color("○", Colors.RED)
            status = color("not linked", Colors.RED)

        print(f"  {symbol} {svc['file']}: {status}")

    print()
    print(color("=== Container Status ===", Colors.CYAN))
    run(
        ["podman", "ps", "-a", "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}"],
        check=False,
    )

    print()
    print(color("=== Network Status ===", Colors.CYAN))
    run(["podman", "network", "ls"], check=False)


# === Log Commands ===


def cmd_logs(args):
    """View logs from services."""
    extra_args = []

    if args.follow:
        extra_args.append("-f")
    if args.lines:
        extra_args.extend(["-n", str(args.lines)])
    elif not args.follow:
        extra_args.extend(["-n", "100"])  # Default
    if args.since:
        extra_args.extend(["--since", args.since])
    if args.priority:
        extra_args.extend(["-p", args.priority])

    if args.service == "all" or not args.service:
        # All services
        units = []
        for svc in CONTAINER_SERVICES:
            units.extend(["-u", f"{svc['name']}.service"])
        print(color("=== Viewing logs for all VISP services ===", Colors.CYAN))
        journalctl(*units, *extra_args)
    else:
        # Single service
        print(color(f"=== Viewing logs for {args.service} ===", Colors.CYAN))
        journalctl("-u", f"{args.service}.service", *extra_args)


# === Service Control Commands ===


def cmd_start(args):
    """Start service(s)."""
    services = _resolve_services(args.service)

    for svc in services:
        if svc["type"] == "network":
            continue  # Networks start automatically
        service_name = f"{svc['name']}.service"
        print(f"Starting {service_name}...")
        result = systemctl("start", service_name)
        if result.returncode != 0:
            print(color(f"  Failed: {result.stderr}", Colors.RED))
        else:
            print(color("  Started", Colors.GREEN))


def cmd_stop(args):
    """Stop service(s)."""
    services = _resolve_services(args.service)

    # Stop in reverse order
    for svc in reversed(services):
        if svc["type"] == "network":
            continue
        service_name = f"{svc['name']}.service"
        print(f"Stopping {service_name}...")
        result = systemctl("stop", service_name)
        if result.returncode != 0:
            print(color(f"  Failed: {result.stderr}", Colors.RED))
        else:
            print(color("  Stopped", Colors.GREEN))


def cmd_restart(args):
    """Restart service(s) or entire cluster."""
    services = _resolve_services(args.service)

    if args.service == "all":
        print(color("=== Restarting entire VISP cluster ===", Colors.CYAN))
        print()

        # Stop in reverse order
        print(color("Stopping services...", Colors.YELLOW))
        for svc in reversed([s for s in services if s["type"] == "container"]):
            service_name = f"{svc['name']}.service"
            print(f"  Stopping {svc['name']}...")
            systemctl("stop", service_name)

        print()
        print(color("Starting services...", Colors.GREEN))
        for svc in [s for s in services if s["type"] == "container"]:
            service_name = f"{svc['name']}.service"
            print(f"  Starting {svc['name']}...")
            result = systemctl("start", service_name)
            if result.returncode != 0:
                print(color(f"    Failed: {result.stderr}", Colors.RED))
    else:
        for svc in services:
            if svc["type"] == "network":
                continue
            service_name = f"{svc['name']}.service"
            print(f"Restarting {service_name}...")
            result = systemctl("restart", service_name)
            if result.returncode != 0:
                print(color(f"  Failed: {result.stderr}", Colors.RED))
            else:
                print(color("  Restarted", Colors.GREEN))


# === Installation Commands ===


def cmd_install(args):
    """Link quadlet files to systemd directory."""
    SYSTEMD_QUADLETS_DIR.mkdir(parents=True, exist_ok=True)

    services = _resolve_services(args.service)

    for svc in services:
        source = QUADLETS_DIR / svc["file"]
        target = SYSTEMD_QUADLETS_DIR / svc["file"]

        if not source.exists():
            print(color(f"  ✗ {svc['file']}: source not found", Colors.RED))
            continue

        if target.is_symlink():
            if target.resolve() == source.resolve():
                print(f"  ○ {svc['file']}: already linked")
                continue
            else:
                if args.force:
                    target.unlink()
                else:
                    print(
                        color(
                            f"  ! {svc['file']}: exists with different target (use --force)",
                            Colors.YELLOW,
                        )
                    )
                    continue
        elif target.exists():
            if args.force:
                target.unlink()
            else:
                print(
                    color(
                        f"  ! {svc['file']}: exists as file (use --force)",
                        Colors.YELLOW,
                    )
                )
                continue

        target.symlink_to(source.resolve())
        print(color(f"  ✓ {svc['file']}: linked", Colors.GREEN))

    print()
    print("Run 'visp-ctl reload' to apply changes.")


def cmd_uninstall(args):
    """Remove quadlet links from systemd directory."""
    services = _resolve_services(args.service)

    # Stop services first
    if not args.keep_running:
        print(color("Stopping services...", Colors.YELLOW))
        for svc in reversed([s for s in services if s["type"] == "container"]):
            service_name = f"{svc['name']}.service"
            systemctl("stop", service_name)

    print()
    print(color("Removing links...", Colors.CYAN))

    for svc in services:
        target = SYSTEMD_QUADLETS_DIR / svc["file"]

        if target.is_symlink():
            target.unlink()
            print(color(f"  ✓ {svc['file']}: removed", Colors.GREEN))
        elif target.exists():
            print(color(f"  ! {svc['file']}: not a symlink, skipping", Colors.YELLOW))
        else:
            print(f"  ○ {svc['file']}: not installed")

    print()
    print("Run 'visp-ctl reload' to apply changes.")


def cmd_reload(args):
    """Reload systemd daemon to pick up quadlet changes."""
    print("Reloading systemd daemon...")
    result = systemctl("daemon-reload")
    if result.returncode == 0:
        print(color("Done. Quadlet changes are now active.", Colors.GREEN))
    else:
        print(color(f"Failed: {result.stderr}", Colors.RED))


# === Container Commands ===


def cmd_exec(args):
    """Execute command in container."""
    container = f"systemd-{args.container}"
    run(["podman", "exec", "-it", container, *args.command], check=False)


def cmd_shell(args):
    """Open shell in container."""
    container = f"systemd-{args.container}"
    shell = args.shell or "/bin/bash"
    run(["podman", "exec", "-it", container, shell], check=False)


def cmd_build(args):
    """Build container images (placeholder for future)."""
    print("Build functionality coming soon...")
    print("For now, use: python visp_deploy.py build")


# === Debug Commands ===


def cmd_debug(args):
    """Debug startup issues for a service."""
    service = args.service
    service_name = f"{service}.service"

    print(color(f"=== Debug info for {service} ===", Colors.CYAN))
    print()

    # Service status
    print(color("Service Status:", Colors.YELLOW))
    systemctl("status", service_name)
    print()

    # Recent logs
    print(color("Recent Logs (last 50 lines):", Colors.YELLOW))
    journalctl("-u", service_name, "-n", "50", "--no-pager")
    print()

    # Check if container exists
    print(color("Container Info:", Colors.YELLOW))
    container = f"systemd-{service}"
    rc, stdout, stderr = run_quiet(["podman", "inspect", container])
    if rc == 0:
        run(
            [
                "podman",
                "inspect",
                container,
                "--format",
                "Name: {{.Name}}\nState: {{.State.Status}}\nStarted: {{.State.StartedAt}}\nImage: {{.Image}}",
            ],
            check=False,
        )
    else:
        print(color(f"Container not found: {container}", Colors.RED))
    print()

    # Check quadlet link
    print(color("Quadlet Link:", Colors.YELLOW))
    svc_info = next((s for s in SERVICES if s["name"] == service), None)
    if svc_info:
        link_path = SYSTEMD_QUADLETS_DIR / svc_info["file"]
        if link_path.is_symlink():
            print(f"  {link_path} -> {link_path.resolve()}")
        elif link_path.exists():
            print(color(f"  {link_path} exists but is not a symlink", Colors.YELLOW))
        else:
            print(color(f"  {link_path} does not exist", Colors.RED))


# === Network Info ===


def cmd_network(args):
    """Show network information and DNS status."""
    print(color("=== Network Backend ===", Colors.CYAN))
    rc, stdout, _ = run_quiet(
        ["podman", "info", "--format", "{{.Host.NetworkBackend}}"]
    )
    backend = stdout if rc == 0 else "unknown"
    if backend == "netavark":
        print(color(f"  Backend: {backend} (recommended)", Colors.GREEN))
    else:
        print(
            color(
                f"  Backend: {backend} (CNI - consider upgrading to netavark)",
                Colors.YELLOW,
            )
        )
    print()

    print(color("=== VISP Networks ===", Colors.CYAN))
    for svc in NETWORK_SERVICES:
        net_name = f"systemd-{svc['name']}"
        rc, _, _ = run_quiet(["podman", "network", "exists", net_name])
        if rc == 0:
            print(color(f"\n  {net_name}:", Colors.GREEN))
            run(
                [
                    "podman",
                    "network",
                    "inspect",
                    net_name,
                    "--format",
                    "    DNS: {{.DNSEnabled}}\n    Internal: {{.Internal}}\n    Driver: {{.Driver}}",
                ],
                check=False,
            )
        else:
            print(color(f"\n  {net_name}: not found", Colors.RED))

    print()
    print(color("=== Container Network Connections ===", Colors.CYAN))
    rc, stdout, _ = run_quiet(["podman", "ps", "--format", "{{.Names}}"])
    if rc == 0 and stdout:
        for container in stdout.split("\n"):
            if container.startswith("systemd-"):
                rc, nets, _ = run_quiet(
                    [
                        "podman",
                        "inspect",
                        container,
                        "--format",
                        "{{range .NetworkSettings.Networks}}{{.NetworkID}} {{end}}",
                    ]
                )
                print(f"  {container}: {nets if nets else 'none'}")


# === Helpers ===


def _resolve_services(service_arg: str) -> list[dict]:
    """Resolve service argument to list of service dicts."""
    if service_arg == "all":
        return SERVICES

    svc = next((s for s in SERVICES if s["name"] == service_arg), None)
    if svc:
        return [svc]

    print(color(f"Unknown service: {service_arg}", Colors.RED))
    print(f"Available: {', '.join(s['name'] for s in SERVICES)}")
    sys.exit(1)


def _get_service_names() -> list[str]:
    """Get list of service names for argparse choices."""
    return ["all"] + [s["name"] for s in SERVICES]


# === Main ===


def main():
    parser = argparse.ArgumentParser(
        description="VISP Control - Unified management tool for VISP Podman deployment",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  visp-ctl status              # Show all service status
  visp-ctl logs -f             # Follow all logs
  visp-ctl logs session-manager -n 200  # Last 200 lines from session-manager
  visp-ctl restart all         # Restart entire cluster
  visp-ctl restart mongo       # Restart just mongo
  visp-ctl install all         # Link all quadlets
  visp-ctl reload              # Reload systemd after quadlet changes
  visp-ctl debug mongo         # Debug mongo startup issues
  visp-ctl shell session-manager  # Open bash in session-manager
  visp-ctl exec mongo mongosh  # Run mongosh in mongo container
""",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # status
    subparsers.add_parser("status", aliases=["s"], help="Show status of all services")

    # logs
    p_logs = subparsers.add_parser(
        "logs", aliases=["l"], help="View logs from services"
    )
    p_logs.add_argument(
        "service", nargs="?", default="all", help="Service name or 'all'"
    )
    p_logs.add_argument("-f", "--follow", action="store_true", help="Follow logs")
    p_logs.add_argument("-n", "--lines", type=int, help="Number of lines to show")
    p_logs.add_argument("--since", help="Show logs since TIME (e.g., '1 hour ago')")
    p_logs.add_argument("-p", "--priority", help="Filter by priority")

    # start
    p_start = subparsers.add_parser("start", help="Start service(s)")
    p_start.add_argument(
        "service", default="all", nargs="?", help="Service name or 'all'"
    )

    # stop
    p_stop = subparsers.add_parser("stop", help="Stop service(s)")
    p_stop.add_argument(
        "service", default="all", nargs="?", help="Service name or 'all'"
    )

    # restart
    p_restart = subparsers.add_parser(
        "restart", aliases=["r"], help="Restart service(s)"
    )
    p_restart.add_argument(
        "service", default="all", nargs="?", help="Service name or 'all'"
    )

    # install
    p_install = subparsers.add_parser(
        "install", aliases=["i"], help="Link quadlet files to systemd"
    )
    p_install.add_argument(
        "service", default="all", nargs="?", help="Service name or 'all'"
    )
    p_install.add_argument(
        "-f", "--force", action="store_true", help="Overwrite existing links"
    )

    # uninstall
    p_uninstall = subparsers.add_parser(
        "uninstall", aliases=["u"], help="Remove quadlet links"
    )
    p_uninstall.add_argument(
        "service", default="all", nargs="?", help="Service name or 'all'"
    )
    p_uninstall.add_argument(
        "--keep-running", action="store_true", help="Don't stop services first"
    )

    # reload
    subparsers.add_parser("reload", help="Reload systemd daemon")

    # debug
    p_debug = subparsers.add_parser("debug", aliases=["d"], help="Debug startup issues")
    p_debug.add_argument("service", help="Service to debug")

    # exec
    p_exec = subparsers.add_parser(
        "exec", aliases=["e"], help="Execute command in container"
    )
    p_exec.add_argument("container", help="Container name (without systemd- prefix)")
    p_exec.add_argument("command", nargs="+", help="Command to run")

    # shell
    p_shell = subparsers.add_parser(
        "shell", aliases=["sh"], help="Open shell in container"
    )
    p_shell.add_argument("container", help="Container name (without systemd- prefix)")
    p_shell.add_argument(
        "--shell", default="/bin/bash", help="Shell to use (default: /bin/bash)"
    )

    # build
    subparsers.add_parser("build", aliases=["b"], help="Build container images")

    # network
    subparsers.add_parser(
        "network", aliases=["n", "net"], help="Show network info and DNS status"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # Dispatch commands
    cmd_map = {
        "status": cmd_status,
        "s": cmd_status,
        "logs": cmd_logs,
        "l": cmd_logs,
        "start": cmd_start,
        "stop": cmd_stop,
        "restart": cmd_restart,
        "r": cmd_restart,
        "install": cmd_install,
        "i": cmd_install,
        "uninstall": cmd_uninstall,
        "u": cmd_uninstall,
        "reload": cmd_reload,
        "debug": cmd_debug,
        "d": cmd_debug,
        "exec": cmd_exec,
        "e": cmd_exec,
        "shell": cmd_shell,
        "sh": cmd_shell,
        "build": cmd_build,
        "b": cmd_build,
        "network": cmd_network,
        "n": cmd_network,
        "net": cmd_network,
    }

    handler = cmd_map.get(args.command)
    if handler:
        try:
            handler(args)
        except KeyboardInterrupt:
            print()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
