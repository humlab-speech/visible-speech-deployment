"""BuildManager: encapsulates image and Node.js project builds."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Dict, Any

from .runner import Runner, color, Colors


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
                print(
                    color("  ✗ container-agent source missing package.json", Colors.RED)
                )
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
            print(
                color(
                    "  ✓ Copied container-agent source to build context", Colors.GREEN
                )
            )
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

        cmd.extend(["-t", f"{image}:latest"])
        cmd.extend(
            [
                "-f",
                (
                    f"{context}/{dockerfile}"
                    if not dockerfile.startswith("./")
                    else dockerfile
                ),
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

        output_dir.mkdir(parents=True, exist_ok=True)

        if no_cache:
            print(
                color("  Cleaning output directory for fresh build...", Colors.YELLOW)
            )
            for item in output_dir.iterdir():
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)

        uid = os.getuid()
        gid = os.getgid()

        # Build command using podman run (mirrors original strategy)
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
                f"{build_cmd} && cp -r dist/* /output/ && chown -R {uid}:{gid} /output"
            ),
        ]

        print(color("  Running containerized build...", Colors.CYAN))

        try:
            res = self.runner.run(cmd, check=False)
            if res.returncode == 0:
                verify_path = output_dir / verify_file
                if verify_path.exists() or any(output_dir.iterdir()):
                    print(color(f"  ✓ {name} built successfully", Colors.GREEN))
                    return True
                print(
                    color(
                        f"  ✗ Build completed but {verify_file} not found", Colors.RED
                    )
                )
                return False
            print(
                color(
                    f"  ✗ {name} build failed (exit code {res.returncode})", Colors.RED
                )
            )
            return False
        except Exception as e:
            print(color(f"  ✗ {name} build error: {e}", Colors.RED))
            return False
