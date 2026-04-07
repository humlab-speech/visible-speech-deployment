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
  mode        Show or set deployment mode (dev/prod)
  build       Build container images (supports --no-cache, --pull)
  exec        Execute command in container
  shell       Open shell in container
  backup      Backup MongoDB database to tar.gz
  restore     Restore MongoDB database from backup

Build examples:
  ./visp-podman.py build                         # Build all services
  ./visp-podman.py build session-manager         # Build single service
  ./visp-podman.py build container-agent         # Build container-agent (Node.js)
  ./visp-podman.py build webclient --config visp.dev # Build webclient with visp.dev config (default)
  ./visp-podman.py build --no-cache              # Clean rebuild (no cache)
  ./visp-podman.py build --list                  # List buildable services

Backup/Restore examples:
  ./visp-podman.py backup                        # Backup to current directory
  ./visp-podman.py backup -o /backups/db.tar.gz  # Backup to specific path
  ./visp-podman.py restore backup.tar.gz         # Restore with confirmation
  ./visp-podman.py restore backup.tar.gz --force # Restore without confirmation

Mode examples:
  ./visp-podman.py mode                          # Show current mode
  ./visp-podman.py mode dev                      # Set to development mode
  ./visp-podman.py mode prod                     # Set to production mode
  ./visp-podman.py install --mode prod --force   # Install prod quadlets
"""

import argparse
import os
import subprocess
import sys
import threading
from pathlib import Path

from vispctl.build import BuildManager
from vispctl.cleanup_containers import cleanup_containers
from vispctl.images import ImageManager
from vispctl.network import NetworkManager

# Use the new modular managers where appropriate
from vispctl.runner import Runner
from vispctl.service import Service
from vispctl.service_manager import ServiceManager

# Configuration
PROJECT_DIR = Path(__file__).parent.resolve()
QUADLETS_BASE_DIR = Path(__file__).parent / "quadlets"
SYSTEMD_QUADLETS_DIR = Path.home() / ".config/containers/systemd"
CONTAINERS_CONF = Path.home() / ".config/containers/containers.conf"
MODE_FILE = Path(__file__).parent / ".visp-mode"


def render_quadlet_template(content: str) -> str:
    """Replace template placeholders with actual system values."""
    content = content.replace("@@PROJECT_DIR@@", str(PROJECT_DIR))
    content = content.replace("@@UID@@", str(os.getuid()))
    return content


# Default mode
DEFAULT_MODE = "dev"


def get_current_mode() -> str:
    """Get the current deployment mode from .visp-mode file."""
    if MODE_FILE.exists():
        return MODE_FILE.read_text().strip()
    return DEFAULT_MODE


def set_current_mode(mode: str) -> None:
    """Set the current deployment mode."""
    MODE_FILE.write_text(mode)


def get_quadlets_dir(mode: str = None) -> Path:
    """Get the quadlets directory for the specified mode."""
    if mode is None:
        mode = get_current_mode()
    return QUADLETS_BASE_DIR / mode


# Service definitions - order matters for startup
SERVICES = [
    # Networks first
    Service("visp-net", "network", "visp-net.network"),
    Service("octra-net", "network", "octra-net.network"),
    # Then containers in dependency order
    Service("mongo", "container", "mongo.container"),
    Service("matomo-db", "container", "matomo-db.container"),
    Service("matomo", "container", "matomo.container"),
    Service("traefik", "container", "traefik.container"),
    Service("whisperx", "container", "whisperx.container"),
    Service("wsrng-server", "container", "wsrng-server.container"),
    Service("session-manager", "container", "session-manager.container"),
    Service("emu-webapp", "container", "emu-webapp.container"),
    Service("emu-webapp-server", "container", "emu-webapp-server.container"),
    Service("octra", "container", "octra.container"),
    Service("apache", "container", "apache.container"),
]

CONTAINER_SERVICES = [s for s in SERVICES if s.type == "container"]
NETWORK_SERVICES = [s for s in SERVICES if s.type == "network"]

# Container-internal log files that are NOT visible in journalctl.
# These are files inside the container that must be read via `podman exec`.
# Format: service_name -> list of (label, container_path) tuples.
CONTAINER_LOG_FILES: dict[str, list[tuple[str, str]]] = {
    "apache": [
        ("api", "/var/log/api/webapi.log"),
        ("api-debug", "/var/log/api/webapi.debug.log"),
        ("php-errors", "/var/log/api/php_error.log"),
        ("apache-error", "/var/log/apache2/visp.local-error.log"),
        ("octra-error", "/var/log/apache2/octra-error.log"),
        ("emu-error", "/var/log/apache2/emu-webapp-error.log"),
        ("shibboleth", "/var/log/shibboleth/shibd.log"),
        ("shibboleth-warn", "/var/log/shibboleth/shibd_warn.log"),
    ],
}


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


# Module-level Runner instance for consistent subprocess handling
RUNNER = Runner()


def run(cmd: list[str], capture: bool = False, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    """Run a command via the shared Runner."""
    return RUNNER.run(cmd, capture=capture, check=check, **kwargs)


def run_quiet(cmd: list[str]) -> tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr) via Runner."""
    return RUNNER.run_quiet(cmd)


def systemctl(*args) -> subprocess.CompletedProcess:
    """Run systemctl --user command via Runner."""
    return RUNNER.systemctl(*args)


def journalctl(*args) -> subprocess.CompletedProcess:
    """Run journalctl --user command via Runner."""
    return RUNNER.journalctl(*args)


def load_env_vars(env_file_path: Path) -> dict:
    """Load environment variables from a .env file."""
    env_vars = {}
    if not env_file_path.exists():
        return env_vars

    with open(env_file_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                env_vars[key.strip()] = value.strip()
    return env_vars


# Deprecated secret wrapper functions removed — use `vispctl.secrets.SecretManager` directly.
# These wrappers were left behind during refactoring and are now deleted to remove
# dead code and avoid confusion.


# === Status Commands ===


def cmd_status(args):
    """Show status of all services and containers."""
    print(color("=== VISP Service Status ===", Colors.CYAN))
    print()

    # Use ServiceManager for service status
    sm = ServiceManager(Runner(), SERVICES)
    sm.status()

    # (ServiceManager prints service status). Continue with quadlet links and other info below.

    print()
    print(color("=== Quadlet Links ===", Colors.CYAN))
    current_mode = get_current_mode()
    quadlets_dir = get_quadlets_dir(current_mode)
    print(f"  Mode: {color(current_mode, Colors.MAGENTA)}")
    print()

    drift_warnings = []
    for svc in SERVICES:
        link_path = SYSTEMD_QUADLETS_DIR / svc.file
        target_path = quadlets_dir / svc.file

        if link_path.is_symlink():
            actual_target = link_path.resolve()
            if actual_target == target_path.resolve():
                symbol = color("✓", Colors.GREEN)
                status = color("linked", Colors.GREEN)
            elif actual_target.parent.name in ("dev", "prod"):
                symbol = color("!", Colors.YELLOW)
                linked_mode = actual_target.parent.name
                status = color(f"linked ({linked_mode} mode)", Colors.YELLOW)
            elif actual_target.parent.name == "quadlets":
                # Old-style link to root quadlets directory
                symbol = color("!", Colors.YELLOW)
                status = color("linked (legacy, run install --force)", Colors.YELLOW)
            else:
                symbol = color("!", Colors.YELLOW)
                status = color(f"linked (unknown: {actual_target})", Colors.YELLOW)
        elif link_path.exists():
            # Rendered file (new-style template install) — check for drift
            if target_path.exists():
                expected = render_quadlet_template(target_path.read_text())
                installed = link_path.read_text()
                if installed == expected:
                    symbol = color("✓", Colors.GREEN)
                    status = color("installed", Colors.GREEN)
                else:
                    symbol = color("!", Colors.YELLOW)
                    status = color("installed (out of date — run install --force)", Colors.YELLOW)
                    drift_warnings.append(svc.file)
            else:
                symbol = color("✓", Colors.GREEN)
                status = color("installed", Colors.GREEN)
        else:
            symbol = color("○", Colors.RED)
            status = color("not installed", Colors.RED)

        print(f"  {symbol} {svc.file}: {status}")

    if drift_warnings:
        print()
        print(
            color(
                f"  ⚠ {len(drift_warnings)} quadlet(s) differ from templates. "
                "Run './visp-podman.py install --force && ./visp-podman.py reload' to update.",
                Colors.YELLOW,
            )
        )

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


def _tail_container_logs(service: str, lines: int = 50, follow: bool = False, stop_event: threading.Event = None):
    """Tail container-internal log files via podman exec.

    For services that write to log files inside the container (not stdout),
    this reads those files so they appear alongside journalctl output.
    """
    log_files = CONTAINER_LOG_FILES.get(service)
    if not log_files:
        return

    container = service

    # Check if container is running
    rc, _, _ = run_quiet(["podman", "inspect", "--format", "{{.State.Status}}", container])
    if rc != 0:
        print(color(f"  Container {container} not running — skipping app logs", Colors.YELLOW))
        return

    if follow:
        # Follow mode: spawn tail -f processes and stream output with prefixes
        processes = []
        for label, path in log_files:
            cmd = ["podman", "exec", container, "tail", "-n", "0", "-f", path]
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                processes.append((label, proc))
            except OSError:
                pass

        def _stream_output(lbl, proc):
            try:
                for line in proc.stdout:
                    if stop_event and stop_event.is_set():
                        break
                    print(f"{color(f'[{lbl}]', Colors.MAGENTA)} {line}", end="")
            except (OSError, ValueError):
                pass

        threads = []
        for label, proc in processes:
            t = threading.Thread(target=_stream_output, args=(label, proc), daemon=True)
            t.start()
            threads.append(t)

        # Wait for stop signal (KeyboardInterrupt handled by caller)
        try:
            if stop_event:
                stop_event.wait()
        except KeyboardInterrupt:
            pass
        finally:
            for _, proc in processes:
                proc.terminate()
            for _, proc in processes:
                proc.wait()
    else:
        # Snapshot mode: show last N lines from each log file
        for label, path in log_files:
            cmd = ["podman", "exec", container, "tail", "-n", str(lines), path]
            rc, stdout, stderr = run_quiet(cmd)
            if rc == 0 and stdout.strip():
                print(color(f"\n--- {label} ({path}) ---", Colors.MAGENTA))
                print(stdout)
            elif rc != 0 and "No such file" not in stderr:
                # File doesn't exist yet — that's fine, skip silently
                pass


def cmd_logs(args):
    """View logs from services."""
    extra_args = []
    journal_only = args.journal_only

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
        # All services — journal only (too noisy to mix all container logs)
        units = []
        for svc in CONTAINER_SERVICES:
            units.extend(["-u", f"{svc.name}.service"])
        print(color("=== Viewing logs for all VISP services ===", Colors.CYAN))
        journalctl(*units, "--no-pager", *extra_args)
    else:
        service = args.service
        has_app_logs = service in CONTAINER_LOG_FILES

        if args.follow:
            # Follow mode: run journalctl and container log tailers concurrently
            print(color(f"=== Following logs for {service} ===", Colors.CYAN))
            if has_app_logs and not journal_only:
                print(color("  (including container app logs — use --journal-only to hide)", Colors.CYAN))

            stop_event = threading.Event()

            # Start container log tailers in background threads
            if has_app_logs and not journal_only:
                log_thread = threading.Thread(
                    target=_tail_container_logs,
                    args=(service,),
                    kwargs={"follow": True, "stop_event": stop_event},
                    daemon=True,
                )
                log_thread.start()

            # Run journalctl in foreground (blocks until Ctrl+C)
            try:
                journalctl("-u", f"{service}.service", *extra_args)
            except KeyboardInterrupt:
                pass
            finally:
                stop_event.set()
        else:
            # Snapshot mode: show journal, then container logs
            print(color(f"=== Viewing logs for {service} ===", Colors.CYAN))
            journalctl("-u", f"{service}.service", "--no-pager", *extra_args)

            if has_app_logs and not journal_only:
                lines = args.lines if args.lines else 30
                print(color(f"\n=== Container app logs (last {lines} lines each) ===", Colors.CYAN))
                _tail_container_logs(service, lines=lines)


# === Service Control Commands ===


def cmd_start(args):
    """Start service(s)."""
    sm = ServiceManager(Runner(), SERVICES)
    sm.start(args.service)


def cmd_stop(args):
    """Stop service(s)."""
    sm = ServiceManager(Runner(), SERVICES)
    sm.stop(args.service)


def cmd_restart(args):
    """Restart service(s) or entire cluster."""
    sm = ServiceManager(Runner(), SERVICES)

    if args.service == "all":
        print(color("=== Restarting entire VISP cluster ===", Colors.CYAN))
        print()
        print(color("Stopping services...", Colors.YELLOW))
        sm.stop("all")
        print()
        print(color("Starting services...", Colors.GREEN))
        sm.start("all")
    else:
        # For individual services, stop then start the specific names
        sm.stop(args.service)
        sm.start(args.service)


# === Network Backend Management ===


def check_netavark() -> tuple[bool, str]:
    """Check if netavark is configured and working.

    Delegates to NetworkManager.
    """
    runner = Runner()
    nm = NetworkManager(runner)
    return nm.check_netavark()


def configure_netavark() -> bool:
    """Configure Podman to use netavark backend.

    Returns:
        bool: True if successful, False otherwise
    """
    runner = Runner()
    nm = NetworkManager(runner)
    return nm.configure_netavark()


def prompt_netavark_migration() -> bool:
    """Prompt user for netavark migration.

    Returns:
        bool: True if user wants to migrate, False otherwise
    """
    print()
    print(color("=" * 70, Colors.YELLOW))
    print(color("NETAVARK MIGRATION REQUIRED", Colors.YELLOW))
    print(color("=" * 70, Colors.YELLOW))
    print()
    print("VISP requires the netavark network backend for proper DNS resolution.")
    print("Your system is currently using CNI, which has critical DNS issues.")
    print()
    print(color("What will happen:", Colors.CYAN))
    print("  1. Configure netavark in ~/.config/containers/containers.conf")
    print("  2. Run 'podman system reset' (removes all containers)")
    print("  3. Images are preserved (no need to rebuild)")
    print("  4. Networks will be recreated automatically")
    print()
    print(color("⚠️  WARNING: All running containers will be removed!", Colors.RED))
    print("  Make sure you have backups of important data.")
    print()

    while True:
        response = input("Proceed with migration? (yes/no): ").strip().lower()
        if response in ["yes", "y"]:
            return True
        elif response in ["no", "n"]:
            print()
            print("Migration cancelled. VISP may not work correctly with CNI.")
            print("You can migrate later by running: ./visp-podman.py install")
            return False
        else:
            print("Please answer 'yes' or 'no'")


def migrate_to_netavark() -> bool:
    """Perform netavark migration.

    Delegates to NetworkManager.
    """
    runner = Runner()
    nm = NetworkManager(runner)
    return nm.migrate_to_netavark()


def ensure_networks_exist() -> bool:
    """Ensure all required Podman networks exist.

    Delegates to NetworkManager.
    """
    runner = Runner()
    nm = NetworkManager(runner)
    return nm.ensure_networks_exist()


# === Installation Commands ===


def cmd_install(args):
    """Link quadlet files to systemd directory."""

    # First-time setup: generate passwords if needed
    env_file_path = Path(".env")
    secrets_file_path = Path(".env.secrets")

    if not env_file_path.exists() or not secrets_file_path.exists():
        print(color("\n=== First-Time Setup: Generating Environment Files ===", Colors.CYAN))
        print()
        print("This will create .env and .env.secrets with auto-generated passwords.")
        print("You can modify these files later if needed.")
        print()

        from vispctl.passwords import setup_env_file

        try:
            setup_env_file(auto_passwords=True, interactive=False)
            print()
        except Exception as e:
            print(color(f"❌ Error setting up environment files: {e}", Colors.RED))
            print("Please check the error and try again.")
            sys.exit(1)

    # Check for netavark backend using NetworkManager
    runner = Runner()
    nm = NetworkManager(runner)

    is_netavark, current_backend = nm.check_netavark()

    if not is_netavark:
        print()
        print(color(f"Current network backend: {current_backend}", Colors.YELLOW))
        print()

        if current_backend == "cni":
            # Offer to migrate from CNI
            if nm.prompt_netavark_migration():
                if not nm.migrate_to_netavark():
                    print(
                        color(
                            "Migration failed. Please fix the errors and try again.",
                            Colors.RED,
                        )
                    )
                    sys.exit(1)
                print()
                print(color("✓ Migration complete!", Colors.GREEN))
                print()
            else:
                sys.exit(1)
        else:
            # Unknown backend, just configure netavark
            print(color("Netavark is required for proper DNS resolution.", Colors.YELLOW))
            response = input("Configure netavark now? (yes/no): ").strip().lower()
            if response in ["yes", "y"]:
                if not nm.configure_netavark():
                    sys.exit(1)
                print()
                print(
                    color(
                        "✓ Netavark configured. Please restart Podman services.",
                        Colors.GREEN,
                    )
                )
                print("  Run: podman system reset")
                print()
            else:
                print("Installation cancelled.")
                sys.exit(1)

    # Ensure networks exist (netavark doesn't auto-create from quadlet files)
    print()
    if not ensure_networks_exist():
        print(color("Failed to create networks. Please check the errors above.", Colors.RED))
        sys.exit(1)
    print()

    SYSTEMD_QUADLETS_DIR.mkdir(parents=True, exist_ok=True)

    # Get mode from args or current setting
    mode = getattr(args, "mode", None) or get_current_mode()
    quadlets_dir = get_quadlets_dir(mode)

    print(color(f"Installing quadlets for {mode} mode", Colors.CYAN))
    print(f"  Source: {quadlets_dir}")
    print(f"  Target: {SYSTEMD_QUADLETS_DIR}")
    print()

    # Load environment variables and create Podman secrets via SecretManager
    from vispctl.secrets import SecretManager

    sm = SecretManager(RUNNER)
    env_vars = sm.load_all()

    # Create Podman secrets from environment variables
    print(color("Creating Podman secrets...", Colors.CYAN))
    secrets = sm.get_derived(env_vars)
    sm.create_secrets(secrets)
    print()

    # Ensure all mount/cert directories referenced by quadlets exist.
    # Parsed dynamically from Volume= lines so the list never goes stale.
    print(color("Creating mount directories...", Colors.CYAN))
    created = 0
    for quadlet_file in sorted(quadlets_dir.glob("*.container")):
        for line in quadlet_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or not line.startswith("Volume=@@PROJECT_DIR@@/"):
                continue
            # Extract source path (before the first ":")
            rel_path = line.split("=", 1)[1].split(":")[0].replace("@@PROJECT_DIR@@/", "")
            # Skip external/ — those are managed by visp-deploy.py
            if rel_path.startswith("external/"):
                continue
            target = PROJECT_DIR / rel_path
            if target.exists():
                continue
            # If the leaf name has a dot, it's likely a file — ensure its parent exists
            if "." in Path(rel_path).name:
                if not target.parent.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    created += 1
            else:
                target.mkdir(parents=True, exist_ok=True)
                created += 1
    if created:
        print(f"  Created {created} missing mount directories")
    else:
        print("  All mount directories already exist")
    print()

    # Ensure the WhisperVault Unix socket directory exists.
    # Both whisperx and session-manager mount mounts/whisper/api; Podman
    # refuses to start if the host path does not exist.
    whisper_sock_dir = PROJECT_DIR / "mounts" / "whisper" / "api"
    if not whisper_sock_dir.exists():
        whisper_sock_dir.mkdir(parents=True, exist_ok=True)
        print(color("  ✓ Created mounts/whisper/api/ (WhisperVault socket directory)", Colors.GREEN))
    print()

    # Ensure container-writable directories have correct permissions.
    # In rootless Podman the host user maps to UID 0 inside the container,
    # so directories owned by the host user are root:root (755) in the
    # container — Apache's www-data (UID 33) cannot write to them.
    # We fix this by making specific directories world-writable (777).
    print(color("Fixing container-writable directory permissions...", Colors.CYAN))
    writable_dirs = [
        PROJECT_DIR / "mounts/apache/apache/uploads",
        PROJECT_DIR / "mounts/repositories",
        PROJECT_DIR / "mounts/api-logs/logs",
        PROJECT_DIR / "mounts/apache/apache/logs/apache2",
        PROJECT_DIR / "mounts/apache/apache/logs/shibboleth",
        PROJECT_DIR / "mounts/session-manager/logs",
    ]
    perm_fixed = 0
    for d in writable_dirs:
        if not d.exists():
            continue
        current_mode = d.stat().st_mode & 0o777
        if current_mode != 0o777:
            try:
                d.chmod(0o777)
            except PermissionError:
                # Directory is owned by a subuid from rootless Podman — use podman unshare
                result = subprocess.run(
                    ["podman", "unshare", "chmod", "777", str(d)],
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    print(color(f"  ✗ Failed to fix permissions on {d}: {result.stderr.strip()}", Colors.RED))
                    continue
            perm_fixed += 1
    if perm_fixed:
        print(f"  Fixed permissions on {perm_fixed} directories (set to 777)")
    else:
        print("  All container-writable directories already have correct permissions")
    print()

    # Generate matomo-tracker.js from template if BASE_DOMAIN is set
    tracker_template = PROJECT_DIR / "mounts/apache/apache/matomo-tracker.js.template"
    tracker_output = PROJECT_DIR / "mounts/apache/apache/matomo-tracker.js"
    if tracker_template.exists() and env_vars.get("BASE_DOMAIN"):
        content = tracker_template.read_text()
        content = content.replace("{{BASE_DOMAIN}}", env_vars["BASE_DOMAIN"])
        tracker_output.write_text(content)
        print(color(f"  ✓ Generated matomo-tracker.js for {env_vars['BASE_DOMAIN']}", Colors.GREEN))
    elif not tracker_output.exists():
        # Create a placeholder so the bind-mount doesn't fail
        tracker_output.write_text("// Matomo tracker not configured — set BASE_DOMAIN and re-run install\n")
        print(color("  ⚠ Created placeholder matomo-tracker.js (BASE_DOMAIN not set)", Colors.YELLOW))
    print()

    # Determine which services are available in this mode
    services = _resolve_services(args.service)

    # Filter to only services that exist in the mode's quadlet directory
    available_services = [s for s in services if (quadlets_dir / s.file).exists()]

    if not available_services:
        print(color(f"No quadlet files found in {quadlets_dir}", Colors.RED))
        return

    for svc in available_services:
        source = quadlets_dir / svc.file
        target = SYSTEMD_QUADLETS_DIR / svc.file

        if not source.exists():
            print(color(f"  ✗ {svc.file}: source not found", Colors.RED))
            continue

        # Remove existing target if --force is set
        if target.exists() or target.is_symlink():
            if not args.force:
                print(f"  ○ {svc.file}: already installed")
                continue
            target.unlink()

        # Render template: replace placeholders with actual system values
        try:
            content = source.read_text()
            rendered = render_quadlet_template(content)
            target.write_text(rendered)
            print(color(f"  ✓ {svc.file}: installed", Colors.GREEN))
        except Exception as e:
            print(color(f"  ✗ {svc.file}: {e}", Colors.RED))
            continue

    # Save the mode
    set_current_mode(mode)

    print()
    print(f"Mode set to: {color(mode, Colors.MAGENTA)}")
    print("Run './visp-podman.py reload' to apply changes.")


def cmd_uninstall(args):
    """Remove quadlet links from systemd directory."""
    services = _resolve_services(args.service)

    # Stop services first
    if not args.keep_running:
        print(color("Stopping services...", Colors.YELLOW))
        sm = ServiceManager(Runner(), SERVICES)
        sm.stop("all")

    print()
    print(color("Removing links...", Colors.CYAN))

    for svc in services:
        target = SYSTEMD_QUADLETS_DIR / svc.file

        if target.is_symlink() or target.exists():
            target.unlink()
            print(color(f"  ✓ {svc.file}: removed", Colors.GREEN))
        else:
            print(f"  ○ {svc.file}: not installed")

    print()

    # Remove Podman secrets using SecretManager
    print(color("Removing Podman secrets...", Colors.CYAN))
    from vispctl.secrets import SecretManager

    sm = SecretManager(RUNNER)
    visp_secrets = sm.list_secrets()
    if visp_secrets:
        sm.remove_secrets(visp_secrets)
    else:
        print("  No VISP secrets found")
    print()
    print()
    print("Run './visp-podman.py reload' to apply changes.")


def cmd_reload(args):
    """Reload systemd daemon to pick up quadlet changes."""
    print("Reloading systemd daemon...")
    result = systemctl("daemon-reload")
    if result.returncode == 0:
        print(color("Done. Quadlet changes are now active.", Colors.GREEN))
    else:
        print(color(f"Failed: {result.stderr}", Colors.RED))


def cmd_mode(args):
    """Show or set deployment mode."""
    new_mode = getattr(args, "new_mode", None)

    if new_mode:
        # Set mode
        old_mode = get_current_mode()
        set_current_mode(new_mode)
        print(f"Mode changed from {color(old_mode, Colors.YELLOW)} to {color(new_mode, Colors.GREEN)}")
        print()
        print(color("To apply the new mode:", Colors.CYAN))
        print(f"  1. ./visp-podman.py install --mode {new_mode} --force")
        print("  2. ./visp-podman.py reload")
        print("  3. ./visp-podman.py restart all")
    else:
        # Show current mode
        current = get_current_mode()
        print(color("=== Deployment Mode ===", Colors.CYAN))
        print()
        print(f"  Current mode: {color(current, Colors.GREEN if current == 'prod' else Colors.YELLOW)}")
        print()
        print(color("Mode differences:", Colors.CYAN))
        print("  dev      - Traefik proxy, source code mounts, container-agent mounted")
        print("  prod     - No Traefik, code baked into images, optimized for deployment")
        print()
        print("  Change mode: ./visp-podman.py mode [dev|prod]")


# === Container Commands ===


def cmd_exec(args):
    """Execute command in container."""
    container = args.container
    run(["podman", "exec", "-it", container, *args.exec_command], check=False)


def cmd_shell(args):
    """Open shell in container."""
    container = args.container
    shell = args.shell or "/bin/bash"
    run(["podman", "exec", "-it", container, shell], check=False)


def cmd_cleanup_containers(args):
    """Stop and remove session containers (legacy and current prefix)."""
    try:
        result = cleanup_containers(mode=args.mode or "stopped", yes=args.yes)
        status = result.get("status", "error")
        message = result.get("message", "")
        removed = result.get("removed", 0)

        if status == "ok":
            print(color(f"Cleanup complete: removed {removed} session container(s)", Colors.GREEN))
        elif status == "cancelled":
            print(color("Cleanup cancelled by user.", Colors.YELLOW))
        else:
            print(color(f"Cleanup finished with status={status}: {message}", Colors.RED))
    except Exception as e:
        print(color(f"Error during cleanup-containers: {e}", Colors.RED))


# Build configurations - maps service name to build info
# Format: {"context": path, "dockerfile": path (optional), "image": image_name, "target": target (optional)}
BUILD_CONFIGS = {
    "apache": {
        "context": ".",
        "dockerfile": "./docker/apache/Dockerfile",
        "image": "visp-apache",
        "target": "production",
        "build_args": {"WEBCLIENT_BUILD": "visp-build"},
        "source_repo": "./external/webclient",  # git.commit label tracks webclient source
    },
    "session-manager": {
        "context": "./external/session-manager",
        "dockerfile": "Dockerfile",
        "image": "visp-session-manager",
    },
    "emu-webapp": {
        "context": "./external/EMU-webApp",
        "dockerfile": "../../docker/emu-webapp/Dockerfile",
        "image": "visp-emu-webapp",
        "target": "production",
    },
    "emu-webapp-server": {
        "context": "./external/emu-webapp-server",
        "dockerfile": "docker/Dockerfile",
        "image": "visp-emu-webapp-server",
    },
    "octra": {
        "context": "./docker/octra",
        "dockerfile": "Dockerfile",
        "image": "visp-octra",
    },
    "wsrng-server": {
        "context": "./external/wsrng-server",
        "dockerfile": "Dockerfile",
        "image": "visp-wsrng-server",
    },
    "whisperx": {
        "context": "./external/WhisperVault",
        "dockerfile": "container/Containerfile",
        "image": "visp-whisperx",
        "description": "WhisperX transcription server (network-isolated, communicates via Unix socket)",
    },
    # Session images - used by session-manager to spawn user sessions
    "operations-session": {
        "context": "./docker/session-manager",
        "dockerfile": "operations-session/Dockerfile",
        "image": "visp-operations-session",
        "description": "Operations session image (base for other sessions)",
        "prepare_context": "container-agent",  # Needs container-agent copied to build context
        "source_repo": "./external/container-agent",  # git.commit label tracks container-agent source
    },
    "rstudio-session": {
        "context": "./docker/session-manager",
        "dockerfile": "rstudio-session/Dockerfile",
        "image": "visp-rstudio-session",
        "description": "RStudio session image",
        "depends_on": "operations-session",
    },
    "jupyter-session": {
        "context": "./docker/session-manager",
        "dockerfile": "jupyter-session/Dockerfile",
        "image": "visp-jupyter-session",
        "description": "Jupyter session image",
        "depends_on": "operations-session",
    },
}

# Special builds - Node.js tools built via container (no host npm needed)
NODE_BUILD_CONFIGS = {
    "container-agent": {
        "source": "./external/container-agent",
        "output": "./external/container-agent/dist",
        "description": "Container management agent (webpack build)",
        "build_cmd": "npm run build",
        "verify_file": "main.js",
    },
    "webclient": {
        "source": "./external/webclient",
        "output": "./external/webclient/dist",
        "description": "Angular webclient (ng build)",
        # Run ng build directly - skipping php:vendor (composer) since the Apache
        # Dockerfile installs PHP dependencies itself during image build.
        # Use npx to invoke the locally installed ng binary.
        "build_cmd": "npx ng build --configuration={config} --output-path dist",
        "default_config": "visp.dev",
        "verify_file": "index.php",
        # Angular needs more memory and uses node:20 (not alpine) for better compatibility
        "container_image": "node:20",
    },
}

# Services that can be built (have Dockerfiles)
BUILDABLE_SERVICES = list(BUILD_CONFIGS.keys())

# All buildable targets including node builds
ALL_BUILDABLE = BUILDABLE_SERVICES + list(NODE_BUILD_CONFIGS.keys())


def prepare_build_context(name: str, config: dict) -> bool:
    """
    Prepare the build context for images that need extra files copied in.

    Delegates to BuildManager.
    """
    bm = BuildManager(Runner(), build_configs=BUILD_CONFIGS, node_configs=NODE_BUILD_CONFIGS)
    return bm.prepare_build_context(name, config)


def build_node_project(name: str, config: dict, no_cache: bool = False, build_config: str = None) -> bool:
    """
    Build a Node.js project using a containerized build (no host npm needed).

    Delegates to BuildManager.
    """
    bm = BuildManager(Runner(), build_configs=BUILD_CONFIGS, node_configs=NODE_BUILD_CONFIGS)
    return bm.build_node_project(name, config, no_cache=no_cache, build_config=build_config)


def cmd_build(args):
    """Build container images.

    Delegates image and node builds to BuildManager.
    Checks version drift before building.
    """
    # Handle --list flag
    if getattr(args, "list", False):
        cmd_build_list(args)
        return

    no_cache = getattr(args, "no_cache", False)
    pull = getattr(args, "pull", False)
    service = getattr(args, "service", "all")
    build_config = getattr(args, "config", None)
    force = getattr(args, "force", False)

    # Check version drift before building (unless --force)
    if not force:
        from pathlib import Path

        from vispctl.git_repo import GitRepository
        from vispctl.versions import ComponentConfig

        config = ComponentConfig()
        mode = get_current_mode()

        # Determine which services need version checks
        services_to_check = []
        if service == "all":
            # Check all node builds that correspond to external repos
            services_to_check = [s for s in NODE_BUILD_CONFIGS.keys() if s in dict(config.get_components())]
        elif service in NODE_BUILD_CONFIGS and service in dict(config.get_components()):
            services_to_check = [service]

        # Check for version drift
        version_warnings = []
        for svc_name in services_to_check:
            comp_data = config.get_component(svc_name)
            if not comp_data:
                continue

            version = comp_data.get("version", "latest")
            is_locked = config.is_locked(svc_name)

            repo_path = Path.cwd() / "external" / svc_name
            if not repo_path.exists():
                version_warnings.append(f"  ⚠️  {svc_name}: Repository not found at {repo_path}")
                continue

            repo = GitRepository(str(repo_path))
            if not repo.is_git_repo():
                continue

            current_commit = repo.get_current_commit()
            if not current_commit:
                continue

            # In prod mode (locked), check if current commit matches locked version
            if mode == "prod" and is_locked:
                if current_commit != version:
                    version_warnings.append(
                        f"  ⚠️  {svc_name}: Version mismatch in PROD mode\n"
                        f"      Current: {current_commit[:8]}, Expected: {version[:8]}\n"
                        f"      Run: ./visp-podman.py deploy update"
                    )
            # In dev mode (unlocked), just warn if there's drift from locked version
            elif mode == "dev" and not is_locked:
                locked_version = config.get_locked_version(svc_name)
                if locked_version and locked_version != "N/A" and current_commit != locked_version:
                    version_warnings.append(
                        f"  ℹ️  {svc_name}: Differs from locked version (this is OK in dev mode)\n"
                        f"      Current: {current_commit[:8]}, Locked: {locked_version[:8]}"
                    )

        if version_warnings:
            print(color("\n=== Version Check Warnings ===", Colors.YELLOW))
            for warning in version_warnings:
                print(warning)
            print()
            if mode == "prod" and any("Version mismatch" in w for w in version_warnings):
                print(color("❌ Cannot build in PROD mode with version mismatches.", Colors.RED))
                print("   Options:")
                print("   1. Run: ./visp-podman.py deploy update")
                print("   2. Use --force to override (not recommended)")
                print()
                return
            elif version_warnings:
                print(color("Continuing build (use --force to skip this check)...", Colors.YELLOW))
                print()

    bm = BuildManager(Runner(), build_configs=BUILD_CONFIGS, node_configs=NODE_BUILD_CONFIGS)

    # Node build target
    if service in NODE_BUILD_CONFIGS:
        config = NODE_BUILD_CONFIGS[service]
        success = bm.build_node_project(service, config, no_cache, build_config)
        if success:
            print(color(f"\n✓ {service} build complete", Colors.GREEN))
            # Automatically fix permissions on output directory after successful build
            output_path = Path(config.get("output"))
            if output_path.exists():
                print(color(f"Fixing permissions on {output_path}...", Colors.YELLOW))
                from vispctl.permissions import PermissionsManager

                pm = PermissionsManager(Runner())
                pm.apply_fix([output_path], recursive=True, host_owner=True)
                print(color("✓ Permissions fixed", Colors.GREEN))
        else:
            print(color(f"\n✗ {service} build failed", Colors.RED))
        return

    # Determine which services to build
    if service == "all":
        services_to_build = BUILDABLE_SERVICES
        node_builds_to_do = list(NODE_BUILD_CONFIGS.keys())
    elif service in BUILD_CONFIGS:
        services_to_build = [service]
        node_builds_to_do = []
    else:
        print(color(f"Error: Unknown service '{service}'", Colors.RED))
        print(f"Buildable services: {', '.join(ALL_BUILDABLE)}")
        return

    print(color("=== Building VISP Container Images ===", Colors.CYAN))
    print()

    if no_cache:
        print(color("Building with --no-cache (clean rebuild)", Colors.YELLOW))
    if pull:
        print(color("Building with --pull (fetch latest base images)", Colors.YELLOW))
    print()

    results = {"success": [], "failed": [], "skipped": []}

    for svc_name in services_to_build:
        config = BUILD_CONFIGS[svc_name]
        description = config.get("description", "")
        target = config.get("target")

        print(color(f"Building {svc_name}...", Colors.BLUE))
        print(f"  Image: {config['image']}:latest")
        print(f"  Context: {config['context']}")
        if description:
            print(f"  Description: {description}")
        if target:
            print(f"  Target: {target}")

        depends_on = config.get("depends_on")
        if depends_on and depends_on not in results["success"]:
            rc, _, _ = run_quiet(
                [
                    "podman",
                    "image",
                    "exists",
                    f"{BUILD_CONFIGS[depends_on]['image']}:latest",
                ]
            )
            if rc != 0 and service != "all":
                print(color(f"  ✗ Requires {depends_on} to be built first", Colors.RED))
                print(f"    Run: ./visp-podman.py build {depends_on}")
                results["skipped"].append(svc_name)
                print()
                continue

        # Prepare context and build using BuildManager
        if config.get("prepare_context"):
            if not bm.prepare_build_context(svc_name, config):
                results["failed"].append(svc_name)
                print()
                continue

        ok = bm.build_image(svc_name, config, no_cache=no_cache, pull=pull)
        if ok:
            results["success"].append(svc_name)
        else:
            results["failed"].append(svc_name)

        print()

    # Build Node.js projects if requested
    if node_builds_to_do:
        print()
        print(color("=== Building Node.js Projects (containerized) ===", Colors.CYAN))
        print()
        for node_name in node_builds_to_do:
            config = NODE_BUILD_CONFIGS[node_name]
            success = bm.build_node_project(node_name, config, no_cache, build_config)
            if success:
                results["success"].append(node_name)
                # Automatically fix permissions on output directory after successful build
                output_path = Path(config.get("output"))
                if output_path.exists():
                    print(color(f"  Fixing permissions on {output_path}...", Colors.YELLOW))
                    from vispctl.permissions import PermissionsManager

                    pm = PermissionsManager(Runner())
                    pm.apply_fix([output_path], recursive=True, host_owner=True)
                    print(color("  ✓ Permissions fixed", Colors.GREEN))
            else:
                results["failed"].append(node_name)
            print()

    # Summary
    print(color("=== Build Summary ===", Colors.CYAN))
    if results["success"]:
        print(color(f"  Successful: {', '.join(results['success'])}", Colors.GREEN))
    if results["skipped"]:
        print(
            color(
                f"  Skipped (missing deps): {', '.join(results['skipped'])}",
                Colors.YELLOW,
            )
        )
    if results["failed"]:
        print(color(f"  Failed: {', '.join(results['failed'])}", Colors.RED))

    if results["failed"]:
        print()
        print(
            color(
                "Tip: Use --no-cache to force a clean rebuild if you're having issues",
                Colors.YELLOW,
            )
        )


def cmd_build_list(args):
    """List buildable services."""
    print(color("=== Buildable Container Images ===", Colors.CYAN))
    print()
    for name, config in BUILD_CONFIGS.items():
        print(f"  {color(name, Colors.BLUE)}")
        print(f"    Image: {config['image']}:latest")
        print(f"    Context: {config['context']}")
        if config.get("description"):
            print(f"    Description: {config['description']}")
        if config.get("target"):
            print(f"    Target: {config['target']}")
        if config.get("depends_on"):
            print(f"    Depends on: {config['depends_on']}")
        if config.get("prepare_context"):
            print(f"    Requires: {config['prepare_context']} to be built first")
        print()

    print(color("=== Buildable Node.js Projects (containerized) ===", Colors.CYAN))
    print()
    for name, config in NODE_BUILD_CONFIGS.items():
        print(f"  {color(name, Colors.BLUE)}")
        print(f"    Source: {config['source']}")
        print(f"    Output: {config['output']}")
        print(f"    Description: {config['description']}")
        if config.get("default_config"):
            print(f"    Default config: {config['default_config']}")
            print("    Available configs: visp, visp-demo, visp-pdf-server, datalab, visp-local")
        print()


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
    container = service
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

    # Check quadlet file
    print(color("Quadlet File:", Colors.YELLOW))
    svc_info = next((s for s in SERVICES if s.name == service), None)
    if svc_info:
        link_path = SYSTEMD_QUADLETS_DIR / svc_info.file
        if link_path.is_symlink():
            print(color(f"  {link_path} -> {link_path.resolve()} (legacy symlink)", Colors.YELLOW))
        elif link_path.exists():
            print(f"  {link_path} (rendered template)")
        else:
            print(color(f"  {link_path} does not exist", Colors.RED))


# === Network Info ===


def cmd_network(args):
    """Show network information and DNS status, or perform actions like 'ensure'."""
    runner = Runner()
    nm = NetworkManager(runner)

    if getattr(args, "action", None) == "ensure":
        print(color("Ensuring required Podman networks exist...", Colors.CYAN))
        ok = nm.ensure_networks_exist()
        if ok:
            print(color("  ✓ Networks ensured", Colors.GREEN))
        else:
            print(color("  ✗ Failed to ensure networks", Colors.RED))
            sys.exit(1)
        return

    # Default: show current network backend status
    print(color("=== Network Backend ===", Colors.CYAN))
    is_net, backend = nm.check_netavark()
    if is_net:
        print(color(f"  Backend: {backend} (recommended)", Colors.GREEN))
    else:
        print(
            color(
                f"  Backend: {backend} (CNI - consider upgrading to netavark)",
                Colors.YELLOW,
            )
        )
    print()


def cmd_images(args):
    """List VISP container images and their status."""
    if hasattr(args, "subcommand") and args.subcommand == "base":
        return cmd_images_base(args)

    im = ImageManager(RUNNER, BUILD_CONFIGS, NETWORK_SERVICES)
    im.display_visp_images()
    im.display_network_info()


def cmd_images_base(args):
    """List all base images used in Dockerfiles."""
    im = ImageManager(RUNNER, BUILD_CONFIGS, NETWORK_SERVICES)
    im.display_base_images()


# === Deploy Commands ===


def cmd_deploy(args):
    """Dispatch deploy subcommands."""
    if args.deploy_command == "status":
        cmd_deploy_status(args)
    elif args.deploy_command == "lock":
        cmd_deploy_lock(args)
    elif args.deploy_command == "unlock":
        cmd_deploy_unlock(args)
    elif args.deploy_command == "rollback":
        cmd_deploy_rollback(args)
    elif args.deploy_command == "update":
        cmd_deploy_update(args)
    else:
        print("Unknown deploy command. Use --help for usage.")
        sys.exit(1)


def cmd_deploy_status(args):
    """Show repository status and version drift."""
    from vispctl.deploy import DeployManager

    dm = DeployManager(runner=RUNNER)
    dm.check_status(fetch=not getattr(args, "no_fetch", False))


def cmd_deploy_lock(args):
    """Lock components to current versions."""
    from vispctl.deploy import DeployManager

    dm = DeployManager()
    components = getattr(args, "components", [])
    lock_all = getattr(args, "all", False)

    success = dm.lock_components(components, lock_all=lock_all)
    if not success:
        sys.exit(1)


def cmd_deploy_unlock(args):
    """Unlock components to track latest."""
    from vispctl.deploy import DeployManager

    dm = DeployManager()
    components = getattr(args, "components", [])
    unlock_all = getattr(args, "all", False)

    success = dm.unlock_components(components, unlock_all=unlock_all)
    if not success:
        sys.exit(1)


def cmd_deploy_rollback(args):
    """Rollback components to locked versions."""
    from vispctl.deploy import DeployManager

    dm = DeployManager()
    components = getattr(args, "components", [])
    rollback_all = getattr(args, "all", False)

    success = dm.rollback_components(components, rollback_all=rollback_all)
    if not success:
        sys.exit(1)


def cmd_deploy_update(args):
    """Update external repositories."""
    from vispctl.deploy import DeployManager

    dm = DeployManager()
    force = getattr(args, "force", False)

    success = dm.update_components(force=force)
    if not success:
        sys.exit(1)


def cmd_fix_permissions(args):
    """Fix file ownership and permissions using 'podman unshare'."""
    from vispctl.permissions import PermissionsManager

    # Default target paths if none provided: all container-writable directories
    if getattr(args, "paths", None):
        paths = [Path(p) for p in args.paths]
    else:
        paths = [
            Path("mounts/repositories"),
            Path("mounts/apache/apache/uploads"),
            Path("mounts/api-logs/logs"),
            Path("mounts/apache/apache/logs/apache2"),
            Path("mounts/apache/apache/logs/shibboleth"),
            Path("mounts/session-manager/logs"),
        ]

    existing = [p for p in paths if p.exists()]
    missing = [p for p in paths if not p.exists()]
    for p in missing:
        print(color(f"! Path does not exist: {p}", Colors.YELLOW))

    if not existing:
        print(
            color(
                "No existing target paths to operate on. Use --path to specify one.",
                Colors.YELLOW,
            )
        )
        return

    host_owner_flag = getattr(args, "host_owner", False)
    pm = PermissionsManager(RUNNER)
    planned = pm.plan_fix(
        existing,
        recursive=getattr(args, "recursive", False),
        host_owner=host_owner_flag,
    )

    print(color("=== Permission Fix Plan ===", Colors.CYAN))
    for c in planned:
        print(f"  {c}")

    if not getattr(args, "apply", False):
        print()
        print(color("Dry run complete. Re-run with --apply to make changes.", Colors.YELLOW))
        return

    print()
    print(color("Applying permission fixes...", Colors.CYAN))

    ok = pm.apply_fix(
        existing,
        recursive=getattr(args, "recursive", False),
        host_owner=host_owner_flag,
    )

    if ok:
        print(color("✓ Permissions fixed", Colors.GREEN))
    else:
        print(color("✗ One or more operations failed", Colors.RED))

    # Post-check: confirm host ownership matches current user when possible
    import os

    mismatched = []
    for p in existing:
        try:
            st = p.stat()
            if st.st_uid != os.getuid():
                mismatched.append((p, st.st_uid))
        except Exception:
            mismatched.append((p, None))

    if mismatched:
        print()
        print(
            color(
                "⚠️  Ownership check: some paths are not owned by the " "current user on the host:",
                Colors.YELLOW,
            )
        )
        for p, uid in mismatched:
            if uid is None:
                print(f"  - {p}: cannot stat (permission denied)")
            else:
                print(f"  - {p}: host uid={uid} " f"(current user uid={os.getuid()})")
        print(
            color(
                "Note: this can be normal under rootless Podman userns "
                "mapping. If you passed --host-owner and host ownership "
                "does not match, your system's userns mapping doesn't map "
                "namespace 0 to your host UID. In that case you can either "
                "remove files inside the namespace (podman unshare rm -rf) "
                "or manually chown as admin outside this script "
                "(not recommended for normal operation).",
                Colors.YELLOW,
            )
        )


# === User Management Commands ===


def cmd_users(args):
    """Dispatch user management subcommands."""
    from vispctl import users as users_mod

    handler = users_mod.COMMANDS.get(args.users_command)
    if handler:
        handler(args)
    else:
        print("Unknown users command. Use --help for usage.")
        sys.exit(1)


# === Database Audit Commands ===


def cmd_doctor(args):
    """Tree-view project health overview with full consistency checks."""
    from vispctl.doctor import run_doctor

    issues = run_doctor(
        project_id=getattr(args, "project_id", None),
        show_files=not getattr(args, "no_files", False),
        show_healthy=not getattr(args, "problems_only", False),
        problems_only=getattr(args, "problems_only", False),
        json_output=getattr(args, "json", False),
        fix_cache=getattr(args, "fix_cache", False),
        fix=getattr(args, "fix", False),
    )
    if issues:
        sys.exit(1)


# === Backup/Restore Commands ===


def cmd_backup(args):
    """Backup MongoDB database to timestamped tar.gz file."""
    from vispctl.backup import BackupManager

    bm = BackupManager(RUNNER)
    out = bm.backup(output=getattr(args, "output", None), dry_run=getattr(args, "dry_run", False))
    if out is None:
        sys.exit(1)
    return


def cmd_restore(args):
    """Restore MongoDB database from backup file."""
    from vispctl.backup import BackupManager

    bm = BackupManager(RUNNER)
    ok = bm.restore(Path(args.backup_file), force=getattr(args, "force", False))
    if not ok:
        sys.exit(1)
    return


# === Helpers ===


def _resolve_services(service_arg: str) -> list[Service]:
    """Resolve service argument to list of Service objects."""
    if service_arg == "all":
        return SERVICES

    svc = next((s for s in SERVICES if s.name == service_arg), None)
    if svc:
        return [svc]

    print(color(f"Unknown service: {service_arg}", Colors.RED))
    print(f"Available: {', '.join(s.name for s in SERVICES)}")
    sys.exit(1)


def _get_service_names() -> list[str]:
    """Get list of service names for argparse choices."""
    return ["all"] + [s.name for s in SERVICES]


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
  visp-ctl deploy status       # Check git repo versions and drift
  visp-ctl deploy lock webclient  # Lock webclient to current version
  visp-ctl deploy unlock --all # Unlock all components to track latest
  visp-ctl deploy update       # Update repos to configured versions
""",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # status
    subparsers.add_parser("status", aliases=["s"], help="Show status of all services")

    # logs
    p_logs = subparsers.add_parser("logs", aliases=["l"], help="View logs from services")
    p_logs.add_argument("service", nargs="?", default="all", help="Service name or 'all'")
    p_logs.add_argument("-f", "--follow", action="store_true", help="Follow logs")
    p_logs.add_argument("-n", "--lines", type=int, help="Number of lines to show")
    p_logs.add_argument("--since", help="Show logs since TIME (e.g., '1 hour ago')")
    p_logs.add_argument("-p", "--priority", help="Filter by priority")
    p_logs.add_argument(
        "--journal-only", action="store_true", help="Show only journalctl output, skip container app logs"
    )

    # start
    p_start = subparsers.add_parser("start", help="Start service(s)")
    p_start.add_argument("service", default="all", nargs="?", help="Service name or 'all'")

    # stop
    p_stop = subparsers.add_parser("stop", help="Stop service(s)")
    p_stop.add_argument("service", default="all", nargs="?", help="Service name or 'all'")

    # restart
    p_restart = subparsers.add_parser("restart", aliases=["r"], help="Restart service(s)")
    p_restart.add_argument("service", default="all", nargs="?", help="Service name or 'all'")

    # install
    p_install = subparsers.add_parser("install", aliases=["i"], help="Link quadlet files to systemd")
    p_install.add_argument("service", default="all", nargs="?", help="Service name or 'all'")
    p_install.add_argument("-f", "--force", action="store_true", help="Overwrite existing links")
    p_install.add_argument("-m", "--mode", choices=["dev", "prod"], help="Deployment mode (dev or prod)")

    # uninstall
    p_uninstall = subparsers.add_parser("uninstall", aliases=["u"], help="Remove quadlet links")
    p_uninstall.add_argument("service", default="all", nargs="?", help="Service name or 'all'")
    p_uninstall.add_argument("--keep-running", action="store_true", help="Don't stop services first")

    # reload
    subparsers.add_parser("reload", help="Reload systemd daemon")

    # mode
    p_mode = subparsers.add_parser("mode", aliases=["m"], help="Show or set deployment mode")
    p_mode.add_argument(
        "new_mode",
        nargs="?",
        choices=["dev", "prod"],
        help="Set mode to dev or prod (omit to show current mode)",
    )

    # debug
    p_debug = subparsers.add_parser("debug", aliases=["d"], help="Debug startup issues")
    p_debug.add_argument("service", help="Service to debug")

    # exec
    p_exec = subparsers.add_parser("exec", aliases=["e"], help="Execute command in container")
    p_exec.add_argument("container", help="Container name (e.g. apache, session-manager)")
    p_exec.add_argument("exec_command", nargs="+", help="Command to run")

    # shell
    p_shell = subparsers.add_parser("shell", aliases=["sh"], help="Open shell in container")
    p_shell.add_argument("container", help="Container name (e.g. apache, session-manager)")
    p_shell.add_argument("--shell", default="/bin/bash", help="Shell to use (default: /bin/bash)")

    # cleanup-containers
    p_cleanup = subparsers.add_parser(
        "cleanup-containers",
        aliases=["cleanup"],
        help="Stop and remove session containers (legacy and current naming)",
    )
    p_cleanup.add_argument(
        "--mode",
        choices=["all", "stopped", "running"],
        default="stopped",
        help="Which containers to target (default: stopped)",
    )
    p_cleanup.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Do not prompt for confirmation",
    )

    # build
    p_build = subparsers.add_parser("build", aliases=["b"], help="Build container images")
    p_build.add_argument(
        "service",
        default="all",
        nargs="?",
        help=f"Service to build or 'all' (options: {', '.join(ALL_BUILDABLE)})",
    )
    p_build.add_argument(
        "--no-cache",
        action="store_true",
        help="Build without using cache (clean rebuild)",
    )
    p_build.add_argument(
        "--pull",
        action="store_true",
        help="Always pull the latest base images",
    )
    p_build.add_argument(
        "--list",
        action="store_true",
        help="List all buildable services",
    )
    p_build.add_argument(
        "--config",
        "-c",
        default=None,
        help="Build configuration for webclient (e.g., visp, datalab, visp-pdf-server)",
    )
    p_build.add_argument(
        "--force",
        action="store_true",
        help="Skip version checks (not recommended in production)",
    )

    # network
    p_network = subparsers.add_parser("network", aliases=["n", "net"], help="Show network info and DNS status")
    p_network.add_argument(
        "action",
        nargs="?",
        choices=["ensure"],
        help="Optional action: ensure (create required networks)",
    )

    # images
    p_images = subparsers.add_parser("images", aliases=["img"], help="List VISP container images and build status")
    p_images_sub = p_images.add_subparsers(dest="subcommand", help="Images subcommands")
    p_images_sub.add_parser("base", help="List all base images from Dockerfiles with versions")

    # deploy
    p_deploy = subparsers.add_parser("deploy", help="Manage deployments: version control, git repos, status")
    p_deploy_sub = p_deploy.add_subparsers(dest="deploy_command", help="Deploy subcommands", required=True)

    # deploy status
    p_deploy_status = p_deploy_sub.add_parser("status", help="Check repository status and version drift")
    p_deploy_status.add_argument(
        "--no-fetch", action="store_true", help="Skip fetching from remotes (use cached remote state)"
    )

    # deploy lock
    p_deploy_lock = p_deploy_sub.add_parser("lock", help="Lock components to their current versions")
    p_deploy_lock.add_argument("components", nargs="*", help="Components to lock (specify names or use --all)")
    p_deploy_lock.add_argument("--all", action="store_true", help="Lock all components")

    # deploy unlock
    p_deploy_unlock = p_deploy_sub.add_parser("unlock", help="Unlock components to track latest")
    p_deploy_unlock.add_argument("components", nargs="*", help="Components to unlock (specify names or use --all)")
    p_deploy_unlock.add_argument("--all", action="store_true", help="Unlock all components")

    # deploy rollback
    p_deploy_rollback = p_deploy_sub.add_parser("rollback", help="Rollback components to their locked versions")
    p_deploy_rollback.add_argument("components", nargs="*", help="Components to rollback (specify names or use --all)")
    p_deploy_rollback.add_argument("--all", action="store_true", help="Rollback all components")

    # deploy update
    p_deploy_update = p_deploy_sub.add_parser("update", help="Update external repositories to configured versions")
    p_deploy_update.add_argument("--force", action="store_true", help="Force update even with uncommitted changes")

    # fix-permissions
    p_fix = subparsers.add_parser(
        "fix-permissions",
        aliases=["fixperm"],
        help="Fix ownership and permissions for mount paths using podman unshare",
    )
    p_fix.add_argument(
        "--path",
        "-p",
        dest="paths",
        action="append",
        help=(
            "Path to fix (can be specified multiple times). "
            "Default: all container-writable dirs (uploads, repositories, logs)"
        ),
    )
    p_fix.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="Apply changes recursively (adds -R to chown/chmod)",
    )
    p_fix.add_argument(
        "--host-owner",
        action="store_true",
        help=(
            "Try to set host ownership to the current user using "
            "namespace mapping (uses 'podman unshare chown 0:0'; no sudo)."
        ),
    )
    p_fix.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform the changes. Default: dry-run",
    )

    # backup
    p_backup = subparsers.add_parser("backup", help="Backup MongoDB database")
    p_backup.add_argument(
        "--output",
        "-o",
        help="Output file path (default: ./visp_mongodb_VERSION_TIMESTAMP.tar.gz)",
    )
    p_backup.add_argument(
        "--dry-run",
        action="store_true",
        help="Do a dry-run (show actions without making changes)",
    )

    # restore
    p_restore = subparsers.add_parser("restore", help="Restore MongoDB database from backup")
    p_restore.add_argument("backup_file", help="Backup file to restore")
    p_restore.add_argument("--force", action="store_true", help="Skip confirmation prompt")

    # users
    p_users = subparsers.add_parser("users", help="Manage users in MongoDB")
    p_users_sub = p_users.add_subparsers(dest="users_command", help="Users subcommands", required=True)

    p_users_sub.add_parser("list", aliases=["ls"], help="List all users")

    p_u_show = p_users_sub.add_parser("show", aliases=["get"], help="Show user details")
    p_u_show.add_argument("username", help="Username to show")

    p_u_create = p_users_sub.add_parser("create", aliases=["add"], help="Create new user")
    p_u_create.add_argument("email", help="User email address")
    p_u_create.add_argument("--first-name", "-f", help="First name")
    p_u_create.add_argument("--last-name", "-l", help="Last name")
    p_u_create.add_argument("--can-create-projects", "-p", action="store_true", help="Allow user to create projects")

    p_u_activate = p_users_sub.add_parser("activate", aliases=["enable"], help="Enable user login")
    p_u_activate.add_argument("username", help="Username to activate")

    p_u_deactivate = p_users_sub.add_parser("deactivate", aliases=["disable"], help="Disable user login")
    p_u_deactivate.add_argument("username", help="Username to deactivate")

    p_u_grant = p_users_sub.add_parser("grant", help="Grant privilege to user")
    p_u_grant.add_argument("username", help="Username")
    p_u_grant.add_argument("privilege", help="Privilege (createProjects, createInviteCodes)")

    p_u_revoke = p_users_sub.add_parser("revoke", help="Revoke privilege from user")
    p_u_revoke.add_argument("username", help="Username")
    p_u_revoke.add_argument("privilege", help="Privilege to revoke")

    p_u_delete = p_users_sub.add_parser("delete", aliases=["rm"], help="Delete user")
    p_u_delete.add_argument("username", help="Username to delete")
    p_u_delete.add_argument("--force", "-F", action="store_true", help="Skip confirmation")

    # doctor (replaces old 'audit' command — 'audit' kept as alias)
    p_doctor = subparsers.add_parser(
        "doctor",
        aliases=["audit"],
        help="Project health overview: tree view + emuDB consistency checks",
    )
    p_doctor.add_argument("project_id", nargs="?", help="Check a specific project by ID (default: all)")
    p_doctor.add_argument(
        "--no-files",
        action="store_true",
        help="Hide per-session/bundle file details (compact view)",
    )
    p_doctor.add_argument(
        "--problems",
        action="store_true",
        dest="problems_only",
        help="Only show projects with issues",
    )
    p_doctor.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON (for scripting)",
    )
    p_doctor.add_argument(
        "--fix-cache",
        action="store_true",
        dest="fix_cache",
        help="Delete stale VISP_emuDBcache.sqlite files where found",
    )
    p_doctor.add_argument(
        "--fix",
        action="store_true",
        help="Fix issues: prune stale bundle list entries, move orphan bundles to _lost+found/",
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
        "mode": cmd_mode,
        "m": cmd_mode,
        "debug": cmd_debug,
        "d": cmd_debug,
        "exec": cmd_exec,
        "e": cmd_exec,
        "shell": cmd_shell,
        "sh": cmd_shell,
        "cleanup-containers": cmd_cleanup_containers,
        "cleanup": cmd_cleanup_containers,
        "build": cmd_build,
        "b": cmd_build,
        "network": cmd_network,
        "n": cmd_network,
        "net": cmd_network,
        "images": cmd_images,
        "img": cmd_images,
        "deploy": cmd_deploy,
        "fix-permissions": cmd_fix_permissions,
        "fixperm": cmd_fix_permissions,
        "backup": cmd_backup,
        "restore": cmd_restore,
        "users": cmd_users,
        "doctor": cmd_doctor,
        "audit": cmd_doctor,
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
