"""Containerized npm for dev-mode services whose source tree is bind-mounted.

In dev mode session-manager mounts ``external/session-manager`` over
``/session-manager``, so ``node_modules`` comes from the host tree rather than
from the image. Installing those packages *on the host* would be a trap: the
host toolchain and the image's differ (Node 22/glibc 2.43 vs Node 20/glibc 2.36
at the time of writing), so any dependency with a compiled native addon would
build against the host and fail to load inside the container — glibc is forward
incompatible. Running npm *through the service image* keeps node_modules built
by exactly the runtime that will execute it, and preserves the Dockerfile's
design goal of not requiring Node.js on the host at all.

Ownership works out because session-manager runs with the default rootless
mapping (no ``UserNS=keep-id``), so container root is the host user: files npm
writes land owned by the invoking user, not a sub-UID.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .runner import Colors, Runner, color

# Service name -> (host source dir relative to project root, image, container workdir).
# Only services whose dev quadlet bind-mounts its source tree belong here.
NPM_SERVICES: dict[str, tuple[str, str, str]] = {
    "session-manager": (
        "external/session-manager",
        "localhost/visp-session-manager:latest",
        "/session-manager",
    ),
}


def run_npm(
    runner: Runner,
    project_dir: Path,
    service: str,
    npm_args: list[str],
) -> int:
    """Run ``npm <args>`` inside *service*'s image against its host source tree.

    Returns the exit code (0 on success, non-zero on any failure).
    """
    entry = NPM_SERVICES.get(service)
    if entry is None:
        known = ", ".join(sorted(NPM_SERVICES)) or "(none)"
        print(color(f"'{service}' has no bind-mounted source tree. Known: {known}", Colors.RED))
        return 1

    rel_source, image, workdir = entry
    source = project_dir / rel_source

    if not source.exists():
        print(color(f"Source directory not found: {source}", Colors.RED))
        print("  Run './visp.py deploy update' to fetch external repositories.")
        return 1

    rc, _, _ = runner.run_quiet(["podman", "image", "exists", image])
    if rc != 0:
        print(color(f"Image {image} not found.", Colors.RED))
        print(f"  Run './visp.py build {service}' first.")
        return 1

    if not npm_args:
        npm_args = ["install"]

    print(color(f"npm {' '.join(npm_args)} ({service}, in {image})", Colors.BLUE))

    # Allocate a TTY only when we have one — 'install' may run from a script or CI,
    # and -t without a terminal garbles npm's progress output.
    tty_args = ["-it"] if sys.stdin.isatty() and sys.stdout.isatty() else []

    # --network=none is deliberately NOT set: npm needs the registry.
    result = runner.run(
        [
            "podman",
            "run",
            "--rm",
            *tty_args,
            "-v",
            f"{source}:{workdir}:rw,z",
            "-w",
            workdir,
            image,
            "npm",
            *npm_args,
        ],
        check=False,
    )
    if result.returncode != 0:
        print(color(f"npm exited with code {result.returncode}", Colors.RED))
        return result.returncode

    print(color("  ✓ npm finished", Colors.GREEN))
    print(f"  Run './visp.py restart {service}' to pick up dependency changes.")
    return 0


def ensure_node_modules(runner: Runner, project_dir: Path, service: str) -> bool:
    """Install node_modules into *service*'s host source tree if missing.

    Dev mode bind-mounts the source tree over the image's ``/session-manager``,
    which shadows the node_modules baked into the image — so a fresh clone has
    nothing to run until this populates the host tree.

    Best-effort: returns False (with guidance) rather than raising if the image
    is not built yet, since on a first install 'build' has not run.
    """
    entry = NPM_SERVICES.get(service)
    if entry is None:
        return False

    rel_source, image, _ = entry
    source = project_dir / rel_source

    if not source.exists():
        # 'deploy update' has not fetched the repo yet; install.py reports that separately.
        return False

    if (source / "node_modules").is_dir():
        print(f"  ○ {service}: node_modules already present")
        return True

    rc, _, _ = runner.run_quiet(["podman", "image", "exists", image])
    if rc != 0:
        print(color(f"  ⚠ {service}: node_modules missing and {image} not built yet", Colors.YELLOW))
        print(f"    Run './visp.py build {service}' then './visp.py npm {service} -- ci'")
        return False

    print(color(f"  Installing {service} node_modules (dev mode bind-mounts the source tree)...", Colors.CYAN))
    lockfile = "ci" if (source / "package-lock.json").exists() else "install"
    return run_npm(runner, project_dir, service, [lockfile]) == 0
