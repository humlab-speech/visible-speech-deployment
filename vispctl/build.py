"""BuildManager: encapsulates image and Node.js project builds."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .config import get_config
from .exceptions import BuildError
from .runner import Colors, Runner, color

NODE_BUILD_MARKER = ".build-marker"

# Whitelist of valid Angular build configurations for --config CLI argument.
# Used to prevent command injection when the config is interpolated into build_cmd.
VALID_BUILD_CONFIGS: set[str] = {
    "visp",
    "visp-demo",
    "visp-pdf-server",
    "datalab",
    "visp-local",
    "visp.dev",
    "production",
    "development",
}


def validate_build_config(build_config: str | None) -> str | None:
    """Validate a build configuration name against the whitelist.

    Raises BuildError if the config is not in the allowed set.
    """
    if build_config is None:
        return None
    if build_config not in VALID_BUILD_CONFIGS:
        raise BuildError(
            f"Invalid build config: {build_config!r}. " f"Allowed values: {', '.join(sorted(VALID_BUILD_CONFIGS))}"
        )
    return build_config


class BuildManager:
    def __init__(
        self,
        runner: Runner,
        build_configs: Dict[str, Dict[str, Any]] = None,
        node_configs: Dict[str, Dict[str, Any]] = None,
    ):
        self.runner = runner
        # Default to empty dicts if not supplied
        self.build_configs = build_configs or {}
        self.node_configs = node_configs or {}

    def run_builds(
        self,
        ordered: list[str],
        no_cache: bool = False,
        pull: bool = False,
        build_config: str | None = None,
    ) -> dict[str, list[str]]:
        """Execute the build loop for an ordered list of services.

        Returns a dict with keys "success", "failed", "skipped" containing
        service name lists.
        """
        from .permissions import PermissionsManager

        results: dict[str, list[str]] = {"success": [], "failed": [], "skipped": []}
        node_names = set(self.node_configs.keys())

        for svc_name in ordered:
            if svc_name in node_names:
                # ── Node.js build (containerized) ──────────────────────────
                cfg = self.node_configs[svc_name]
                print(color(f"Building {svc_name} (node)...", Colors.BLUE))
                print(f"  Source: {cfg['source']}")
                print(f"  Output: {cfg['output']}")

                success = self.build_node_project(svc_name, cfg, no_cache, build_config)
                if success:
                    results["success"].append(svc_name)
                    output_path = Path(cfg.get("output"))
                    if output_path.exists():
                        print(color(f"  Fixing permissions on {output_path}...", Colors.YELLOW))
                        pm = PermissionsManager(self.runner)
                        pm.apply_fix([output_path], recursive=True, host_owner=True)
                        print(color("  ✓ Permissions fixed", Colors.GREEN))
                else:
                    results["failed"].append(svc_name)
            else:
                # ── Container image build ──────────────────────────────────
                cfg = self.build_configs[svc_name]
                description = cfg.get("description", "")
                target = cfg.get("target")

                print(color(f"Building {svc_name}...", Colors.BLUE))
                print(f"  Image: {cfg['image']}:latest")
                print(f"  Context: {cfg['context']}")
                if description:
                    print(f"  Description: {description}")
                if target:
                    print(f"  Target: {target}")

                depends_on = cfg.get("depends_on")
                if depends_on and depends_on not in results["success"]:
                    rc, _, _ = self.runner.run_quiet(
                        ["podman", "image", "exists", f"{self.build_configs[depends_on]['image']}:latest"]
                    )
                    if rc != 0:
                        print(color(f"  ✗ Requires {depends_on} image — not built and not present", Colors.RED))
                        results["skipped"].append(svc_name)
                        print()
                        continue

                if cfg.get("prepare_context"):
                    if not self.prepare_build_context(svc_name, cfg):
                        results["failed"].append(svc_name)
                        print()
                        continue

                ok = self.build_image(svc_name, cfg, no_cache=no_cache, pull=pull)
                if ok:
                    results["success"].append(svc_name)
                else:
                    results["failed"].append(svc_name)

            print()

        return results

    def check_version_drift(self, ordered: list, mode: str) -> tuple[list[str], bool]:
        """Check for version drift between repo state and locked versions.

        Returns (warnings, is_blocking).
        - warnings: list of human-readable warning strings
        - is_blocking: True if the build should be aborted (prod mode mismatch)
        """
        from .git_repo import GitRepository
        from .versions import ComponentConfig

        comp_config = ComponentConfig()
        node_names = set(self.node_configs.keys())
        services_to_check = [s for s in ordered if s in node_names and s in dict(comp_config.get_components())]

        warnings: list[str] = []
        is_blocking = False

        for svc_name in services_to_check:
            comp_data = comp_config.get_component(svc_name)
            if not comp_data:
                continue

            version = comp_data.get("version", "latest")
            is_locked = comp_config.is_locked(svc_name)

            repo_path = get_config().project_dir / "external" / svc_name
            if not repo_path.exists():
                warnings.append(f"  ⚠  {svc_name}: Repository not found at {repo_path}")
                continue

            repo = GitRepository(str(repo_path))
            if not repo.is_git_repo():
                continue

            current_commit = repo.get_current_commit()
            if not current_commit:
                continue

            if mode == "prod" and is_locked:
                if current_commit != version:
                    warnings.append(
                        f"  ⚠  {svc_name}: Version mismatch in PROD mode\n"
                        f"      Current: {current_commit[:8]}, Expected: {version[:8]}\n"
                        f"      Run: ./visp.py deploy update"
                    )
                    is_blocking = True
            elif mode == "dev" and not is_locked:
                locked_version = comp_config.get_locked_version(svc_name)
                if locked_version and locked_version != "N/A" and current_commit != locked_version:
                    warnings.append(
                        f"  ℹ  {svc_name}: Differs from locked version (this is OK in dev mode)\n"
                        f"      Current: {current_commit[:8]}, Locked: {locked_version[:8]}"
                    )

        return warnings, is_blocking

    def prepare_build_context(self, name: str, config: dict) -> bool:
        prepare = config.get("prepare_context")
        if not prepare:
            return True

        project_dir = get_config().project_dir
        context_dir = project_dir / config["context"]

        if prepare == "container-agent":
            agent_cfg = self.node_configs.get("container-agent")
            if not agent_cfg:
                print(color("  ✗ container-agent build config missing", Colors.RED))
                return False

            agent_source = project_dir / agent_cfg["source"]
            agent_dest = context_dir / "container-agent"

            if not agent_source.exists():
                print(
                    color(
                        f"  ✗ container-agent source not found at {agent_source}",
                        Colors.RED,
                    )
                )
                return False

            if not (agent_source / "package.json").exists():
                print(color("  ✗ container-agent source missing package.json", Colors.RED))
                return False

            if agent_dest.exists():
                shutil.rmtree(agent_dest)

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

    def build_image(
        self,
        svc_name: str,
        config: Dict[str, Any],
        no_cache: bool = False,
        pull: bool = False,
    ) -> bool:
        context = config["context"]
        dockerfile = config.get("dockerfile", "Dockerfile")
        image = config["image"]
        target = config.get("target")

        cmd = ["podman", "build"]
        if no_cache:
            cmd.append("--no-cache")
        if pull:
            cmd.append("--pull")
        if target:
            cmd.extend(["--target", target])

        # Pass build arguments (e.g. WEBCLIENT_BUILD)
        for key, value in config.get("build_args", {}).items():
            cmd.extend(["--build-arg", f"{key}={value}"])

        # Add git commit label if we're building from a git repo
        context_path = Path(context).resolve()
        # Use source_repo for git.commit label when the build context is not the source
        # (e.g. apache embeds webclient, operations-session embeds container-agent)
        source_repo = config.get("source_repo")
        git_label_path = Path(source_repo).resolve() if source_repo else context_path
        try:
            self._add_git_labels(cmd, git_label_path, "git.commit")

            # Add labels for extra source repos (if multiple repos are embedded in one image)
            for name, repo_path in config.get("extra_source_repos", {}).items():
                extra_path = Path(repo_path).resolve()
                self._add_git_labels(cmd, extra_path, f"git.commit.{name}")

            # When source_repo is set, git.commit tracks the external source but
            # Dockerfile/config changes live in the deployment repo.  Record the
            # deployment repo commit too so deploy status can detect stale images
            # when only the Dockerfile changed.
            if source_repo:
                deploy_path = get_config().project_dir
                deploy_commit = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=deploy_path, capture_output=True, text=True, check=False
                )
                if deploy_commit.returncode == 0:
                    cmd.extend(["--label", f"git.commit.deploy={deploy_commit.stdout.strip()}"])
        except Exception:
            # If git info fails, just continue without labels
            pass

        cmd.extend(["-t", f"{image}:latest"])
        cmd.extend(
            [
                "-f",
                (f"{context}/{dockerfile}" if not dockerfile.startswith("./") else dockerfile),
            ]
        )
        cmd.append(context)

        try:
            print(color(f"Building {svc_name}...", Colors.BLUE))
            res = self.runner.run(cmd, check=False)
            return res.returncode == 0
        except Exception as e:
            print(color(f"✗ {svc_name} build error: {e}", Colors.RED))
            return False

    def _add_git_labels(self, cmd: list[str], path: Path, label_prefix: str) -> None:
        """Add git commit and dirty labels for a path to the build command."""
        git_check = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=path, capture_output=True, check=False)
        if git_check.returncode != 0:
            return
        commit_result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, check=False
        )
        if commit_result.returncode == 0:
            commit_hash = commit_result.stdout.strip()
            cmd.extend(["--label", f"{label_prefix}={commit_hash}"])
            dirty_result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=path,
                capture_output=True,
                text=True,
                check=False,
            )
            if dirty_result.returncode == 0 and dirty_result.stdout.strip():
                dirty_key = label_prefix.replace("git.commit", "git.dirty", 1)
                cmd.extend(["--label", f"{dirty_key}=true"])
            from datetime import datetime

            build_time = datetime.now().isoformat()
            cmd.extend(["--label", f"build.timestamp={build_time}"])

    def build_node_project(
        self,
        name: str,
        config: Dict[str, Any],
        no_cache: bool = False,
        build_config: str = None,
    ) -> bool:
        project_dir = get_config().project_dir
        source_dir = project_dir / config["source"]
        output_dir = project_dir / config["output"]

        build_cmd_template = config.get("build_cmd", "npm run build")
        if "{config}" in build_cmd_template:
            cfg = build_config or config.get("default_config", "production")
            validate_build_config(cfg)
            build_cmd = build_cmd_template.format(config=cfg)
        else:
            if build_config:
                print(
                    color(
                        f"  ⚠ --config {build_config} ignored: {name} does not use build configs (webclient only)",
                        Colors.YELLOW,
                    )
                )
            build_cmd = build_cmd_template

        container_image = config.get("container_image", "node:20-alpine")
        verify_file = config.get("verify_file", "main.js")

        print(color(f"Building {name} (containerized Node.js build)...", Colors.CYAN))
        print(f"  Source: {source_dir}")
        print(f"  Output: {output_dir}")
        print(f"  Build command: {build_cmd}")

        if not source_dir.exists():
            print(color(f"  ✗ Source directory not found: {source_dir}", Colors.RED))
            return False

        # Run optional pre-build step (e.g. composer install for PHP dependencies)
        if not self._run_pre_build(name, config, source_dir):
            return False

        output_dir.mkdir(parents=True, exist_ok=True)

        if no_cache:
            print(color("  Cleaning output directory for fresh build...", Colors.YELLOW))
            for item in output_dir.iterdir():
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)

        # Build command using podman run (mirrors original strategy)
        # Note on chown: inside the container (rootless Podman), UID 0 = host user (tomas),
        # so `chown -R 0:0 /output` correctly gives ownership to the host user.
        # Using os.getuid() (e.g. 1000) inside the container would map to a high UID in the
        # subuid range (~100999), which is wrong.
        # Note on cp: we copy into /output without pre-clearing it to avoid a brief window
        # where Apache would see an empty document root mid-build. Old files from previous
        # builds linger but are harmless (Angular uses content-hashed filenames).
        cmd = [
            "podman",
            "run",
            "--rm",
            "-v",
            f"{source_dir.resolve()}:/src:ro,Z",
            "-v",
            f"{output_dir.resolve()}:/output:Z",
            container_image,
            "sh",
            "-c",
            (
                f"cp -r /src /build && cd /build && npm install --legacy-peer-deps && "
                f"{build_cmd} && chmod -R 777 /output && cp -rf dist/. /output/ && chown -R 0:0 /output"
            ),
        ]

        print(color("  Running containerized build...", Colors.CYAN))

        try:
            res = self.runner.run(cmd, check=False)
            if res.returncode == 0:
                verify_path = output_dir / verify_file
                if verify_path.exists() or any(output_dir.iterdir()):
                    self._write_node_build_marker(source_dir, output_dir)
                    print(color(f"  ✓ {name} built successfully", Colors.GREEN))
                    return True
                print(color(f"  ✗ Build completed but {verify_file} not found", Colors.RED))
                return False
            print(color(f"  ✗ {name} build failed (exit code {res.returncode})", Colors.RED))
            return False
        except Exception as e:
            print(color(f"  ✗ {name} build error: {e}", Colors.RED))
            return False

    def _run_pre_build(
        self,
        name: str,
        config: Dict[str, Any],
        source_dir: Path,
    ) -> bool:
        """Run an optional pre-build step in a separate container.

        Used e.g. for `composer install` before the Angular build, so that
        PHP vendor/ dependencies are present when the asset pipeline copies
        them into dist/.
        """
        pre_build_cmd = config.get("pre_build_cmd")
        if not pre_build_cmd:
            return True

        pre_build_image = config.get("pre_build_image", "docker.io/library/composer:2.9.5")
        print(color(f"  Running pre-build step ({pre_build_image})...", Colors.CYAN))

        cmd = [
            "podman",
            "run",
            "--rm",
            "--userns=keep-id",
            "-v",
            f"{source_dir.resolve()}:/app:rw,Z",
            "-w",
            "/app",
            pre_build_image,
            "sh",
            "-c",
            pre_build_cmd,
        ]

        try:
            res = self.runner.run(cmd, check=False)
            if res.returncode != 0:
                print(color(f"  ✗ {name} pre-build step failed (exit code {res.returncode})", Colors.RED))
                return False
            print(color("  ✓ Pre-build step completed", Colors.GREEN))
            return True
        except Exception as e:
            print(color(f"  ✗ {name} pre-build error: {e}", Colors.RED))
            return False

    @staticmethod
    def _write_node_build_marker(source_dir: Path, output_dir: Path) -> None:
        """Write a .build-marker JSON file into the output directory.

        This records the source git commit so that deploy status can check
        whether the build output matches the current source without relying
        on container image labels (node builds don't produce images).
        """
        marker: dict[str, Any] = {"build_timestamp": datetime.now(timezone.utc).isoformat()}
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=source_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                marker["git_commit"] = result.stdout.strip()
            dirty = subprocess.run(
                ["git", "diff", "--quiet"],
                cwd=source_dir,
                capture_output=True,
                check=False,
            )
            marker["git_dirty"] = dirty.returncode != 0
        except Exception:  # noqa: BLE001
            pass
        try:
            (output_dir / NODE_BUILD_MARKER).write_text(json.dumps(marker, indent=2) + "\n")
        except Exception:  # noqa: BLE001
            pass


def resolve_build_order(
    requested: list[str],
    build_configs: Dict[str, Dict[str, Any]],
    node_configs: Dict[str, Dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Resolve build dependencies and return services in correct build order.

    Returns (ordered_list, auto_added_list).  Node builds come before container
    builds so that artifacts (e.g. container-agent/dist) are ready when images
    that need them are built.  Within each group, dependencies are respected
    via topological sort; ties are broken alphabetically.
    """
    # Collect dependency edges from build configs
    deps: dict[str, list[str]] = {}
    for name, cfg in build_configs.items():
        d = []
        if cfg.get("depends_on"):
            d.append(cfg["depends_on"])
        if cfg.get("prepare_context") and cfg["prepare_context"] in node_configs:
            d.append(cfg["prepare_context"])
        if d:
            deps[name] = d

    # Expand requested set with transitive dependencies
    original = set(requested)
    needed: set[str] = set()

    def _add(name: str) -> None:
        if name in needed:
            return
        needed.add(name)
        for dep in deps.get(name, []):
            _add(dep)

    for name in requested:
        _add(name)

    auto_added = sorted(needed - original)

    # Topological sort with tie-breaking: node builds first, then alphabetical
    node_names = set(node_configs.keys())

    in_deg = {n: 0 for n in needed}
    fwd: dict[str, list[str]] = {n: [] for n in needed}
    for n in needed:
        for dep in deps.get(n, []):
            if dep in needed:
                fwd[dep].append(n)
                in_deg[n] += 1

    ready = [n for n in needed if in_deg[n] == 0]
    result: list[str] = []
    while ready:
        ready.sort(key=lambda n: (0 if n in node_names else 1, n))
        n = ready.pop(0)
        result.append(n)
        for dependent in fwd[n]:
            in_deg[dependent] -= 1
            if in_deg[dependent] == 0:
                ready.append(dependent)

    return result, auto_added


# ---------------------------------------------------------------------------
# Canonical build configuration — these are the single source of truth.
# visp.py imports these rather than defining its own copies.
# ---------------------------------------------------------------------------

# Container image builds: maps service name -> podman build info
BUILD_CONFIGS: dict[str, dict] = {
    "apache": {
        "context": ".",
        "dockerfile": "./docker/apache/Dockerfile",
        "image": "visp-apache",
        "target": "production",
        "build_args": {"WEBCLIENT_BUILD": "visp-build"},
        "source_repo": "./external/webclient",  # git.commit label tracks webclient source
    },
    "session-manager": {
        "context": "./external/session-manager",
        "dockerfile": "Dockerfile",
        "image": "visp-session-manager",
    },
    "artic": {
        "context": "./external/artic",
        "dockerfile": "../../docker/artic/Dockerfile",
        "image": "visp-artic",
        "target": "production",
    },
    "emu-webapp-server": {
        "context": "./external/emu-webapp-server",
        "dockerfile": "docker/Dockerfile",
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
    "whisperx": {
        "context": "./external/WhisperVault",
        "dockerfile": "container/Containerfile",
        "image": "visp-whisperx",
        "description": "WhisperX transcription server (network-isolated, communicates via Unix socket)",
    },
    # Session images — used by session-manager to spawn user sessions
    "jupyter-session": {
        "context": "./docker/session-manager",
        "dockerfile": "jupyter-session/Dockerfile",
        "image": "visp-jupyter-session",
        "description": "Jupyter + R session image (also used for operations tasks)",
        "prepare_context": "container-agent",  # Needs container-agent copied to build context
        "source_repo": "./external/container-agent",  # git.commit label tracks container-agent source
    },
    "session-proxy": {
        "context": "./docker/session-proxy",
        "dockerfile": "Dockerfile",
        "image": "visp-session-proxy",
        "description": "Tinyproxy sidecar for network-isolated session containers",
    },
    "podman-socket-proxy": {
        "context": "./docker/podman-socket-proxy",
        "dockerfile": "Dockerfile",
        "image": "visp-podman-socket-proxy",
        "description": "Body-inspecting Podman socket proxy — enforces image/mount/cap allowlist on container create",
    },
}

# Node.js tool builds: built inside a container (no host npm/node required)
NODE_BUILD_CONFIGS: dict[str, dict] = {
    "container-agent": {
        "source": "./external/container-agent",
        "output": "./external/container-agent/dist",
        "description": "Container management agent (webpack build)",
        "build_cmd": "npm run build",
        "verify_file": "main.js",
    },
    "webclient": {
        "source": "./external/webclient",
        "output": "./external/webclient/dist",
        "description": "Angular webclient (ng build)",
        # Pre-build: install PHP dependencies so angular.json's asset pipeline
        # can copy vendor/ into dist/. Uses --ignore-platform-reqs because the
        # Composer container lacks ext-mongodb (only needed at PHP runtime).
        "pre_build_cmd": "composer install --no-interaction --prefer-dist --no-dev --ignore-platform-reqs",
        "pre_build_image": "docker.io/library/composer:2.9.5",
        # Use npx to invoke the locally installed ng binary.
        # Note: --output-path is NOT passed here; angular.json controls outputPath.
        "build_cmd": "npx ng build --configuration={config}",
        "default_config": "visp.dev",
        "verify_file": "index.php",
        # Angular 20 requires Node ^20.19 || ^22.12 || >=24
        "container_image": "node:22.22.2",
    },
}


def cmd_build(
    args,
    runner=None,
    build_configs=None,
    node_configs=None,
    all_buildable: list[str] | None = None,
) -> None:
    """Build container images and node projects."""

    if getattr(args, "list", False):
        cmd_build_list(args, build_configs=build_configs, node_configs=node_configs)
        return

    no_cache = getattr(args, "no_cache", False)
    pull = getattr(args, "pull", False)
    raw_services = getattr(args, "services", ["all"])
    build_config = getattr(args, "config", None)
    force = getattr(args, "force", False)

    if "all" in raw_services:
        requested = list(all_buildable)
    else:
        unknown = [s for s in raw_services if s not in all_buildable]
        if unknown:
            print(color(f"Error: Unknown service(s): {', '.join(unknown)}", Colors.RED))
            print(f"Buildable services: {', '.join(all_buildable)}")
            return
        requested = list(raw_services)

    ordered, auto_added = resolve_build_order(requested, build_configs, node_configs)

    if auto_added:
        print(color(f"Auto-adding dependencies: {', '.join(auto_added)}", Colors.YELLOW))
        print()

    bm = BuildManager(runner, build_configs=build_configs, node_configs=node_configs)

    if not force:
        from .quadlets import get_current_mode

        mode = get_current_mode()
        version_warnings, is_blocking = bm.check_version_drift(ordered, mode)

        if version_warnings:
            print(color("\n=== Version Check Warnings ===", Colors.CYAN))
            for warning in version_warnings:
                print(warning)
            print()
            if is_blocking:
                print(color("Cannot build in PROD mode with version mismatches.", Colors.RED))
                print("   Options:")
                print("   1. Run: ./visp.py deploy update")
                print("   2. Use --force to override (not recommended)")
                print()
                return
            print(color("Continuing build (use --force to skip this check)...", Colors.YELLOW))
            print()

    print(color("=== Building VISP Services ===", Colors.CYAN))
    print(f"  Order: {' -> '.join(ordered)}")
    print()

    if no_cache:
        print(color("Building with --no-cache (clean rebuild)", Colors.YELLOW))
    if pull:
        print(color("Building with --pull (fetch latest base images)", Colors.YELLOW))
    if no_cache or pull:
        print()

    results = bm.run_builds(ordered, no_cache=no_cache, pull=pull, build_config=build_config)

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


def cmd_build_list(args, build_configs=None, node_configs=None) -> None:  # noqa: ARG001
    """List buildable services."""

    print(color("=== Buildable Container Images ===", Colors.CYAN))
    print()
    for name, config in (build_configs or {}).items():
        print(f"  {color(name, Colors.BLUE)}")
        print(f"    Image: localhost/{config['image']}:latest")
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
    for name, config in (node_configs or {}).items():
        print(f"  {color(name, Colors.BLUE)}")
        print(f"    Source: {config['source']}")
        print(f"    Output: {config['output']}")
        print(f"    Description: {config['description']}")
        if config.get("default_config"):
            print(f"    Default config: {config['default_config']}")
            print(f"    Available configs: {', '.join(sorted(VALID_BUILD_CONFIGS))}")
        print()
