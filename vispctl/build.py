"""BuildManager: encapsulates image and Node.js project builds."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .runner import Colors, Runner, color

NODE_BUILD_MARKER = ".build-marker"


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

    def prepare_build_context(self, name: str, config: Dict[str, Any]) -> bool:
        prepare = config.get("prepare_context")
        if not prepare:
            return True

        context_dir = Path(__file__).parent.parent / config["context"]

        if prepare == "container-agent":
            agent_cfg = self.node_configs.get("container-agent")
            if not agent_cfg:
                print(color("  ✗ container-agent build config missing", Colors.RED))
                return False

            agent_source = Path(__file__).parent.parent / agent_cfg["source"]
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
        import subprocess
        from pathlib import Path

        context_path = Path(context).resolve()
        # Use source_repo for git.commit label when the build context is not the source
        # (e.g. apache embeds webclient, operations-session embeds container-agent)
        source_repo = config.get("source_repo")
        git_label_path = Path(source_repo).resolve() if source_repo else context_path
        try:
            # Check if context is in a git repo
            git_check = subprocess.run(
                ["git", "rev-parse", "--git-dir"], cwd=git_label_path, capture_output=True, check=False
            )
            if git_check.returncode == 0:
                # Get current commit hash
                commit_result = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=git_label_path, capture_output=True, text=True, check=False
                )
                if commit_result.returncode == 0:
                    commit_hash = commit_result.stdout.strip()
                    cmd.extend(["--label", f"git.commit={commit_hash}"])

                    # Check if the source tree was dirty at build time
                    dirty_result = subprocess.run(
                        ["git", "status", "--porcelain"],
                        cwd=git_label_path,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if dirty_result.returncode == 0 and dirty_result.stdout.strip():
                        cmd.extend(["--label", "git.dirty=true"])

                    # Also add timestamp
                    from datetime import datetime

                    build_time = datetime.now().isoformat()
                    cmd.extend(["--label", f"build.timestamp={build_time}"])

            # Add labels for extra source repos (if multiple repos are embedded in one image)
            for name, repo_path in config.get("extra_source_repos", {}).items():
                extra_path = Path(repo_path).resolve()
                extra_check = subprocess.run(
                    ["git", "rev-parse", "--git-dir"], cwd=extra_path, capture_output=True, check=False
                )
                if extra_check.returncode == 0:
                    extra_commit = subprocess.run(
                        ["git", "rev-parse", "HEAD"], cwd=extra_path, capture_output=True, text=True, check=False
                    )
                    if extra_commit.returncode == 0:
                        cmd.extend(["--label", f"git.commit.{name}={extra_commit.stdout.strip()}"])
                    extra_dirty = subprocess.run(
                        ["git", "status", "--porcelain"],
                        cwd=extra_path,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if extra_dirty.returncode == 0 and extra_dirty.stdout.strip():
                        cmd.extend(["--label", f"git.dirty.{name}=true"])

            # When source_repo is set, git.commit tracks the external source but
            # Dockerfile/config changes live in the deployment repo.  Record the
            # deployment repo commit too so deploy status can detect stale images
            # when only the Dockerfile changed.
            if source_repo:
                deploy_path = Path(__file__).parent.parent.resolve()
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

    def build_node_project(
        self,
        name: str,
        config: Dict[str, Any],
        no_cache: bool = False,
        build_config: str = None,
    ) -> bool:
        source_dir = Path(__file__).parent.parent / config["source"]
        output_dir = Path(__file__).parent.parent / config["output"]

        build_cmd_template = config.get("build_cmd", "npm run build")
        if "{config}" in build_cmd_template:
            cfg = build_config or config.get("default_config", "production")
            build_cmd = build_cmd_template.format(config=cfg)
        else:
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

        pre_build_image = config.get("pre_build_image", "docker.io/library/composer:2")
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
