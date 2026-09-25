"""End-to-end smoke tests, run in the official Playwright container.

``tests/e2e/recording-smoke.mjs`` drives a headless Chromium (with a fake
microphone) through the online recording pipeline: log in, create a recording
session, record it, and check the recordings reach the project's EMU-DB. It
needs the local IdP to log in, so it only runs in dev mode.

The container runs with host networking so ``https://<BASE_DOMAIN>`` resolves
and routes exactly as it does for a browser on this machine. Its npm packages
are installed into ``tests/e2e/node_modules`` on first use, from inside the
container, so no Node.js is needed on the host. The playwright npm version is
pinned in ``tests/e2e/package.json`` and must match the image tag below.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .runner import Colors, Runner, color

PLAYWRIGHT_IMAGE = "mcr.microsoft.com/playwright:v1.63.0-noble"

E2E_DIR = "tests/e2e"
SMOKE_TESTS = {
    "recording": "recording-smoke.mjs",
}


def build_smoketest_command(
    project_dir: Path,
    test: str,
    base_domain: str,
    project: str,
    keep: bool,
) -> list[str]:
    """The podman command that runs one smoke test."""
    e2e_dir = project_dir / E2E_DIR
    repositories = project_dir / "mounts" / "repositories"
    env = {
        "VISP_URL": f"https://{base_domain}",
        "VISP_PROJECT": project,
        "VISP_KEEP": "1" if keep else "0",
        "VISP_REPOSITORIES_DIR": "/repositories",
        "VISP_ARTIFACTS_DIR": "/e2e/artifacts",
    }
    env_args = [arg for key, value in env.items() for arg in ("-e", f"{key}={value}")]
    script = f"npm ci --no-audit --no-fund --loglevel=error && node {SMOKE_TESTS[test]}"
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
        script,
    ]


def run_smoketest(
    runner: Runner,
    project_dir: Path,
    mode: str,
    base_domain: str,
    test: str = "recording",
    project: str = "Test 3",
    keep: bool = False,
) -> int:
    """Run a smoke test. Returns its exit code."""
    if mode != "dev":
        print(color("Smoke tests log in through the local IdP, so they only run in dev mode.", Colors.RED))
        return 1
    if test not in SMOKE_TESTS:
        print(color(f"Unknown smoke test '{test}'. Known: {', '.join(SMOKE_TESTS)}", Colors.RED))
        return 1

    print(color(f"Running the {test} smoke test against https://{base_domain} (project '{project}')", Colors.BLUE))
    cmd = build_smoketest_command(project_dir, test, base_domain, project, keep)
    result = runner.run(cmd, check=False)
    if result.returncode != 0:
        print(color(f"✗ {test} smoke test failed", Colors.RED), file=sys.stderr)
        return result.returncode
    print(color(f"✓ {test} smoke test passed", Colors.GREEN))
    return 0
