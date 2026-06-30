#!/usr/bin/env python3
"""
VISP Control - Unified management tool for VISP Podman deployment

Commands:
  status      Show status of all services and containers
  logs        View logs (replaces visp-logs.sh)
  start       Start service(s)
  stop        Stop service(s)
  up          Enable and start service(s)
  down        Stop and disable service(s)
  restart     Restart service(s) or entire cluster
  install     Link quadlet files to systemd directory
  uninstall   Remove quadlet links from systemd directory
  reload      Reload systemd daemon (after quadlet changes)
  apply       Apply quadlet changes in one step (install --force + reload + restart)
  mode        Show or set deployment mode (dev/prod)
  build       Build container images (supports --no-cache, --pull)
  exec        Execute command in container
  shell       Open shell in container
  backup      Backup MongoDB database to tar.gz
  restore     Restore MongoDB database from backup

Build examples:
  ./visp.py build                         # Build all services
  ./visp.py build session-manager         # Build single service
  ./visp.py build container-agent         # Build container-agent (Node.js)
  ./visp.py build webclient --config visp.dev # Build webclient with visp.dev config (default)
  ./visp.py build --no-cache              # Clean rebuild (no cache)
  ./visp.py build --list                  # List buildable services

Backup/Restore examples:
  ./visp.py backup                        # Backup to current directory
  ./visp.py backup -o /backups/db.tar.gz  # Backup to specific path
  ./visp.py restore backup.tar.gz         # Restore with confirmation
  ./visp.py restore backup.tar.gz --force # Restore without confirmation

Mode examples:
  ./visp.py mode                          # Show current mode
  ./visp.py mode dev                      # Set to development mode
  ./visp.py mode prod                     # Set to production mode
  ./visp.py install --mode prod --force   # Install prod quadlets
"""

import argparse
import sys
from pathlib import Path

from vispctl.build import (
    BUILD_CONFIGS,
    NODE_BUILD_CONFIGS,
)
from vispctl.build import (
    cmd_build as _cmd_build,
)
from vispctl.build import (
    cmd_build_list as _cmd_build_list,
)
from vispctl.cleanup_containers import cleanup_containers
from vispctl.exceptions import VispError
from vispctl.images import ImageManager
from vispctl.logs import CONTAINER_LOG_FILES, view_logs
from vispctl.network import cmd_network as _cmd_network
from vispctl.permissions import cmd_fix_permissions as _cmd_fix_permissions
from vispctl.quadlets import (
    cmd_apply as _cmd_apply,
)
from vispctl.quadlets import (
    get_current_mode,
    render_quadlet_template,
    set_current_mode,
)
from vispctl.runner import Colors, Runner, color
from vispctl.service import (
    DEFAULT_SERVICES,
    Service,
    get_disabled_optional_services,
    get_runtime_services,
    resolve_services,
)
from vispctl.service_manager import ServiceManager
from vispctl.status import show_container_list, show_network_list, show_quadlet_table

# Configuration
PROJECT_DIR = Path(__file__).parent.resolve()
SYSTEMD_QUADLETS_DIR = Path.home() / ".config/containers/systemd"

SERVICES = DEFAULT_SERVICES
NETWORK_SERVICES = [s for s in SERVICES if s.type == "network"]
BUILDABLE_SERVICES = list(BUILD_CONFIGS.keys())
ALL_BUILDABLE = BUILDABLE_SERVICES + list(NODE_BUILD_CONFIGS.keys())

RUNNER = Runner()


def _container_services(services: list[Service]) -> list[Service]:
    """Filter service list to container services."""
    return [s for s in services if s.type == "container"]


# === Status Commands ===


def cmd_status(args):  # noqa: ARG001
    """Show status of all services and containers."""
    print(color("=== VISP Service Status ===", Colors.CYAN))
    print()

    runtime_services = get_runtime_services()

    # Use ServiceManager for service status
    sm = ServiceManager(RUNNER, runtime_services)
    sm.status()

    disabled_optional_services = get_disabled_optional_services()
    if disabled_optional_services:
        print()
        print(color("=== Disabled Optional Services ===", Colors.CYAN))
        for service_name, env_var in disabled_optional_services.items():
            print(f"  ○ {service_name}: disabled via {env_var}=false in .env")

    print()
    show_quadlet_table(runtime_services, get_current_mode(), RUNNER, SYSTEMD_QUADLETS_DIR, render_quadlet_template)

    print()
    show_container_list(RUNNER)

    print()
    show_network_list(RUNNER)


# === Log Commands ===


def cmd_logs(args):
    """View logs from services (and optionally debug diagnostics)."""
    view_logs(
        args,
        runner=RUNNER,
        container_log_files=CONTAINER_LOG_FILES,
        systemd_dir=SYSTEMD_QUADLETS_DIR,
        get_runtime_services=get_runtime_services,
        get_all_services=lambda: get_runtime_services(include_disabled=True),
        resolve_services=lambda s: resolve_services(s, PROJECT_DIR),
        container_services=_container_services,
    )


# === Service Control Commands ===


def cmd_start(args):
    """Start service(s)."""
    sm = ServiceManager(RUNNER, get_runtime_services(include_disabled=True))
    services = args.services
    if not services or services == ["all"]:
        target_names = [svc.name for svc in _container_services(resolve_services("all", PROJECT_DIR))]
        sm.start(target_names)
    else:
        target_names = [svc.name for s in services for svc in resolve_services(s, PROJECT_DIR)]
        sm.start(target_names)


def cmd_stop(args):
    """Stop service(s)."""
    sm = ServiceManager(RUNNER, get_runtime_services(include_disabled=True))
    services = args.services
    if not services or services == ["all"]:
        sm.stop("all")
    else:
        target_names = [svc.name for s in services for svc in resolve_services(s, PROJECT_DIR, include_disabled=True)]
        sm.stop(target_names)


def cmd_up(args):
    """Enable and start service(s)."""
    sm = ServiceManager(RUNNER, get_runtime_services(include_disabled=True))
    services = args.services
    if not services or services == ["all"]:
        target_names = [svc.name for svc in _container_services(resolve_services("all", PROJECT_DIR))]
    else:
        target_names = [svc.name for s in services for svc in resolve_services(s, PROJECT_DIR)]

    sm.enable(target_names)
    sm.start(target_names)


def cmd_down(args):
    """Stop and disable service(s)."""
    sm = ServiceManager(RUNNER, get_runtime_services(include_disabled=True))
    services = args.services
    if not services or services == ["all"]:
        target_names = [
            svc.name for svc in _container_services(resolve_services("all", PROJECT_DIR, include_disabled=True))
        ]
    else:
        target_names = [svc.name for s in services for svc in resolve_services(s, PROJECT_DIR, include_disabled=True)]

    sm.stop(target_names)
    sm.disable(target_names)


def cmd_restart(args):
    """Restart service(s) or entire cluster."""
    sm = ServiceManager(RUNNER, get_runtime_services(include_disabled=True))
    services = args.services

    if not services or services == ["all"]:
        print(color("=== Restarting entire VISP cluster ===", Colors.CYAN))
        print()
        print(color("Stopping services...", Colors.YELLOW))
        sm.stop("all")
        print()
        print(color("Starting services...", Colors.GREEN))
        target_names = [svc.name for svc in _container_services(resolve_services("all", PROJECT_DIR))]
        sm.start(target_names)
    else:
        # For individual services, stop then start the specific names
        target_names = [svc.name for s in services for svc in resolve_services(s, PROJECT_DIR)]
        sm.stop(target_names)
        sm.start(target_names)


# === Network Backend Management ===
# Free-standing wrappers (check_netavark, configure_netavark, etc.) were removed —
# they were dead code. Call NetworkManager methods directly (as cmd_install already does).


# === Installation Commands ===


def cmd_install(args):
    """Link quadlet files to systemd directory."""
    from vispctl.install import run_install

    mode = getattr(args, "mode", None) or get_current_mode()
    services = resolve_services(args.service, PROJECT_DIR)
    run_install(
        project_dir=PROJECT_DIR,
        systemd_dir=SYSTEMD_QUADLETS_DIR,
        runner=RUNNER,
        mode=mode,
        service_arg=args.service,
        services=services,
        all_services=SERVICES,
        disabled_optional=get_disabled_optional_services(),
        render_fn=render_quadlet_template,
        force=args.force,
    )


def cmd_uninstall(args):
    """Remove quadlet links from systemd directory."""
    services = resolve_services(args.service, PROJECT_DIR, include_disabled=True)

    # Stop services first
    if not args.keep_running:
        print(color("Stopping services...", Colors.YELLOW))
        sm = ServiceManager(RUNNER, SERVICES)
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

    # Optionally remove Podman networks
    if getattr(args, "remove_networks", False):
        print()
        print(color("Removing Podman networks...", Colors.CYAN))
        # systemd prefixes network unit names with "systemd-" at runtime
        visp_networks = [f"systemd-{svc.name}" for svc in NETWORK_SERVICES]
        for net_name in visp_networks:
            rc, _, stderr = RUNNER.run_quiet(["podman", "network", "rm", net_name])
            if rc == 0:
                print(color(f"  ✓ {net_name}: removed", Colors.GREEN))
            elif "no such network" in stderr.lower() or "not found" in stderr.lower():
                print(f"  ○ {net_name}: not found")
            else:
                print(color(f"  ✗ {net_name}: {stderr.strip()}", Colors.RED))

    print()
    print()
    print("Run './visp.py reload' to apply changes.")


def cmd_reload(args):  # noqa: ARG001
    """Reload systemd daemon to pick up quadlet changes."""
    print("Reloading systemd daemon...")
    result = RUNNER.systemctl("daemon-reload")
    if result.returncode == 0:
        print(color("Done. Quadlet changes are now active.", Colors.GREEN))
    else:
        print(color(f"Failed: {result.stderr}", Colors.RED))


def cmd_apply(args):
    _cmd_apply(
        args,
        project_dir=PROJECT_DIR,
        systemd_dir=SYSTEMD_QUADLETS_DIR,
        runner=RUNNER,
        build_configs=BUILD_CONFIGS,
        network_services=NETWORK_SERVICES,
    )


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
        print(f"  1. ./visp.py install --mode {new_mode} --force")
        print("  2. ./visp.py reload")
        print("  3. ./visp.py restart all")
    else:
        # Show current mode
        current = get_current_mode()
        print(color("=== Deployment Mode ===", Colors.CYAN))
        print()
        print(f"  Current mode: {color(current, Colors.GREEN if current == 'prod' else Colors.YELLOW)}")
        print()
        print(color("Mode differences:", Colors.CYAN))
        print("  dev      - Source code mounts, container-agent mounted")
        print("  prod     - Code baked into images, optimized for deployment")
        print()
        print("  Change mode: ./visp.py mode [dev|prod]")


# === Container Commands ===


def cmd_exec(args):
    """Execute command in container."""
    container = args.container
    RUNNER.run(["podman", "exec", "-it", container, *args.exec_command], check=False)


def cmd_shell(args):
    """Open shell in container."""
    container = args.container
    shell = args.shell or "/bin/bash"
    RUNNER.run(["podman", "exec", "-it", container, shell], check=False)


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
    except (OSError, RuntimeError, ValueError) as e:
        print(color(f"Error during cleanup-containers: {e}", Colors.RED))


def cmd_session_doctor(args):
    """Diagnose session containers, proxy sidecars, and socket directories."""
    from vispctl.session_doctor import run_session_doctor

    issues = run_session_doctor(
        show_healthy=not getattr(args, "problems_only", False),
        problems_only=getattr(args, "problems_only", False),
        json_output=getattr(args, "json", False),
        verbose=getattr(args, "verbose", False),
        clean=getattr(args, "clean", False),
        yes=getattr(args, "yes", False),
    )
    if issues:
        sys.exit(1)


def cmd_build(args):
    _cmd_build(
        args,
        runner=RUNNER,
        build_configs=BUILD_CONFIGS,
        node_configs=NODE_BUILD_CONFIGS,
        all_buildable=ALL_BUILDABLE,
    )


def cmd_build_list(args):  # noqa: ARG001
    _cmd_build_list(args, build_configs=BUILD_CONFIGS, node_configs=NODE_BUILD_CONFIGS)


# === Debug Commands ===


def cmd_debug(args):
    """Shorthand for 'logs [service] --debug'. Shows diagnostics + logs."""
    service = args.service

    def _make_logs_args(svc_name: str) -> argparse.Namespace:
        return argparse.Namespace(
            service=svc_name,
            debug=True,
            follow=False,
            no_follow=True,
            lines=None,
            since=None,
            priority=None,
            journal_only=False,
        )

    if service == "all":
        for svc in _container_services(get_runtime_services()):
            print(color(f"\n{'=' * 60}", Colors.CYAN))
            cmd_logs(_make_logs_args(svc.name))
    else:
        cmd_logs(_make_logs_args(service))


# === Network Info ===


def cmd_network(args):
    _cmd_network(args, runner=RUNNER)


def cmd_images(args):
    """List VISP container images and their status."""
    if hasattr(args, "subcommand") and args.subcommand == "base":
        return cmd_images_base(args)

    im = ImageManager(RUNNER, BUILD_CONFIGS, NETWORK_SERVICES)
    im.display_visp_images()
    im.display_network_info()


def cmd_images_base(args):  # noqa: ARG001
    """List all base images used in Dockerfiles."""
    im = ImageManager(RUNNER, BUILD_CONFIGS, NETWORK_SERVICES)
    im.display_base_images()


# === Deploy Commands ===


def cmd_deploy_status(args):
    """Show repository status and version drift."""
    from vispctl.deploy import DeployManager

    dm = DeployManager(runner=RUNNER)
    all_clean = dm.check_status(fetch=not getattr(args, "no_fetch", False))

    if getattr(args, "strict", False) and not all_clean:
        print()
        print(color("✗ Strict mode: version drift detected", Colors.RED))
        sys.exit(1)


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
    _cmd_fix_permissions(args, project_dir=PROJECT_DIR, runner=RUNNER)


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

    only_raw = getattr(args, "only", None)
    only_ids = set(only_raw.split(",")) if only_raw else None

    issues = run_doctor(
        project_id=getattr(args, "project_id", None),
        show_files=not getattr(args, "no_files", False),
        show_healthy=not getattr(args, "problems_only", False),
        problems_only=getattr(args, "problems_only", False),
        json_output=getattr(args, "json", False),
        fix_cache=getattr(args, "fix_cache", False),
        fix=getattr(args, "fix", False),
        apply=getattr(args, "apply", False),
        only_ids=only_ids,
        session_filter=getattr(args, "session", None),
        bundle_filter=getattr(args, "bundle", None),
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


# === Main ===


def _check_systemd_user_bus() -> None:
    from vispctl.runner import check_systemd_user_bus

    check_systemd_user_bus()


def main():
    _check_systemd_user_bus()

    parser = argparse.ArgumentParser(
        description="VISP Control - Unified management tool for VISP Podman deployment",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  visp-ctl status              # Show all service status
  visp-ctl logs -f             # Follow all logs
  visp-ctl logs session-manager -n 200  # Last 200 lines from session-manager
  visp-ctl up all              # Enable and start all services
  visp-ctl down all            # Stop and disable all services
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
    subparsers.add_parser("status", aliases=["s"], help="Show status of all services").set_defaults(func=cmd_status)

    # logs
    p_logs = subparsers.add_parser("logs", aliases=["l"], help="View logs from services")
    p_logs.set_defaults(func=cmd_logs)
    p_logs.add_argument("service", nargs="?", default="all", help="Service name or 'all'")
    follow_group = p_logs.add_mutually_exclusive_group()
    follow_group.add_argument("-f", "--follow", action="store_true", help="Follow logs")
    follow_group.add_argument(
        "--no-follow",
        action="store_true",
        help="Do not follow logs (single container services follow by default)",
    )
    p_logs.add_argument("-n", "--lines", type=int, help="Number of lines to show")
    p_logs.add_argument("--since", help="Show logs since TIME (e.g., '1 hour ago')")
    p_logs.add_argument("-p", "--priority", help="Filter by priority")
    p_logs.add_argument(
        "--journal-only", action="store_true", help="Show only journalctl output, skip container app logs"
    )
    p_logs.add_argument(
        "--debug", action="store_true", help="Include service status, container info, and quadlet diagnostics"
    )

    # start
    p_start = subparsers.add_parser("start", help="Start service(s)")
    p_start.set_defaults(func=cmd_start)
    p_start.add_argument("services", default=["all"], nargs="*", help="Service name(s) or 'all'")

    # stop
    p_stop = subparsers.add_parser("stop", help="Stop service(s)")
    p_stop.set_defaults(func=cmd_stop)
    p_stop.add_argument("services", default=["all"], nargs="*", help="Service name(s) or 'all'")

    # up
    p_up = subparsers.add_parser("up", help="Enable and start service(s)")
    p_up.set_defaults(func=cmd_up)
    p_up.add_argument("services", default=["all"], nargs="*", help="Service name(s) or 'all'")

    # down
    p_down = subparsers.add_parser("down", help="Stop and disable service(s)")
    p_down.set_defaults(func=cmd_down)
    p_down.add_argument("services", default=["all"], nargs="*", help="Service name(s) or 'all'")

    # restart
    p_restart = subparsers.add_parser("restart", aliases=["r"], help="Restart service(s)")
    p_restart.set_defaults(func=cmd_restart)
    p_restart.add_argument("services", default=["all"], nargs="*", help="Service name(s) or 'all'")

    # install
    p_install = subparsers.add_parser("install", aliases=["i"], help="Link quadlet files to systemd")
    p_install.set_defaults(func=cmd_install)
    p_install.add_argument("service", default="all", nargs="?", help="Service name or 'all'")
    p_install.add_argument("-f", "--force", action="store_true", help="Overwrite existing links")
    p_install.add_argument("-m", "--mode", choices=["dev", "prod"], help="Deployment mode (dev or prod)")

    # uninstall
    p_uninstall = subparsers.add_parser("uninstall", aliases=["u"], help="Remove quadlet links")
    p_uninstall.set_defaults(func=cmd_uninstall)
    p_uninstall.add_argument("service", default="all", nargs="?", help="Service name or 'all'")
    p_uninstall.add_argument("--keep-running", action="store_true", help="Don't stop services first")
    p_uninstall.add_argument("--remove-networks", action="store_true", help="Also remove Podman networks")

    # reload
    subparsers.add_parser("reload", help="Reload systemd daemon").set_defaults(func=cmd_reload)

    # apply
    p_apply = subparsers.add_parser(
        "apply",
        aliases=["a"],
        help="Apply quadlet changes: install --force + reload + restart (one step)",
    )
    p_apply.set_defaults(func=cmd_apply)
    p_apply.add_argument(
        "service",
        nargs="?",
        default="all",
        help="Service to apply changes for (default: all)",
    )

    # mode
    p_mode = subparsers.add_parser("mode", aliases=["m"], help="Show or set deployment mode")
    p_mode.set_defaults(func=cmd_mode)
    p_mode.add_argument(
        "new_mode",
        nargs="?",
        choices=["dev", "prod"],
        help="Set mode to dev or prod (omit to show current mode)",
    )

    # debug
    p_debug = subparsers.add_parser("debug", aliases=["d"], help="Shorthand for 'logs --debug'")
    p_debug.set_defaults(func=cmd_debug)
    p_debug.add_argument("service", nargs="?", default="all", help="Service name or 'all' (default: all)")

    # exec
    p_exec = subparsers.add_parser("exec", aliases=["e"], help="Execute command in container")
    p_exec.set_defaults(func=cmd_exec)
    p_exec.add_argument("container", help="Container name (e.g. apache, session-manager)")
    p_exec.add_argument("exec_command", nargs="+", help="Command to run")

    # shell
    p_shell = subparsers.add_parser("shell", aliases=["sh"], help="Open shell in container")
    p_shell.set_defaults(func=cmd_shell)
    p_shell.add_argument("container", help="Container name (e.g. apache, session-manager)")
    p_shell.add_argument("--shell", default="/bin/bash", help="Shell to use (default: /bin/bash)")

    # cleanup-containers
    p_cleanup = subparsers.add_parser(
        "cleanup-containers",
        aliases=["cleanup"],
        help="Stop and remove session containers (legacy and current naming)",
    )
    p_cleanup.set_defaults(func=cmd_cleanup_containers)
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

    # session-doctor
    p_sdoctor = subparsers.add_parser(
        "session-doctor",
        aliases=["sd"],
        help="Diagnose session containers, proxy sidecars, and socket dirs",
    )
    p_sdoctor.set_defaults(func=cmd_session_doctor)
    p_sdoctor.add_argument(
        "--problems",
        action="store_true",
        dest="problems_only",
        help="Only show sessions with issues",
    )
    p_sdoctor.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON (for scripting)",
    )
    p_sdoctor.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show extra details (image names, etc.)",
    )
    p_sdoctor.add_argument(
        "--clean",
        action="store_true",
        help="Remove orphaned containers and stale socket directories",
    )
    p_sdoctor.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Do not prompt for confirmation (with --clean)",
    )

    # build
    p_build = subparsers.add_parser("build", aliases=["b"], help="Build container images")
    p_build.set_defaults(func=cmd_build)
    p_build.add_argument(
        "services",
        nargs="*",
        default=["all"],
        help=f"Services to build (default: all). Options: {', '.join(ALL_BUILDABLE)}",
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
    p_network.set_defaults(func=cmd_network)
    p_network.add_argument(
        "action",
        nargs="?",
        choices=["ensure"],
        help="Optional action: ensure (create required networks)",
    )

    # images
    p_images = subparsers.add_parser("images", aliases=["img"], help="List VISP container images and build status")
    p_images.set_defaults(func=cmd_images)
    p_images_sub = p_images.add_subparsers(dest="subcommand", help="Images subcommands")
    p_images_sub.add_parser("base", help="List all base images from Dockerfiles with versions").set_defaults(
        func=cmd_images_base
    )

    # deploy
    p_deploy = subparsers.add_parser("deploy", help="Manage deployments: version control, git repos, status")
    p_deploy_sub = p_deploy.add_subparsers(dest="deploy_command", help="Deploy subcommands", required=True)

    p_deploy_status = p_deploy_sub.add_parser("status", help="Check repository status and version drift")
    p_deploy_status.set_defaults(func=cmd_deploy_status)
    p_deploy_status.add_argument(
        "--no-fetch", action="store_true", help="Skip fetching from remotes (use cached remote state)"
    )
    p_deploy_status.add_argument(
        "--strict", action="store_true", help="Exit with code 1 if any version drift is detected (for CI/CD)"
    )

    p_deploy_lock = p_deploy_sub.add_parser("lock", help="Lock components to their current versions")
    p_deploy_lock.set_defaults(func=cmd_deploy_lock)
    p_deploy_lock.add_argument("components", nargs="*", help="Components to lock (specify names or use --all)")
    p_deploy_lock.add_argument("--all", action="store_true", help="Lock all components")

    p_deploy_unlock = p_deploy_sub.add_parser("unlock", help="Unlock components to track latest")
    p_deploy_unlock.set_defaults(func=cmd_deploy_unlock)
    p_deploy_unlock.add_argument("components", nargs="*", help="Components to unlock (specify names or use --all)")
    p_deploy_unlock.add_argument("--all", action="store_true", help="Unlock all components")

    p_deploy_rollback = p_deploy_sub.add_parser("rollback", help="Rollback components to their locked versions")
    p_deploy_rollback.set_defaults(func=cmd_deploy_rollback)
    p_deploy_rollback.add_argument("components", nargs="*", help="Components to rollback (specify names or use --all)")
    p_deploy_rollback.add_argument("--all", action="store_true", help="Rollback all components")

    p_deploy_update = p_deploy_sub.add_parser("update", help="Update external repositories to configured versions")
    p_deploy_update.set_defaults(func=cmd_deploy_update)
    p_deploy_update.add_argument("--force", action="store_true", help="Force update even with uncommitted changes")

    # fix-permissions
    p_fix = subparsers.add_parser(
        "fix-permissions",
        aliases=["fixperm"],
        help="Fix ownership and permissions for mount paths using podman unshare",
    )
    p_fix.set_defaults(func=cmd_fix_permissions)
    p_fix.add_argument(
        "--path",
        "-p",
        dest="paths",
        action="append",
        help="Path to fix (can be specified multiple times). Default: run the same permission repair used by install.",
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
            "namespace mapping for explicit --path targets "
            "(uses 'podman unshare chown 0:0'; no sudo)."
        ),
    )
    p_fix.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform the changes. Default: dry-run",
    )

    # backup
    p_backup = subparsers.add_parser("backup", help="Backup MongoDB database")
    p_backup.set_defaults(func=cmd_backup)
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
    p_restore.set_defaults(func=cmd_restore)
    p_restore.add_argument("backup_file", help="Backup file to restore")
    p_restore.add_argument("--force", action="store_true", help="Skip confirmation prompt")

    # users
    p_users = subparsers.add_parser("users", help="Manage users in MongoDB")
    p_users.set_defaults(func=cmd_users)
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
    p_doctor.set_defaults(func=cmd_doctor)
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
        help="Show fixable issues and what would change (dry-run). Combine with --apply to perform.",
    )
    p_doctor.add_argument(
        "--apply",
        action="store_true",
        help="Actually apply fixes (requires --fix). Without this, --fix is dry-run.",
    )
    p_doctor.add_argument(
        "--only",
        metavar="IDS",
        help="Comma-separated fix IDs to apply (e.g. 'a3f1,b2e4'). Others are skipped.",
    )
    p_doctor.add_argument(
        "--session",
        metavar="NAME",
        help="Restrict to a specific session name (e.g. 'Session_1')",
    )
    p_doctor.add_argument(
        "--bundle",
        metavar="NAME",
        help="Restrict to a specific bundle name (e.g. 'my_recording')",
    )

    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        return

    try:
        args.func(args)
    except VispError as e:
        print(color(f"Error: {e}", Colors.RED))
        sys.exit(1)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
