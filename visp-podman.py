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
  ./visp-podman.py build webclient --config visp # Build webclient with visp config
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
import subprocess
import sys
from pathlib import Path

# Configuration
QUADLETS_BASE_DIR = Path(__file__).parent / "quadlets"
SYSTEMD_QUADLETS_DIR = Path.home() / ".config/containers/systemd"
CONTAINERS_CONF = Path.home() / ".config/containers/containers.conf"
MODE_FILE = Path(__file__).parent / ".visp-mode"

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
    {"name": "emu-webapp", "type": "container", "file": "emu-webapp.container"},
    {
        "name": "emu-webapp-server",
        "type": "container",
        "file": "emu-webapp-server.container",
    },
    {"name": "octra", "type": "container", "file": "octra.container"},
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


def load_all_env_vars() -> dict:
    """Load environment variables from both .env and .env.secrets files.
    Returns merged dictionary with secrets taking precedence."""
    env_vars = {}

    # Load non-sensitive config from .env
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        env_vars.update(load_env_vars(env_file))

    # Load sensitive credentials from .env.secrets (overrides .env)
    secrets_file = Path(__file__).parent / ".env.secrets"
    if secrets_file.exists():
        env_vars.update(load_env_vars(secrets_file))
    elif not (Path(__file__).parent / ".env.secrets").exists():
        print(color("⚠ WARNING: .env.secrets file not found!", Colors.YELLOW))
        print(color("  Secrets will be read from .env (legacy mode)", Colors.YELLOW))
        print(color("  Consider running: cp .env .env.secrets", Colors.YELLOW))

    return env_vars


def get_derived_secrets(env_vars: dict) -> dict:
    """Build derived secrets from environment variables.
    Returns dict of secret_name -> secret_value."""
    secrets = {}

    # Direct secrets from .env
    if "MONGO_ROOT_PASSWORD" in env_vars:
        secrets["visp_mongo_root_password"] = env_vars["MONGO_ROOT_PASSWORD"]

    if "VISP_API_ACCESS_TOKEN" in env_vars:
        secrets["visp_api_access_token"] = env_vars["VISP_API_ACCESS_TOKEN"]

    if "TEST_USER_LOGIN_KEY" in env_vars:
        secrets["visp_test_user_login_key"] = env_vars["TEST_USER_LOGIN_KEY"]

    # Derived/constructed secrets
    if "MONGO_ROOT_PASSWORD" in env_vars:
        secrets["visp_mongo_uri"] = (
            f"mongodb://root:{env_vars['MONGO_ROOT_PASSWORD']}@mongo:27017"
        )

    if "BASE_DOMAIN" in env_vars:
        secrets["visp_media_file_base_url"] = (
            f"https://emu-webapp.{env_vars['BASE_DOMAIN']}"
        )

    return secrets


def create_podman_secrets(secrets: dict) -> None:
    """Create or update Podman secrets from a dictionary."""
    for name, value in secrets.items():
        # Check if secret exists
        result = subprocess.run(
            ["podman", "secret", "inspect", name], capture_output=True, text=True
        )

        if result.returncode == 0:
            # Secret exists, remove it first (podman doesn't support update)
            subprocess.run(["podman", "secret", "rm", name], capture_output=True)

        # Create the secret
        process = subprocess.run(
            ["podman", "secret", "create", name, "-"],
            input=value,
            capture_output=True,
            text=True,
        )

        if process.returncode == 0:
            print(f"  ✓ Secret '{name}': created")
        else:
            print(f"  ✗ Secret '{name}': failed - {process.stderr}")


def remove_podman_secrets(secret_names: list) -> None:
    """Remove Podman secrets by name."""
    for name in secret_names:
        result = subprocess.run(
            ["podman", "secret", "rm", name], capture_output=True, text=True
        )
        if result.returncode == 0:
            print(f"  ✓ Secret '{name}': removed")


def list_visp_secrets() -> list:
    """List all VISP-related Podman secrets."""
    result = subprocess.run(
        ["podman", "secret", "ls", "--format", "{{.Name}}"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return [
            name
            for name in result.stdout.strip().split("\n")
            if name.startswith("visp_")
        ]
    return []


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
    current_mode = get_current_mode()
    quadlets_dir = get_quadlets_dir(current_mode)
    print(f"  Mode: {color(current_mode, Colors.MAGENTA)}")
    print()

    for svc in SERVICES:
        link_path = SYSTEMD_QUADLETS_DIR / svc["file"]
        target_path = quadlets_dir / svc["file"]

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


# === Network Backend Management ===


def check_netavark() -> tuple[bool, str]:
    """Check if netavark is configured and working.

    Returns:
        tuple: (is_netavark, backend_name) - True if netavark, False if CNI or unknown
    """
    try:
        result = subprocess.run(
            ["podman", "info", "--format", "{{.Host.NetworkBackend}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            backend = result.stdout.strip()
            return (backend == "netavark", backend)
    except Exception:
        pass
    return (False, "unknown")


def configure_netavark() -> bool:
    """Configure Podman to use netavark backend.

    Returns:
        bool: True if successful, False otherwise
    """
    print(color("Configuring netavark network backend...", Colors.CYAN))

    # Check if packages are installed
    try:
        result = subprocess.run(
            ["dpkg", "-l", "podman-netavark", "aardvark-dns"],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            print(color("  ✗ Required packages not installed", Colors.RED))
            print("  Please install: sudo apt install podman-netavark aardvark-dns")
            return False
    except Exception:
        print(color("  ⚠ Could not verify package installation", Colors.YELLOW))

    # Create containers config directory
    CONTAINERS_CONF.parent.mkdir(parents=True, exist_ok=True)

    # Read existing config or create new
    config_lines = []
    network_section_exists = False

    if CONTAINERS_CONF.exists():
        with open(CONTAINERS_CONF, "r") as f:
            config_lines = f.readlines()
        # Check if [network] section exists
        for line in config_lines:
            if line.strip() == "[network]":
                network_section_exists = True
                break

    # Add or update netavark configuration
    if not network_section_exists:
        # Add new [network] section
        if config_lines and not config_lines[-1].endswith("\n"):
            config_lines.append("\n")
        config_lines.append("\n[network]\n")
        config_lines.append('network_backend = "netavark"\n')
    else:
        # Update existing section
        in_network_section = False
        backend_set = False
        new_lines = []
        for line in config_lines:
            if line.strip() == "[network]":
                in_network_section = True
                new_lines.append(line)
            elif in_network_section and line.startswith("["):
                # Entering new section
                if not backend_set:
                    new_lines.append('network_backend = "netavark"\n')
                in_network_section = False
                new_lines.append(line)
            elif in_network_section and "network_backend" in line:
                new_lines.append('network_backend = "netavark"\n')
                backend_set = True
            else:
                new_lines.append(line)

        if in_network_section and not backend_set:
            new_lines.append('network_backend = "netavark"\n')
        config_lines = new_lines

    # Write config
    try:
        with open(CONTAINERS_CONF, "w") as f:
            f.writelines(config_lines)
        print(color(f"  ✓ Updated {CONTAINERS_CONF}", Colors.GREEN))
        return True
    except Exception as e:
        print(color(f"  ✗ Failed to write config: {e}", Colors.RED))
        return False


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

    Returns:
        bool: True if successful, False otherwise
    """
    # Configure netavark
    if not configure_netavark():
        return False

    print()
    print(color("Running podman system reset...", Colors.CYAN))
    print("  This will remove all containers but preserve images.")

    try:
        result = subprocess.run(["podman", "system", "reset", "-f"], check=False)
        if result.returncode != 0:
            print(color("  ✗ podman system reset failed", Colors.RED))
            return False
        print(color("  ✓ System reset complete", Colors.GREEN))
        return True
    except Exception as e:
        print(color(f"  ✗ Error: {e}", Colors.RED))
        return False


def ensure_networks_exist() -> bool:
    """Ensure all required Podman networks exist.

    With netavark, quadlet .network files don't auto-create networks.
    We need to create them manually with the correct settings.

    Returns:
        bool: True if all networks exist or were created, False on error
    """
    # Networks that need to be created
    required_networks = [
        {"name": "systemd-visp-net", "internal": False},
        {"name": "systemd-whisper-net", "internal": True},
        {"name": "systemd-octra-net", "internal": True},
    ]

    print(color("Checking Podman networks...", Colors.CYAN))

    # Get existing networks
    try:
        result = subprocess.run(
            ["podman", "network", "ls", "--format", "{{.Name}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        existing_networks = (
            set(result.stdout.strip().split("\n")) if result.stdout.strip() else set()
        )
    except Exception as e:
        print(color(f"  ✗ Failed to list networks: {e}", Colors.RED))
        return False

    # Create missing networks
    for network in required_networks:
        if network["name"] in existing_networks:
            print(f"  ○ {network['name']}: exists")
            continue

        print(f"  Creating {network['name']}...")
        cmd = ["podman", "network", "create", "--driver", "bridge"]
        if network["internal"]:
            cmd.append("--internal")
        cmd.append(network["name"])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode == 0:
                print(color(f"  ✓ {network['name']}: created", Colors.GREEN))
            else:
                print(
                    color(f"  ✗ {network['name']}: {result.stderr.strip()}", Colors.RED)
                )
                return False
        except Exception as e:
            print(color(f"  ✗ {network['name']}: {e}", Colors.RED))
            return False

    return True


# === Installation Commands ===


def cmd_install(args):
    """Link quadlet files to systemd directory."""

    # Check for netavark backend
    is_netavark, current_backend = check_netavark()

    if not is_netavark:
        print()
        print(color(f"Current network backend: {current_backend}", Colors.YELLOW))
        print()

        if current_backend == "cni":
            # Offer to migrate from CNI
            if prompt_netavark_migration():
                if not migrate_to_netavark():
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
            print(
                color("Netavark is required for proper DNS resolution.", Colors.YELLOW)
            )
            response = input("Configure netavark now? (yes/no): ").strip().lower()
            if response in ["yes", "y"]:
                if not configure_netavark():
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
        print(
            color(
                "Failed to create networks. Please check the errors above.", Colors.RED
            )
        )
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

    # Load environment variables from both .env and .env.secrets
    env_vars = load_all_env_vars()

    # Create Podman secrets from environment variables
    print(color("Creating Podman secrets...", Colors.CYAN))
    secrets = get_derived_secrets(env_vars)
    create_podman_secrets(secrets)
    print()

    # Determine which services are available in this mode
    services = _resolve_services(args.service)

    # Filter to only services that exist in the mode's quadlet directory
    available_services = [s for s in services if (quadlets_dir / s["file"]).exists()]

    if not available_services:
        print(color(f"No quadlet files found in {quadlets_dir}", Colors.RED))
        return

    for svc in available_services:
        source = quadlets_dir / svc["file"]
        target = SYSTEMD_QUADLETS_DIR / svc["file"]

        if not source.exists():
            print(color(f"  ✗ {svc['file']}: source not found", Colors.RED))
            continue

        # Remove existing target if --force is set
        if target.exists() or target.is_symlink():
            if not args.force:
                print(f"  ○ {svc['file']}: already installed")
                continue
            target.unlink()

        # Create symlink to source quadlet
        try:
            target.symlink_to(source.resolve())
            print(color(f"  ✓ {svc['file']}: installed", Colors.GREEN))
        except Exception as e:
            print(color(f"  ✗ {svc['file']}: {e}", Colors.RED))
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

    # Remove Podman secrets
    print(color("Removing Podman secrets...", Colors.CYAN))
    visp_secrets = list_visp_secrets()
    if visp_secrets:
        remove_podman_secrets(visp_secrets)
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
        print(
            f"Mode changed from {color(old_mode, Colors.YELLOW)} to {color(new_mode, Colors.GREEN)}"
        )
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
        print(
            f"  Current mode: {color(current, Colors.GREEN if current == 'prod' else Colors.YELLOW)}"
        )
        print()
        print(color("Mode differences:", Colors.CYAN))
        print("  dev      - Traefik proxy, source code mounts, container-agent mounted")
        print(
            "  prod     - No Traefik, code baked into images, optimized for deployment"
        )
        print()
        print("  Change mode: ./visp-podman.py mode [dev|prod]")


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


# Build configurations - maps service name to build info
# Format: {"context": path, "dockerfile": path (optional), "image": image_name, "target": target (optional)}
BUILD_CONFIGS = {
    "apache": {
        "context": ".",
        "dockerfile": "./docker/apache/Dockerfile",
        "image": "visp-apache",
    },
    "session-manager": {
        "context": "./external/session-manager",
        "dockerfile": "Dockerfile",
        "image": "visp-session-manager",
    },
    "emu-webapp": {
        "context": "./docker/emu-webapp",
        "dockerfile": "Dockerfile",
        "image": "visp-emu-webapp",
        "target": "production",
    },
    "emu-webapp-server": {
        "context": "./external/emu-webapp-server/docker",
        "dockerfile": "Dockerfile",
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
    "whisper": {
        "context": "./docker/whisper",
        "dockerfile": "Dockerfile",
        "image": "visp-whisper",
    },
    # Session images - used by session-manager to spawn user sessions
    "operations-session": {
        "context": "./docker/session-manager",
        "dockerfile": "operations-session/Dockerfile",
        "image": "visp-operations-session",
        "description": "Operations session image (base for other sessions)",
        "prepare_context": "container-agent",  # Needs container-agent copied to build context
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
        "output": "./container-agent/dist",
        "description": "Container management agent (webpack build)",
        "build_cmd": "npm run build",
        "verify_file": "main.js",
    },
    "webclient": {
        "source": "./external/webclient",
        "output": "./external/webclient/dist",
        "description": "Angular webclient (ng build)",
        # Default to visp-build, can be overridden with --config
        "build_cmd": "npm run {config}-build",
        "default_config": "visp",
        "verify_file": "index.html",
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

    For session images, this copies the container-agent source into the build context.
    The Dockerfile will then build container-agent as part of the multi-stage build.

    Args:
        name: Build target name
        config: Build configuration dict

    Returns:
        True if preparation succeeded, False otherwise
    """
    import shutil

    prepare = config.get("prepare_context")
    if not prepare:
        return True  # Nothing to prepare

    context_dir = Path(__file__).parent / config["context"]

    if prepare == "container-agent":
        # Copy container-agent SOURCE to session-manager build context
        # The Dockerfile builds it as part of multi-stage build
        agent_source = (
            Path(__file__).parent / NODE_BUILD_CONFIGS["container-agent"]["source"]
        )
        agent_dest = context_dir / "container-agent"

        if not agent_source.exists():
            print(
                color(
                    f"  ✗ container-agent source not found at {agent_source}",
                    Colors.RED,
                )
            )
            return False

        # Check for package.json to ensure it's the source directory
        if not (agent_source / "package.json").exists():
            print(color("  ✗ container-agent source missing package.json", Colors.RED))
            return False

        # Ensure destination exists and is clean
        if agent_dest.exists():
            shutil.rmtree(agent_dest)

        # Copy source directory (excluding node_modules for faster copy)
        def ignore_patterns(directory, files):
            return (
                ["node_modules", ".git", "dist"]
                if any(x in files for x in ["node_modules", ".git", "dist"])
                else []
            )

        shutil.copytree(agent_source, agent_dest, ignore=ignore_patterns)

        print(color("  ✓ Copied container-agent source to build context", Colors.GREEN))
        return True

    print(color(f"  ✗ Unknown prepare_context type: {prepare}", Colors.RED))
    return False


def build_node_project(
    name: str, config: dict, no_cache: bool = False, build_config: str = None
) -> bool:
    """
    Build a Node.js project using a containerized build (no host npm needed).

    Uses podman to run node container that:
    1. Mounts source directory
    2. Runs npm install && npm run build
    3. Outputs to the configured output directory

    Args:
        name: Project name
        config: Build configuration dict
        no_cache: Whether to clean before building
        build_config: Optional build configuration (e.g., 'visp', 'datalab' for webclient)
    """
    import shutil
    import os

    source_dir = Path(__file__).parent / config["source"]
    output_dir = Path(__file__).parent / config["output"]

    # Determine build command
    build_cmd = config.get("build_cmd", "npm run build")
    if "{config}" in build_cmd:
        cfg = build_config or config.get("default_config", "production")
        build_cmd = build_cmd.format(config=cfg)

    # Container image (default to node:20-alpine for smaller size)
    container_image = config.get("container_image", "node:20-alpine")
    verify_file = config.get("verify_file", "main.js")

    print(color(f"Building {name} (containerized Node.js build)...", Colors.BLUE))
    print(f"  Source: {source_dir}")
    print(f"  Output: {output_dir}")
    print(f"  Description: {config['description']}")
    print(f"  Build command: {build_cmd}")
    if build_config:
        print(f"  Configuration: {build_config}")
    print()

    if not source_dir.exists():
        print(color(f"  ✗ Source directory not found: {source_dir}", Colors.RED))
        return False

    # Create output directory if needed
    output_dir.mkdir(parents=True, exist_ok=True)

    # If no_cache, remove existing output
    if no_cache:
        print(color("  Cleaning output directory for fresh build...", Colors.YELLOW))
        for item in output_dir.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)

    # Build command using podman
    # Strategy: Copy source to container, build there, copy output back
    # This avoids permission issues with bind mounts
    uid = os.getuid()
    gid = os.getgid()

    # For Angular/large projects, increase memory limit
    memory_limit = "--memory=4g" if "angular" in config["description"].lower() else ""

    cmd = [
        "podman",
        "run",
        "--rm",
    ]
    if memory_limit:
        cmd.append(memory_limit)
    cmd.extend(
        [
            "-v",
            f"{source_dir.resolve()}:/src:ro,Z",
            "-v",
            f"{output_dir.resolve()}:/output:Z",
            container_image,
            "sh",
            "-c",
            (
                f"cp -r /src /build && cd /build && npm install --legacy-peer-deps && "
                f"{build_cmd} && cp -r dist/* /output/ && chown -R {uid}:{gid} /output"
            ),
        ]
    )

    print(color("  Running containerized build...", Colors.CYAN))
    print(f"  Container: {container_image}")
    print(f"  Steps: npm install && {build_cmd}")
    print()

    try:
        result = subprocess.run(cmd, check=False)
        if result.returncode == 0:
            # Verify output
            verify_path = output_dir / verify_file
            if verify_path.exists():
                print(color(f"  ✓ {name} built successfully", Colors.GREEN))
                print(f"    Output: {verify_path}")
                return True
            else:
                # Check if any files were created
                files = list(output_dir.iterdir())
                if files:
                    print(color(f"  ✓ {name} built successfully", Colors.GREEN))
                    print(f"    Output directory: {output_dir}")
                    return True
                print(
                    color(
                        f"  ✗ Build completed but {verify_file} not found", Colors.RED
                    )
                )
                return False
        else:
            print(
                color(
                    f"  ✗ {name} build failed (exit code {result.returncode})",
                    Colors.RED,
                )
            )
            return False
    except Exception as e:
        print(color(f"  ✗ {name} build error: {e}", Colors.RED))
        return False


def cmd_build(args):
    """Build container images."""
    # Handle --list flag
    if getattr(args, "list", False):
        cmd_build_list(args)
        return

    no_cache = getattr(args, "no_cache", False)
    pull = getattr(args, "pull", False)
    service = getattr(args, "service", "all")
    build_config = getattr(args, "config", None)

    # Check if it's a Node.js build target
    if service in NODE_BUILD_CONFIGS:
        config = NODE_BUILD_CONFIGS[service]
        success = build_node_project(service, config, no_cache, build_config)
        if success:
            print(color(f"\n✓ {service} build complete", Colors.GREEN))
        else:
            print(color(f"\n✗ {service} build failed", Colors.RED))
        return

    # Determine which services to build
    if service == "all":
        services_to_build = BUILDABLE_SERVICES
        # Also build node projects when building "all"
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

    # Track results
    results = {"success": [], "failed": [], "skipped": []}

    for svc_name in services_to_build:
        config = BUILD_CONFIGS[svc_name]
        context = config["context"]
        dockerfile = config.get("dockerfile", "Dockerfile")
        image = config["image"]
        target = config.get("target")
        description = config.get("description", "")
        depends_on = config.get("depends_on")

        print(color(f"Building {svc_name}...", Colors.BLUE))
        print(f"  Image: {image}:latest")
        print(f"  Context: {context}")
        if description:
            print(f"  Description: {description}")
        if target:
            print(f"  Target: {target}")

        # Check dependencies
        if depends_on and depends_on not in results["success"]:
            # Check if the dependent image exists
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

        # Prepare context if needed (e.g., copy container-agent for session images)
        if config.get("prepare_context"):
            if not prepare_build_context(svc_name, config):
                results["failed"].append(svc_name)
                print()
                continue

        # Build the podman build command
        cmd = ["podman", "build"]

        if no_cache:
            cmd.append("--no-cache")
        if pull:
            cmd.append("--pull")
        if target:
            cmd.extend(["--target", target])

        cmd.extend(["-t", f"{image}:latest"])
        cmd.extend(
            [
                "-f",
                (
                    f"{context}/{dockerfile}"
                    if not dockerfile.startswith("./")
                    else dockerfile
                ),
            ]
        )
        cmd.append(context)
        print()

        try:
            result = subprocess.run(cmd, check=False)
            if result.returncode == 0:
                print(color(f"✓ {svc_name} built successfully", Colors.GREEN))
                results["success"].append(svc_name)
            else:
                print(color(f"✗ {svc_name} build failed", Colors.RED))
                results["failed"].append(svc_name)
        except Exception as e:
            print(color(f"✗ {svc_name} build error: {e}", Colors.RED))
            results["failed"].append(svc_name)

        print()

    # Build Node.js projects if requested
    if node_builds_to_do:
        print()
        print(color("=== Building Node.js Projects (containerized) ===", Colors.CYAN))
        print()
        for node_name in node_builds_to_do:
            config = NODE_BUILD_CONFIGS[node_name]
            success = build_node_project(node_name, config, no_cache, build_config)
            if success:
                results["success"].append(node_name)
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
            print(
                "    Available configs: visp, visp-demo, visp-pdf-server, datalab, visp-local"
            )
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


def cmd_images(args):
    """List VISP container images and their status."""
    print(color("=== VISP Container Images ===", Colors.CYAN))
    print()

    # Get all expected images from BUILD_CONFIGS
    expected_images = {config["image"]: name for name, config in BUILD_CONFIGS.items()}

    # Get actual images from podman
    rc, stdout, _ = run_quiet(
        [
            "podman",
            "images",
            "--format",
            "{{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.Created}}",
        ]
    )

    if rc != 0:
        print(color("Failed to list images", Colors.RED))
        return

    found_images = {}
    for line in stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 4:
            repo, tag, size, created = parts[0], parts[1], parts[2], parts[3]
            # Extract image name from full path (e.g., docker.io/library/visp-apache -> visp-apache)
            image_name = repo.split("/")[-1]
            if image_name.startswith("visp-"):
                found_images[image_name] = {
                    "tag": tag,
                    "size": size,
                    "created": created,
                    "full_repo": repo,
                }

    # Print status for each expected image
    for image_name, build_name in sorted(expected_images.items()):
        if image_name in found_images:
            info = found_images[image_name]
            print(
                f"  {color('✓', Colors.GREEN)} {color(build_name, Colors.BLUE):25} {image_name}:{info['tag']}"
            )
            print(f"      Size: {info['size']:12}  Created: {info['created']}")
        else:
            print(
                f"  {color('✗', Colors.RED)} {color(build_name, Colors.BLUE):25} {image_name} (not built)"
            )
        print()

    # Summary
    built = sum(1 for img in expected_images if img in found_images)
    total = len(expected_images)

    if built == total:
        print(color(f"All {total} images are built.", Colors.GREEN))
    else:
        print(
            color(
                f"{built}/{total} images built. Missing images can be built with:",
                Colors.YELLOW,
            )
        )
        print("  ./visp-podman.py build all")

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


# === Backup/Restore Commands ===


def cmd_backup(args):
    """Backup MongoDB database to timestamped tar.gz file."""
    import os
    from datetime import datetime

    print(color("=== MongoDB Backup ===", Colors.CYAN))
    print()

    # Check if mongo container is running
    rc, _, _ = run_quiet(["podman", "ps", "-q", "-f", "name=^mongo$"])
    if rc != 0:
        print(color("✗ MongoDB container not running", Colors.RED))
        print("  Start it with: ./visp-podman.py start mongo")
        sys.exit(1)

    # Get MongoDB version from running container
    print("Detecting MongoDB version...")
    rc, stdout, stderr = run_quiet(["podman", "exec", "mongo", "mongod", "--version"])

    if rc == 0 and stdout:
        # Extract version (e.g., "db version v6.0.14")
        version_lines = [
            line for line in stdout.split("\n") if "version" in line.lower()
        ]
        if version_lines:
            version_line = version_lines[0]
            # Extract version number
            if "v" in version_line:
                mongo_version = (
                    version_line.split("v")[-1].split()[0].split("-")[0]
                )  # Handle v6.0.14-rc1
            else:
                mongo_version = "unknown"
        else:
            mongo_version = "unknown"
    else:
        print(color(f"  ⚠ Could not detect version: {stderr}", Colors.YELLOW))
        mongo_version = "unknown"

    # Load environment variables to get MongoDB password
    env_vars = load_all_env_vars()
    mongo_password = env_vars.get("MONGO_ROOT_PASSWORD")

    if not mongo_password:
        print(
            color("✗ MONGO_ROOT_PASSWORD not found in .env or .env.secrets", Colors.RED)
        )
        sys.exit(1)

    # Create backup filename with timestamp and version
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"visp_mongodb_{mongo_version}_{timestamp}"
    backup_dir = f"/tmp/{backup_name}"

    print(f"  MongoDB version: {color(mongo_version, Colors.GREEN)}")
    print(f"  Backup name: {color(backup_name + '.tar.gz', Colors.GREEN)}")
    print()

    # Run mongodump inside container
    print("Running mongodump...")
    result = run(
        [
            "podman",
            "exec",
            "mongo",
            "mongodump",
            "--username=root",
            f"--password={mongo_password}",
            "--authenticationDatabase=admin",
            f"--out={backup_dir}",
        ],
        capture=False,
    )

    if result.returncode != 0:
        print(color("✗ Backup failed", Colors.RED))
        sys.exit(1)

    # Create tar.gz from the dump
    print("\nCompressing backup...")
    result = run(
        [
            "podman",
            "exec",
            "mongo",
            "tar",
            "-czf",
            f"{backup_dir}.tar.gz",
            "-C",
            "/tmp",
            backup_name,
        ],
        capture=False,
    )

    if result.returncode != 0:
        print(color("✗ Compression failed", Colors.RED))
        sys.exit(1)

    # Copy backup out of container
    output_path = args.output or f"./{backup_name}.tar.gz"
    print(f"\nCopying to {output_path}...")
    result = run(["podman", "cp", f"mongo:{backup_dir}.tar.gz", output_path])

    if result.returncode != 0:
        print(color("✗ Copy failed", Colors.RED))
        sys.exit(1)

    # Cleanup inside container
    run_quiet(
        ["podman", "exec", "mongo", "rm", "-rf", backup_dir, f"{backup_dir}.tar.gz"]
    )

    # Get file size
    if os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        print(
            color(
                f"\n✓ Backup complete: {output_path} ({size_mb:.1f} MB)", Colors.GREEN
            )
        )
        print()
        print("To restore:")
        print(f"  ./visp-podman.py restore {output_path}")
        print()
        print(
            color(
                "Note: This backup contains ONLY the database (users, sessions, metadata).",
                Colors.YELLOW,
            )
        )
        print(
            "      Audio files in mounts/repositories/ should be backed up separately."
        )
    else:
        print(color("✗ Backup file not created", Colors.RED))
        sys.exit(1)


def cmd_restore(args):
    """Restore MongoDB database from backup file."""
    import os

    backup_file = args.backup_file

    if not os.path.exists(backup_file):
        print(color(f"✗ Backup file not found: {backup_file}", Colors.RED))
        sys.exit(1)

    # Check if mongo container is running
    rc, _, _ = run_quiet(["podman", "ps", "-q", "-f", "name=^mongo$"])
    if rc != 0:
        print(color("✗ MongoDB container not running", Colors.RED))
        print("  Start it with: ./visp-podman.py start mongo")
        sys.exit(1)

    print(color("=== MongoDB Restore ===", Colors.YELLOW))
    print()
    print(color("⚠️  WARNING: This will REPLACE all data in MongoDB!", Colors.YELLOW))
    print(f"Backup file: {backup_file}")

    # Load environment variables to get MongoDB password
    env_vars = load_all_env_vars()
    mongo_password = env_vars.get("MONGO_ROOT_PASSWORD")

    if not mongo_password:
        print(
            color("✗ MONGO_ROOT_PASSWORD not found in .env or .env.secrets", Colors.RED)
        )
        sys.exit(1)

    if not args.force:
        print()
        response = input("Continue? (yes/no): ")
        if response.lower() != "yes":
            print("Restore cancelled")
            sys.exit(0)

    # Copy backup into container
    print("\nCopying backup into container...")
    result = run(["podman", "cp", backup_file, "mongo:/tmp/restore.tar.gz"])

    if result.returncode != 0:
        print(color("✗ Copy failed", Colors.RED))
        sys.exit(1)

    # Extract backup
    print("Extracting backup...")
    result = run(
        ["podman", "exec", "mongo", "tar", "-xzf", "/tmp/restore.tar.gz", "-C", "/tmp"]
    )

    if result.returncode != 0:
        print(color("✗ Extraction failed", Colors.RED))
        run_quiet(["podman", "exec", "mongo", "rm", "-f", "/tmp/restore.tar.gz"])
        sys.exit(1)

    # Find the extracted directory
    print("Finding backup directory...")
    rc, stdout, _ = run_quiet(
        [
            "podman",
            "exec",
            "mongo",
            "find",
            "/tmp",
            "-maxdepth",
            "1",
            "-name",
            "visp_mongodb_*",
            "-type",
            "d",
        ]
    )

    if rc != 0 or not stdout.strip():
        print(color("✗ Could not find backup directory in archive", Colors.RED))
        run_quiet(["podman", "exec", "mongo", "rm", "-rf", "/tmp/restore.tar.gz"])
        sys.exit(1)

    backup_dir = stdout.strip().split("\n")[0]
    print(f"  Found: {backup_dir}")

    # Run mongorestore
    print("\nRestoring database...")
    result = run(
        [
            "podman",
            "exec",
            "mongo",
            "mongorestore",
            "--username=root",
            f"--password={mongo_password}",
            "--authenticationDatabase=admin",
            "--drop",  # Drop existing collections before restoring
            backup_dir,
        ],
        capture=False,
    )

    # Cleanup
    print("\nCleaning up...")
    run_quiet(
        ["podman", "exec", "mongo", "rm", "-rf", "/tmp/restore.tar.gz", backup_dir]
    )

    if result.returncode == 0:
        print(color("\n✓ Restore complete", Colors.GREEN))
        print()
        print("You may need to restart services:")
        print("  ./visp-podman.py restart apache session-manager")
    else:
        print(color("\n✗ Restore failed", Colors.RED))
        sys.exit(1)


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
    p_install.add_argument(
        "-m", "--mode", choices=["dev", "prod"], help="Deployment mode (dev or prod)"
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

    # mode
    p_mode = subparsers.add_parser(
        "mode", aliases=["m"], help="Show or set deployment mode"
    )
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
    p_build = subparsers.add_parser(
        "build", aliases=["b"], help="Build container images"
    )
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

    # network
    subparsers.add_parser(
        "network", aliases=["n", "net"], help="Show network info and DNS status"
    )

    # images
    subparsers.add_parser(
        "images", aliases=["img"], help="List VISP container images and build status"
    )

    # backup
    p_backup = subparsers.add_parser("backup", help="Backup MongoDB database")
    p_backup.add_argument(
        "--output",
        "-o",
        help="Output file path (default: ./visp_mongodb_VERSION_TIMESTAMP.tar.gz)",
    )

    # restore
    p_restore = subparsers.add_parser(
        "restore", help="Restore MongoDB database from backup"
    )
    p_restore.add_argument("backup_file", help="Backup file to restore")
    p_restore.add_argument(
        "--force", action="store_true", help="Skip confirmation prompt"
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
        "build": cmd_build,
        "b": cmd_build,
        "network": cmd_network,
        "n": cmd_network,
        "net": cmd_network,
        "images": cmd_images,
        "img": cmd_images,
        "backup": cmd_backup,
        "restore": cmd_restore,
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
