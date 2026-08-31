"""ImageManager: handles container image inspection and auditing."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from .config import get_config
from .runner import Colors, Runner, color
from .service import Service, container_service_names


def _classify_tag(tag: str) -> str:
    """Classify an image tag as 'digest', 'pinned', or 'unpinned'.

    Pinned means the reference cannot move: a digest, a dotted version
    (2.4.67, 3.23), or a date-like run of 4+ digits (trixie-20260406).
    Bare major tags (alpine:3, node:24) and word tags (bookworm, stable)
    can still move, so they count as unpinned.
    """
    if tag.startswith("@"):
        return "digest"
    if tag == "latest" or not any(c.isdigit() for c in tag):
        return "unpinned"
    if "." in tag or re.search(r"\d{4,}", tag):
        return "pinned"
    return "unpinned"


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
        """Get network connections for running VISP containers.

        Intersects the running containers with the known VISP container service
        names (quadlet containers are named ``<name>`` — no ``systemd-`` prefix)
        and records the network IDs each is connected to.

        Returns:
            Dict mapping container_name -> network_ids
        """
        visp_containers = container_service_names()
        rc, stdout, _ = self.runner.run_quiet(["podman", "ps", "--format", "{{.Names}}"])
        if rc != 0 or not stdout:
            return {}
        running = {line.strip() for line in stdout.splitlines() if line.strip()}

        container_networks = {}
        for name in sorted(visp_containers & running):
            rc, nets, _ = self.runner.run_quiet(
                [
                    "podman",
                    "inspect",
                    name,
                    "--format",
                    "{{range .NetworkSettings.Networks}}{{.NetworkID}} {{end}}",
                ]
            )
            if rc == 0:
                container_networks[name] = nets.strip() if nets.strip() else "none"
        return container_networks

    def get_stale_containers(self, services: List[Service]) -> List[Service]:
        """Return services whose running container was started from an older image
        than the current ``localhost/visp-<name>:latest`` tag.

        A container is considered stale when the image ID it was launched with
        differs from the ID of the currently stored tag.  Services that are not
        running, or whose image tag is not of the ``localhost/visp-*`` form, are
        silently skipped.
        """
        stale: List[Service] = []
        for svc in services:
            if svc.type != "container":
                continue
            # Quadlet containers are named after the unit (no "systemd-" prefix;
            # only networks get that prefix).
            container_name = svc.name

            # Get the image ID the running container was launched with
            rc, running_id, _ = self.runner.run_quiet(["podman", "inspect", container_name, "--format", "{{.ImageID}}"])
            if rc != 0 or not running_id.strip():
                continue  # not running

            # Derive expected image tag from the container name
            image_tag = f"localhost/visp-{svc.name}:latest"
            rc2, latest_id, _ = self.runner.run_quiet(["podman", "image", "inspect", image_tag, "--format", "{{.Id}}"])
            if rc2 != 0 or not latest_id.strip():
                continue  # image not built yet

            if running_id.strip() != latest_id.strip():
                stale.append(svc)

        return stale

    def scan_base_images(self) -> Dict[str, List[Tuple[str, str | None]]]:
        """Scan all Dockerfiles and extract base images.

        Returns:
            Dict mapping "image:tag" -> [list of (Dockerfile path, stage name) pairs].
            The stage name is the ``AS <name>`` label of the FROM line, or None for
            unnamed stages.
        """
        base_dir = get_config().project_dir
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
                                stage = parts[3] if len(parts) >= 4 and parts[2].upper() == "AS" else None

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

                                base_images[f"{name}:{tag}"].append((str(relative_path), stage))
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
                print(f"  {color('✓', Colors.GREEN)} {color(build_name, Colors.BLUE):25} {image_name}:{info['tag']}")
                print(f"      Size: {info['size']:12}  Created: {info['created']}")
            else:
                print(f"  {color('✗', Colors.RED)} {color(build_name, Colors.BLUE):25} {image_name} (not built)")
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
        print(color("=== VISP Networks ===", Colors.CYAN))
        print()

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
            if _classify_tag(tag) == "unpinned":
                tag_colored = color(tag, Colors.RED)
                status = "⚠ "
            else:
                tag_colored = color(tag, Colors.GREEN)
                status = "✓ "

            print(f"{status} {color(name, Colors.BLUE)}:{tag_colored}")

            # Show which files use this image
            for f, stage in files:
                stage_label = f" ({stage})" if stage else ""
                print(f"     └─ {f}{stage_label}")
            print()

        # Summary
        total = len(base_images)
        unpinned = sum(1 for img in base_images if _classify_tag(img.rsplit(":", 1)[-1]) == "unpinned")

        print(color("=== Summary ===", Colors.CYAN))
        print(f"Total base images: {total}")
        if unpinned > 0:
            print(color(f"⚠  Unpinned images: {unpinned}", Colors.YELLOW))
            print(
                color(
                    "   Consider pinning to specific versions for reproducibility",
                    Colors.YELLOW,
                )
            )
        else:
            print(color("✓ All images are pinned to specific versions", Colors.GREEN))
