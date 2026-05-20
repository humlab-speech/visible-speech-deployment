"""
vispctl/install.py — Installation helpers extracted from cmd_install.

Each function is a self-contained phase of the install process, accepting
all dependencies as explicit parameters so they can be unit-tested without
a running system.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from vispctl.runner import Colors, color
from vispctl.service import Service


def scaffold_directories(
    project_dir: Path,
    quadlets_dir: Path,
    render_fn: Callable[[str], str],
) -> int:
    """
    Parse Volume= lines from all *.container quadlet files and create any
    missing host-side directories (or placeholder files for file mounts).

    Also ensures the WhisperVault socket directory and Podman socket-proxy
    directory exist regardless of quadlet contents.

    Returns the number of paths created.
    """
    created = 0
    project_dir_str = str(project_dir)

    for quadlet_file in sorted(quadlets_dir.glob("*.container")):
        rendered_content = render_fn(quadlet_file.read_text())
        for line in rendered_content.splitlines():
            line = line.strip()
            if line.startswith("#") or not line.startswith(f"Volume={project_dir_str}/"):
                continue
            # Extract source path (before the first ":")
            rel_path = line.split("=", 1)[1].split(":")[0].replace(f"{project_dir_str}/", "")
            # Skip external/ — those are managed by 'deploy update'
            if rel_path.startswith("external/"):
                continue
            target = project_dir / rel_path
            if target.exists():
                continue
            # If the leaf name has a dot, it's likely a file — ensure its parent exists
            # and touch a placeholder so Podman can bind-mount it.
            if "." in Path(rel_path).name:
                if not target.parent.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    created += 1
                if not target.exists():
                    target.touch()
                    created += 1
            else:
                target.mkdir(parents=True, exist_ok=True)
                created += 1

    # Ensure the WhisperVault Unix socket directory exists.
    whisper_sock_dir = project_dir / "mounts" / "whisper" / "api"
    if not whisper_sock_dir.exists():
        whisper_sock_dir.mkdir(parents=True, exist_ok=True)
        print(color("  ✓ Created mounts/whisper/api/ (WhisperVault socket directory)", Colors.GREEN))

    # Ensure the Podman socket proxy directory exists.
    proxy_sock_dir = project_dir / "mounts" / "podman-proxy"
    if not proxy_sock_dir.exists():
        proxy_sock_dir.mkdir(parents=True, exist_ok=True)
        print(color("  ✓ Created mounts/podman-proxy/ (Podman socket proxy directory)", Colors.GREEN))

    return created


def fix_writable_permissions(project_dir: Path) -> int:
    """
    Ensure container-writable directories have mode 0o777.

    In rootless Podman the host user maps to UID 0 inside the container,
    so host-owned directories appear as root:root (755) to Apache's
    www-data (UID 33). This sets specific directories world-writable.

    Falls back to ``podman unshare chmod 777`` for directories owned by a
    sub-UID from rootless Podman.

    Returns the number of directories whose permissions were changed.
    """
    writable_dirs = [
        project_dir / "mounts/apache/apache/uploads",
        project_dir / "mounts/repositories",
        project_dir / "mounts/api-logs/logs",
        project_dir / "mounts/apache/apache/logs/apache2",
        project_dir / "mounts/apache/apache/logs/shibboleth",
        project_dir / "mounts/session-manager/logs",
        project_dir / "mounts/sessions",
        project_dir / "mounts/matomo/html",
        project_dir / "mounts/podman-proxy",
    ]
    fixed = 0
    for d in writable_dirs:
        if not d.exists():
            continue
        current_mode = d.stat().st_mode & 0o777
        if current_mode == 0o777:
            continue
        try:
            d.chmod(0o777)
        except PermissionError:
            # Directory owned by a subuid — use podman unshare
            result = subprocess.run(
                ["podman", "unshare", "chmod", "777", str(d)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                print(color(f"  ✗ Failed to fix permissions on {d}: {result.stderr.strip()}", Colors.RED))
                continue
        fixed += 1
    return fixed


def generate_tracker_config(project_dir: Path, env_vars: dict[str, str]) -> None:
    """
    Render ``mounts/apache/apache/vc.js`` from the ``*.template`` file,
    substituting ``{{BASE_DOMAIN}}``.

    If ``BASE_DOMAIN`` is not set or the template is missing, writes a
    placeholder ``vc.js`` so the Apache bind-mount never fails.
    """
    tracker_template = project_dir / "mounts/apache/apache/vc.js.template"
    tracker_output = project_dir / "mounts/apache/apache/vc.js"

    if tracker_template.exists() and env_vars.get("BASE_DOMAIN"):
        content = tracker_template.read_text()
        content = content.replace("{{BASE_DOMAIN}}", env_vars["BASE_DOMAIN"])
        tracker_output.write_text(content)
        print(color(f"  ✓ Generated vc.js for {env_vars['BASE_DOMAIN']}", Colors.GREEN))
    elif not tracker_output.exists():
        tracker_output.write_text("// Analytics not configured — set BASE_DOMAIN and re-run install\n")
        print(color("  ⚠ Created placeholder vc.js (BASE_DOMAIN not set)", Colors.YELLOW))


def install_quadlets(
    quadlets_dir: Path,
    systemd_dir: Path,
    services: list[Service],
    render_fn: Callable[[str], str],
    *,
    force: bool = False,
) -> tuple[list[str], list[str], list[str]]:
    """
    Copy (rendered) quadlet files from *quadlets_dir* into *systemd_dir*.

    Only services whose ``.file`` exists in *quadlets_dir* are processed.

    Returns ``(installed, skipped, errors)`` — lists of service file names.
    """
    installed: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    available = [s for s in services if (quadlets_dir / s.file).exists()]

    for svc in available:
        source = quadlets_dir / svc.file
        target = systemd_dir / svc.file

        if not source.exists():
            print(color(f"  ✗ {svc.file}: source not found", Colors.RED))
            errors.append(svc.file)
            continue

        if target.exists() or target.is_symlink():
            if not force:
                print(f"  ○ {svc.file}: already installed")
                skipped.append(svc.file)
                continue
            target.unlink()

        try:
            content = source.read_text()
            rendered = render_fn(content)
            target.write_text(rendered)
            print(color(f"  ✓ {svc.file}: installed", Colors.GREEN))
            installed.append(svc.file)
        except Exception as e:
            print(color(f"  ✗ {svc.file}: {e}", Colors.RED))
            errors.append(svc.file)

    return installed, skipped, errors


def cleanup_disabled_optional_services(
    services: list[Service],
    disabled_optional: dict[str, str],
    systemd_dir: Path,
) -> None:
    """
    Remove quadlet files for optional services that are currently disabled.

    *disabled_optional* maps service-name → controlling env-var name
    (as returned by ``_get_disabled_optional_services()`` in visp.py).
    """
    for svc in services:
        if svc.name not in disabled_optional:
            continue
        target = systemd_dir / svc.file
        if target.exists() or target.is_symlink():
            target.unlink()
            env_var = disabled_optional[svc.name]
            print(color(f"  ○ {svc.file}: removed ({env_var}=false)", Colors.YELLOW))
