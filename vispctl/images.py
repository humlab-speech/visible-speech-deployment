"""ImageManager: handles container image inspection and auditing."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .runner import Colors, Runner, color
from .service import Service


class ImageManager:
    def __init__(
        self,
        runner: Runner,
        build_configs: Dict[str, Dict[str, Any]] = None,
        network_services: List[Service] = None,
    ):
        self.runner = runner
        self.build_configs = build_configs or {}
        self.network_services = network_services or []

    def get_visp_images(self) -> Tuple[Dict[str, Dict], Dict[str, str]]:
        """Get list of VISP images and their status.

        Returns:
            Tuple of (found_images, expected_images)
            - found_images: dict mapping image_name -> {tag, size, created, full_repo}
            - expected_images: dict mapping image_name -> build_name
        """
        expected_images = {config["image"]: name for name, config in self.build_configs.items()}

        rc, stdout, _ = self.runner.run_quiet(
            [
                "podman",
                "images",
                "--format",
                "{{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.Created}}",
            ]
        )

        found_images = {}
        if rc == 0 and stdout:
            for line in stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) >= 4:
                    repo, tag, size, created = parts[0], parts[1], parts[2], parts[3]
                    # Extract image name from full path
                    image_name = repo.split("/")[-1]
                    if image_name.startswith("visp-"):
                        found_images[image_name] = {
                            "tag": tag,
                            "size": size,
                            "created": created,
                            "full_repo": repo,
                        }

        return found_images, expected_images

    def get_network_backend(self) -> Tuple[str, bool]:
        """Get the current network backend.

        Returns:
            Tuple of (backend_name, is_netavark)
        """
        rc, stdout, _ = self.runner.run_quiet(["podman", "info", "--format", "{{.Host.NetworkBackend}}"])
        backend = stdout.strip() if rc == 0 else "unknown"
        return backend, backend == "netavark"

    def get_networks(self) -> Dict[str, bool]:
        """Get status of VISP networks.

        Returns:
            Dict mapping network_name -> exists (bool)
        """
        networks = {}
        for svc in self.network_services:
            net_name = f"systemd-{svc.name}"
            rc, _, _ = self.runner.run_quiet(["podman", "network", "exists", net_name])
            networks[net_name] = rc == 0
        return networks

    def get_container_networks(self) -> Dict[str, str]:
        """Get network connections for running containers.

        Returns:
            Dict mapping container_name -> network_ids
        """
        container_networks = {}
        rc, stdout, _ = self.runner.run_quiet(["podman", "ps", "--format", "{{.Names}}"])
        if rc == 0 and stdout:
            for container in stdout.split("\n"):
                if container.startswith("systemd-"):
                    rc, nets, _ = self.runner.run_quiet(
                        [
                            "podman",
                            "inspect",
                            container,
                            "--format",
                            "{{range .NetworkSettings.Networks}}{{.NetworkID}} {{end}}",
                        ]
                    )
                    container_networks[container] = nets.strip() if nets else "none"
        return container_networks

    def scan_base_images(self) -> Dict[str, List[str]]:
        """Scan all Dockerfiles and extract base images.

        Returns:
            Dict mapping "image:tag" -> [list of Dockerfile paths]
        """
        base_dir = Path(__file__).parent.parent
        dockerfiles = []

        # Search in docker/ and external/
        for pattern in ["docker/**/Dockerfile*", "external/**/Dockerfile*"]:
            dockerfiles.extend(base_dir.glob(pattern))

        # Exclude ARCHIVE and node_modules
        dockerfiles = [f for f in dockerfiles if "ARCHIVE" not in str(f) and "node_modules" not in str(f)]

        # Parse base images from FROM statements
        base_images = defaultdict(list)

        for dockerfile in sorted(dockerfiles):
            try:
                with open(dockerfile, "r") as f:
                    relative_path = dockerfile.relative_to(base_dir)
                    for line in f:
                        line = line.strip()
                        if line.startswith("FROM"):
                            # Skip multi-stage internal references
                            if " AS " in line and not line.split()[1].startswith(
                                ("docker.io/", "quay.io/", "ghcr.io/")
                            ):
                                image_part = line.split()[1]
                                if ":" not in image_part and "/" not in image_part:
                                    continue

                            # Extract image (skip "FROM" and optional "AS stagename")
                            parts = line.split()
                            if len(parts) >= 2:
                                image = parts[1]

                                # Skip internal stage references
                                if image in [
                                    "base",
                                    "builder",
                                    "dependencies",
                                    "r_packages",
                                    "python_packages",
                                    "final",
                                    "development",
                                    "production",
                                    "visp-operations-session",
                                    "container_agent_source",
                                    "container_agent_builder",
                                ]:
                                    continue

                                # Parse image name and tag
                                if "@sha256:" in image:
                                    # Digest format
                                    name, digest = image.split("@")
                                    tag = f"@{digest[:20]}..."
                                elif ":" in image:
                                    name, tag = image.rsplit(":", 1)
                                else:
                                    name = image
                                    tag = "latest"

                                # Normalize registry prefixes
                                name = name.replace("docker.io/library/", "").replace("docker.io/", "")

                                base_images[f"{name}:{tag}"].append(str(relative_path))
            except Exception as e:
                print(color(f"Warning: Failed to parse {relative_path}: {e}", Colors.YELLOW))

        return dict(base_images)

    def display_visp_images(self) -> None:
        """Display VISP container images and their status."""
        print(color("=== VISP Container Images ===", Colors.CYAN))
        print()

        found_images, expected_images = self.get_visp_images()

        # Print status for each expected image
        for image_name, build_name in sorted(expected_images.items()):
            if image_name in found_images:
                info = found_images[image_name]
                print(
                    f"  {color('✓', Colors.GREEN)} {color(build_name, Colors.BLUE):25} " f"{image_name}:{info['tag']}"
                )
                print(f"      Size: {info['size']:12}  Created: {info['created']}")
            else:
                print(f"  {color('✗', Colors.RED)} {color(build_name, Colors.BLUE):25} " f"{image_name} (not built)")
            print()

        # Summary
        built = sum(1 for img in expected_images if img in found_images)
        total = len(expected_images)

        if built == total:
            print(color(f"All {total} images are built.", Colors.GREEN))
        else:
            print(
                color(
                    f"{built}/{total} images built. Missing images can be built with:",
                    Colors.YELLOW,
                )
            )
            print("  ./visp.py build all")
        print()

    def display_network_info(self) -> None:
        """Display network backend and VISP networks."""
        backend, is_netavark = self.get_network_backend()
        if is_netavark:
            print(color(f"  Backend: {backend} (recommended)", Colors.GREEN))
        else:
            print(
                color(
                    f"  Backend: {backend} (CNI - consider upgrading to netavark)",
                    Colors.YELLOW,
                )
            )
        print()

        print(color("=== VISP Networks ===", Colors.CYAN))
        networks = self.get_networks()
        for net_name, exists in networks.items():
            if exists:
                print(color(f"\n  {net_name}:", Colors.GREEN))
                self.runner.run(
                    [
                        "podman",
                        "network",
                        "inspect",
                        net_name,
                        "--format",
                        "    DNS: {{.DNSEnabled}}\n    Internal: {{.Internal}}\n    Driver: {{.Driver}}",
                    ],
                    check=False,
                )
            else:
                print(color(f"\n  {net_name}: not found", Colors.RED))

        print()
        print(color("=== Container Network Connections ===", Colors.CYAN))
        container_networks = self.get_container_networks()
        for container, nets in container_networks.items():
            print(f"  {container}: {nets}")

    def display_base_images(self) -> None:
        """Display base images from Dockerfiles with pinning status."""
        print(color("=== Base Images in Dockerfiles ===", Colors.CYAN))
        print()

        base_images = self.scan_base_images()

        if not base_images:
            print(color("No base images found", Colors.YELLOW))
            return

        # Sort and display results
        for image in sorted(base_images.keys()):
            files = base_images[image]

            # Parse image for display
            if ":" in image:
                name, tag = image.rsplit(":", 1)
            else:
                name, tag = image, "latest"

            # Color code based on tag type
            if tag == "latest" or not any(char.isdigit() for char in tag):
                tag_colored = color(tag, Colors.RED)  # Unpinned
                status = "⚠️ "
            elif "@sha256" in tag:
                tag_colored = color(tag, Colors.GREEN)  # Digest
                status = "✓ "
            else:
                tag_colored = color(tag, Colors.GREEN)  # Versioned
                status = "✓ "

            print(f"{status} {color(name, Colors.BLUE)}:{tag_colored}")

            # Show which files use this image
            for f in files:
                print(f"     └─ {f}")
            print()

        # Summary
        total = len(base_images)
        unpinned = sum(
            1
            for img in base_images.keys()
            if img.endswith(":latest") or not any(char.isdigit() for char in img.split(":")[-1])
        )

        print(color("=== Summary ===", Colors.CYAN))
        print(f"Total base images: {total}")
        if unpinned > 0:
            print(color(f"⚠️  Unpinned images: {unpinned}", Colors.YELLOW))
            print(
                color(
                    "   Consider pinning to specific versions for reproducibility",
                    Colors.YELLOW,
                )
            )
        else:
            print(color("✓ All images are pinned to specific versions", Colors.GREEN))
