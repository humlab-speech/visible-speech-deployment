"""
vispctl/install.py — Installation helpers extracted from cmd_install.

Each function is a self-contained phase of the install process, accepting
all dependencies as explicit parameters so they can be unit-tested without
a running system.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from vispctl.runner import Colors, color
from vispctl.service import Service

if TYPE_CHECKING:
    from vispctl.runner import Runner


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


MONGO_CONTAINER_UID = 999
MONGO_CONTAINER_GID = 999
MONGO_MOUNT_MODE = "u+rwX,go-rwx"


def fix_mongo_mount_ownership(project_dir: Path) -> int:
    """
    Ensure Mongo bind-mount paths are owned by Mongo's runtime UID/GID
    inside the rootless Podman user namespace.

    Uses ``podman unshare chown -R 999:999`` so Mongo's in-container
    ``mongodb`` user is mapped to the correct subordinate host UID/GID.
    Then removes group/other access, so old ``0777`` workarounds do not
    linger after install.

    Returns the number of mount roots successfully normalized.
    """
    mongo_mounts = [
        project_dir / "mounts/mongo/data",
        project_dir / "mounts/mongo/logs",
    ]
    fixed = 0

    for mount_path in mongo_mounts:
        if not mount_path.exists():
            continue

        owner = f"{MONGO_CONTAINER_UID}:{MONGO_CONTAINER_GID}"
        chown_result = subprocess.run(
            ["podman", "unshare", "chown", "-R", owner, str(mount_path)],
            capture_output=True,
            text=True,
        )
        if chown_result.returncode != 0:
            print(
                color(
                    f"  ⚠ Failed to normalize Mongo ownership on {mount_path}: {chown_result.stderr.strip()}",
                    Colors.YELLOW,
                )
            )
            continue

        chmod_result = subprocess.run(
            ["podman", "unshare", "chmod", "-R", MONGO_MOUNT_MODE, str(mount_path)],
            capture_output=True,
            text=True,
        )
        if chmod_result.returncode != 0:
            print(
                color(
                    f"  ⚠ Failed to tighten Mongo permissions on {mount_path}: {chmod_result.stderr.strip()}",
                    Colors.YELLOW,
                )
            )
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
    (as returned by ``get_disabled_optional_services()`` in vispctl.service).
    """
    for svc in services:
        if svc.name not in disabled_optional:
            continue
        target = systemd_dir / svc.file
        if target.exists() or target.is_symlink():
            target.unlink()
            env_var = disabled_optional[svc.name]
            print(color(f"  ○ {svc.file}: removed ({env_var}=false)", Colors.YELLOW))


def _ensure_webclient_dist(project_dir: Path, runner: Runner) -> None:
    """
    Build the webclient dist directory if runtime-critical files are missing.

    Only called in dev mode — prod bakes the build into the Apache image.
    Uses the same containerized Node/Composer build path as ``visp.py build
    webclient`` so the PHP vendor dependencies are present in ``dist/vendor``.
    """
    webclient_dir = project_dir / "external" / "webclient"
    dist_dir = webclient_dir / "dist"
    required_files = [
        dist_dir / "index.php",
        dist_dir / "vendor" / "autoload.php",
    ]

    if not webclient_dir.exists():
        # External repos not present yet — skip silently (phase 13 handles this).
        return

    if all(path.exists() for path in required_files):
        # dist already has the assets Apache/PHP needs.
        return

    if dist_dir.exists() and any(dist_dir.iterdir()):
        missing = [str(path.relative_to(dist_dir)) for path in required_files if not path.exists()]
        print(color("Webclient dist is incomplete; rebuilding missing runtime files:", Colors.YELLOW))
        for path in missing:
            print(f"  - {path}")

    print(color("Building webclient dist (dev mode)...", Colors.CYAN))

    from .build import NODE_BUILD_CONFIGS, BuildManager

    config = dict(NODE_BUILD_CONFIGS["webclient"])
    config["source"] = str(webclient_dir)
    config["output"] = str(dist_dir)

    bm = BuildManager(runner, build_configs={}, node_configs={"webclient": config})
    if not bm.build_node_project("webclient", config, build_config="visp.dev"):
        print(color("  ✗ Webclient dist not built.", Colors.RED))
        print("  Fix the errors above, then run:")
        print("    ./visp.py build webclient --config visp.dev")
        print()
        return

    missing_after_build = [str(path.relative_to(dist_dir)) for path in required_files if not path.exists()]
    if missing_after_build:
        print(color("  ✗ Webclient build finished, but required files are still missing:", Colors.RED))
        for path in missing_after_build:
            print(f"    - {path}")
        print()
        return

    print(color("  ✓ Webclient dist built successfully.", Colors.GREEN))
    print()


def run_install(
    project_dir: Path,
    systemd_dir: Path,
    runner: Runner,
    mode: str,
    service_arg: str,
    services: list[Service],
    all_services: list[Service],
    disabled_optional: dict[str, str],
    render_fn: Callable[[str], str],
    force: bool = False,
) -> None:
    """Orchestrate the full install flow.

    This is the logic previously in ``cmd_install`` in visp.py.  It is
    extracted here so it can be called from a thin CLI wrapper and
    (eventually) tested without a running system.

    Phases (in order):
      1. First-time env-file generation
      2. Netavark backend check / migration
      3. Podman network creation
      4. Podman secret creation
      5. Mount-directory scaffolding
      6. Container-writable permissions + Mongo mount ownership
      7. Tracker config (vc.js)
      8. Dev certs + local IdP files (dev mode only)
      9. Service-specific .env files
      10. Quadlet installation
      11. Cleanup stale disabled-service quadlets
      12. Save mode, print next steps
      13. Check for missing external repos and offer to fetch
      14. Build webclient dist (dev mode only, containerized Node/Composer)
    """
    import sys

    from .network import NetworkManager

    # --- Phase 1: first-time env-file generation ---
    env_file_path = project_dir / ".env"
    secrets_file_path = project_dir / ".env.secrets"

    if not env_file_path.exists() or not secrets_file_path.exists():
        print(color("\n=== First-Time Setup: Generating Environment Files ===", Colors.CYAN))
        print()
        print("This will create .env and .env.secrets with auto-generated passwords.")
        print("You can modify these files later if needed.")
        print()

        from .passwords import setup_env_file

        try:
            setup_env_file(auto_passwords=True, interactive=False)
            print()
        except (OSError, ValueError, RuntimeError) as e:
            print(color(f"❌ Error setting up environment files: {e}", Colors.RED))
            print("Please check the error and try again.")
            sys.exit(1)

    # --- Phase 2: netavark backend check / migration ---
    nm = NetworkManager(runner)
    is_netavark, current_backend = nm.check_netavark()

    if not is_netavark:
        print()
        print(color(f"Current network backend: {current_backend}", Colors.YELLOW))
        print()

        if current_backend == "cni":
            if nm.prompt_netavark_migration():
                if not nm.migrate_to_netavark():
                    print(color("Migration failed. Please fix the errors and try again.", Colors.RED))
                    sys.exit(1)
                print()
                print(color("✓ Migration complete!", Colors.GREEN))
                print()
            else:
                sys.exit(1)
        else:
            print(color("Netavark is required for proper DNS resolution.", Colors.YELLOW))
            response = input("Configure netavark now? (yes/no): ").strip().lower()
            if response in ["yes", "y"]:
                if not nm.configure_netavark():
                    sys.exit(1)
                print()
                print(color("✓ Netavark configured. Please restart Podman services.", Colors.GREEN))
                print("  Run: podman system reset")
                print()
            else:
                print("Installation cancelled.")
                sys.exit(1)

    # --- Phase 3: Podman network creation ---
    print()
    if not nm.ensure_networks_exist():
        print(color("Failed to create networks. Please check the errors above.", Colors.RED))
        sys.exit(1)
    print()

    systemd_dir.mkdir(parents=True, exist_ok=True)

    from .quadlets import get_quadlets_dir, setup_service_env_files

    quadlets_dir = get_quadlets_dir(mode)

    print(color(f"Installing quadlets for {mode} mode", Colors.CYAN))
    print(f"  Source: {quadlets_dir}")
    print(f"  Target: {systemd_dir}")
    print()

    # --- Phase 4: Podman secrets ---
    from .secrets import SecretManager

    sm = SecretManager(runner)
    env_vars = sm.load_all()

    print(color("Creating Podman secrets...", Colors.CYAN))
    sm.create_secrets(sm.get_derived(env_vars))
    print()

    # --- Phase 5: mount-directory scaffolding ---
    print(color("Creating mount directories...", Colors.CYAN))
    created = scaffold_directories(project_dir, quadlets_dir, render_fn)
    if created:
        print(f"  Created {created} missing mount directories")
    else:
        print("  All mount directories already exist")
    print()

    # --- Phase 6: container-writable permissions + Mongo mount ownership/mode ---
    print(color("Fixing container-writable directory permissions...", Colors.CYAN))
    perm_fixed = fix_writable_permissions(project_dir)
    if perm_fixed:
        print(f"  Fixed permissions on {perm_fixed} directories (set to 777)")
    else:
        print("  All container-writable directories already have correct permissions")

    mongo_fixed = fix_mongo_mount_ownership(project_dir)
    if mongo_fixed:
        print(f"  Normalized Mongo mount ownership and permissions on {mongo_fixed} paths")
    else:
        print("  Mongo mount ownership/permissions already normalized (or paths missing)")
    print()

    # --- Phase 7: tracker config (vc.js) ---
    generate_tracker_config(project_dir, env_vars)
    print()

    # --- Phase 8: dev certs + local IdP files (dev only) ---
    if mode == "dev":
        from .certs import ensure_certs, setup_local_idp_files

        base_domain = env_vars.get("BASE_DOMAIN", "").strip()
        if base_domain:
            ensure_certs(project_dir, base_domain)
        setup_local_idp_files(project_dir, env_vars)
        print()

    # --- Phase 9: service-specific .env files ---
    # Sensitive values are injected via Podman Secrets (Secret= lines in quadlets),
    # so we only copy templates with non-secret defaults here.
    setup_service_env_files(project_dir)
    print()

    # --- Phase 10: quadlet installation ---
    installed, skipped, errors = install_quadlets(
        quadlets_dir,
        systemd_dir,
        services,
        render_fn,
        force=force,
    )

    if not installed and not skipped and not errors:
        print(color(f"No quadlet files found in {quadlets_dir}", Colors.RED))
        return

    # --- Phase 11: cleanup stale disabled-service quadlets ---
    if service_arg == "all":
        cleanup_disabled_optional_services(all_services, disabled_optional, systemd_dir)

    # --- Phase 12: save mode, print next steps ---
    from .quadlets import set_current_mode

    set_current_mode(mode)

    print()
    print(f"Mode set to: {color(mode, Colors.MAGENTA)}")
    print("Run './visp.py reload' to apply changes.")

    # --- Phase 13: check for missing external repos ---
    from .versions import DEFAULT_VERSIONS_CONFIG

    external_dir = project_dir / "external"
    missing_repos = [name for name in DEFAULT_VERSIONS_CONFIG if not (external_dir / name / ".git").exists()]
    if missing_repos:
        print()
        print(color("⚠  External repositories are missing:", Colors.YELLOW))
        for name in missing_repos:
            print(f"     • {name}")
        print()
        response = input("  Fetch them now with 'deploy update'? (yes/no) [yes]: ").strip().lower()
        if response in ("", "yes", "y"):
            from .deploy import DeployManager

            dm = DeployManager(runner=runner)
            if not dm.update_components(force=False):
                print(
                    color(
                        "  ✗ deploy update failed — fix errors above and re-run './visp.py deploy update'", Colors.RED
                    )
                )
            else:
                print()
                print(color("  ✓ External repositories ready.", Colors.GREEN))
                # Phase 9 ran before these repos existed, so any service env
                # files that depend on external/*/.env-example templates were
                # skipped. Now that the repos are present, create them.
                print()
                setup_service_env_files(project_dir)
        else:
            print()
            print(color("  Remember to run './visp.py deploy update' before building images.", Colors.YELLOW))

    # --- Phase 14: build webclient dist for dev mode ---
    if mode == "dev":
        _ensure_webclient_dist(project_dir, runner)
