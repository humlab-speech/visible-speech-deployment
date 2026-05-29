"""Quadlet installation helpers for VISP.

Contains: template rendering, mode management, quadlet drift detection,
and service env-file setup.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .runner import Colors, color, load_env_vars
from .service import Service

# ---------------------------------------------------------------------------
# Mode and template helpers
# ---------------------------------------------------------------------------

# Resolved at import time from the location of this file's package root
_PROJECT_DIR = Path(__file__).parent.parent.resolve()
_QUADLETS_BASE_DIR = _PROJECT_DIR / "quadlets"
_MODE_FILE = _PROJECT_DIR / ".visp-mode"
_DEFAULT_MODE = "dev"


def render_quadlet_template(content: str) -> str:
    """Replace @@PLACEHOLDER@@ tokens in a quadlet file with live system values."""
    content = content.replace("@@PROJECT_DIR@@", str(_PROJECT_DIR))
    content = content.replace("@@UID@@", str(os.getuid()))
    env_vars = load_env_vars(_PROJECT_DIR / ".env")
    for key, value in env_vars.items():
        content = content.replace(f"@@{key}@@", value)
    return content


def get_current_mode() -> str:
    """Return the current deployment mode (dev or prod) from .visp-mode file."""
    if _MODE_FILE.exists():
        return _MODE_FILE.read_text().strip()
    return _DEFAULT_MODE


def set_current_mode(mode: str) -> None:
    """Persist the deployment mode to .visp-mode."""
    _MODE_FILE.write_text(mode)


def get_quadlets_dir(mode: str | None = None) -> Path:
    """Return the quadlets source directory for the given (or current) mode."""
    if mode is None:
        mode = get_current_mode()
    return _QUADLETS_BASE_DIR / mode


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
