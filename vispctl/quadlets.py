"""Quadlet installation helpers for VISP.

Extracted from visp.py: quadlet drift detection and service env-file setup.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .runner import Colors, color
from .service import Service


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
