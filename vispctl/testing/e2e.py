"""Shared runner for browser tests, run in the official Playwright container.

A browser test is a Node script in ``tests/e2e/`` that drives a headless
Chromium against the running installation. Tests log in through the local IdP,
so they only run in dev mode.

The container runs with host networking so ``https://<BASE_DOMAIN>`` resolves
and routes exactly as it does for a browser on this machine. Its npm packages
are installed into ``tests/e2e/node_modules`` on first use, from inside the
container, so no Node.js is needed on the host. The playwright npm version is
pinned in ``tests/e2e/package.json`` and must match the image tag below.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..env import load_env_file
from ..runner import Colors, Runner, color

PLAYWRIGHT_IMAGE = "mcr.microsoft.com/playwright:v1.63.0-noble"

E2E_DIR = "tests/e2e"


def read_base_domain(project_dir: Path) -> str:
    """BASE_DOMAIN from .env, or "" if it isn't set."""
    return (load_env_file(project_dir / ".env").get("BASE_DOMAIN") or "").strip()


def build_e2e_command(project_dir: Path, script: str, env: dict[str, str]) -> list[str]:
    """The podman command that runs one browser test script from tests/e2e/."""
    e2e_dir = project_dir / E2E_DIR
    repositories = project_dir / "mounts" / "repositories"
    env = {
        "VISP_REPOSITORIES_DIR": "/repositories",
        "VISP_ARTIFACTS_DIR": "/e2e/artifacts",
        **env,
    }
    env_args = [arg for key, value in env.items() for arg in ("-e", f"{key}={value}")]
    return [
        "podman",
        "run",
        "--rm",
        "--network=host",
        # Chromium needs more shared memory than the 64 MB default.
        "--shm-size=1g",
        # Read another tree without relabelling it: repositories is shared with
        # the running services, so a :z/:Z relabel here would be wrong.
        "--security-opt",
        "label=disable",
        "-v",
        f"{e2e_dir}:/e2e:rw",
        "-v",
        f"{repositories}:/repositories:ro",
        "-w",
        "/e2e",
        *env_args,
        PLAYWRIGHT_IMAGE,
        "sh",
        "-c",
        f"npm ci --no-audit --no-fund --loglevel=error && node {script}",
    ]


def run_e2e(
    runner: Runner,
    project_dir: Path,
    mode: str,
    name: str,
    script: str,
    env: dict[str, str],
) -> int:
    """Run one browser test. Returns its exit code."""
    if mode != "dev":
        print(color("Browser tests log in through the local IdP, so they only run in dev mode.", Colors.RED))
        return 1

    print(color(f"Running the {name} test against {env.get('VISP_URL', '?')}", Colors.BLUE))
    result = runner.run(build_e2e_command(project_dir, script, env), check=False)
    if result.returncode != 0:
        print(color(f"✗ {name} test failed", Colors.RED), file=sys.stderr)
        return result.returncode
    print(color(f"✓ {name} test passed", Colors.GREEN))
    return 0
