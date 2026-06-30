"""Quadlet installation helpers for VISP.

Contains: template rendering, mode management, quadlet drift detection,
and service env-file setup.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .config import get_config
from .runner import Colors, color, load_env_vars
from .service import Service

_DEFAULT_MODE = "dev"


def _get_project_dir() -> Path:
    """Return project directory from config."""
    return get_config().project_dir


def render_quadlet_template(content: str) -> str:
    """Replace @@PLACEHOLDER@@ tokens in a quadlet file with live system values."""
    project_dir = _get_project_dir()
    content = content.replace("@@PROJECT_DIR@@", str(project_dir))
    content = content.replace("@@UID@@", str(os.getuid()))
    env_vars = load_env_vars(project_dir / ".env")
    for key, value in env_vars.items():
        content = content.replace(f"@@{key}@@", value)
    return content


def get_current_mode() -> str:
    """Return the current deployment mode (dev or prod) from .visp-mode file."""
    mode_file = _get_project_dir() / ".visp-mode"
    if mode_file.exists():
        return mode_file.read_text().strip()
    return _DEFAULT_MODE


def set_current_mode(mode: str) -> None:
    """Persist the deployment mode to .visp-mode."""
    (_get_project_dir() / ".visp-mode").write_text(mode)


def get_quadlets_dir(mode: str | None = None) -> Path:
    """Return the quadlets source directory for the given (or current) mode."""
    if mode is None:
        mode = get_current_mode()
    return _get_project_dir() / "quadlets" / mode


def get_quadlet_drift(
    services: list[Service],
    quadlets_dir: Path,
    systemd_dir: Path,
    render_fn,
) -> tuple[list[Service], list[Service]]:
    """Detect which quadlet files have drifted from their source templates.

    Returns:
        (drifted, not_installed)
        drifted       — installed but content differs from rendered template
        not_installed — source exists but no installed file yet
    """
    drifted: list[Service] = []
    not_installed: list[Service] = []
    for svc in services:
        source = quadlets_dir / svc.file
        target = systemd_dir / svc.file
        if not source.exists():
            continue
        expected = render_fn(source.read_text())
        if not target.exists():
            not_installed.append(svc)
        elif target.read_text() != expected:
            drifted.append(svc)
    return drifted, not_installed


def setup_service_env_files(project_dir: Path) -> None:
    """Create service-specific .env files from templates if they don't exist.

    Sensitive values (MONGO_URI, MONGO_PASSWORD, MEDIA_FILE_BASE_URL) are injected
    at runtime via Podman Secrets (see Secret= lines in the quadlet files), so this
    function only copies the template with non-secret defaults.
    """
    print(color("Setting up service environment files...", Colors.CYAN))

    # --- emu-webapp-server ---
    emu_env_target = project_dir / "mounts/emu-webapp-server/.env"
    emu_env_example = project_dir / "external/emu-webapp-server/.env-example"
    if emu_env_target.exists():
        print("  ○ mounts/emu-webapp-server/.env already exists")
    elif emu_env_example.exists():
        # Copy template and strip out secrets (they come via Podman Secrets now)
        content = emu_env_example.read_text()
        lines = []
        secret_keys = {"MONGO_URI", "MONGO_ROOT_PASSWORD", "MEDIA_FILE_BASE_URL"}
        for line in content.splitlines():
            key = line.split("=", 1)[0].strip() if "=" in line else ""
            if key not in secret_keys:
                lines.append(line)
        emu_env_target.parent.mkdir(parents=True, exist_ok=True)
        emu_env_target.write_text("\n".join(lines) + "\n")
        print(color("  ✓ Created mounts/emu-webapp-server/.env (secrets via Podman Secrets)", Colors.GREEN))
    else:
        print(color("  ⚠ external/emu-webapp-server/.env-example not found — run 'deploy update' first", Colors.YELLOW))

    # --- wsrng-server ---
    wsrng_env_target = project_dir / "external/wsrng-server/.env"
    wsrng_env_example = project_dir / "external/wsrng-server/.env-example"
    if wsrng_env_target.exists():
        print("  ○ external/wsrng-server/.env already exists")
    elif wsrng_env_example.exists():
        # Copy template; MONGO_PASSWORD is overridden by Podman Secret at runtime
        shutil.copy(wsrng_env_example, wsrng_env_target)
        print(color("  ✓ Created external/wsrng-server/.env (MONGO_PASSWORD via Podman Secret)", Colors.GREEN))
    else:
        print(color("  ⚠ external/wsrng-server/.env-example not found — run 'deploy update' first", Colors.YELLOW))


def cmd_apply(
    args,
    project_dir: Path | None = None,
    systemd_dir: Path | None = None,
    runner=None,
    build_configs=None,
    network_services=None,
) -> None:
    """Apply quadlet changes and restart containers running stale images."""
    from .images import ImageManager
    from .install import install_quadlets
    from .service import get_runtime_services, resolve_services
    from .service_manager import ServiceManager

    if project_dir is None:
        project_dir = get_config().project_dir
    if systemd_dir is None:
        systemd_dir = get_config().systemd_dir

    service = getattr(args, "service", "all")
    mode = get_current_mode()
    quadlets_dir = get_quadlets_dir(mode)
    services = resolve_services(service, project_dir)

    drifted, not_installed = get_quadlet_drift(services, quadlets_dir, systemd_dir, render_quadlet_template)

    im = ImageManager(runner, build_configs or {}, network_services or [])
    stale_image = im.get_stale_containers(services)
    quadlet_names = {s.name for s in drifted + not_installed}
    stale_image_only = [s for s in stale_image if s.name not in quadlet_names]

    if not drifted and not not_installed and not stale_image_only:
        print(
            color(
                f"All quadlets are up to date and all containers are running the latest images ({mode} mode).",
                Colors.GREEN,
            )
        )
        return

    to_update = drifted + not_installed
    if to_update:
        print(color(f"=== Applying quadlet changes ({mode} mode) ===", Colors.CYAN))
        print()
        if drifted:
            print(color(f"  Out of date ({len(drifted)}):", Colors.YELLOW))
            for svc in drifted:
                print(f"    - {svc.file}")
        if not_installed:
            print(color(f"  Not installed ({len(not_installed)}):", Colors.YELLOW))
            for svc in not_installed:
                print(f"    - {svc.file}")
        print()

        print(color("Installing quadlets...", Colors.CYAN))
        install_quadlets(quadlets_dir, systemd_dir, to_update, render_quadlet_template, force=True)
        print()

        print(color("Reloading systemd daemon...", Colors.CYAN))
        result = runner.systemctl("daemon-reload")
        if result.returncode != 0:
            print(color(f"  daemon-reload failed: {result.stderr}", Colors.RED))
            return
        print(color("  Daemon reloaded", Colors.GREEN))
        print()

    quadlet_restart = [svc for svc in to_update if svc.file.endswith(".container")]
    restart_targets = quadlet_restart + stale_image_only

    if not restart_targets:
        print(color("No container services to restart.", Colors.GREEN))
        return

    if stale_image_only:
        print(color(f"  Stale image ({len(stale_image_only)}):", Colors.YELLOW))
        for svc in stale_image_only:
            print(f"    - {svc.name}")
        print()

    print(color(f"Restarting {len(restart_targets)} service(s)...", Colors.CYAN))
    sm = ServiceManager(runner, get_runtime_services(project_dir, include_disabled=True))
    target_names = [svc.name for svc in restart_targets]
    sm.stop(target_names)
    sm.start(target_names)
    print()
    print(color("Done.", Colors.GREEN))
