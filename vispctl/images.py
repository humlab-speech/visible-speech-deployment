"""ImageManager: handles container image inspection and auditing."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .config import get_config
from .placeholder import placeholder_template
from .quadlets import get_current_mode
from .registry import ImageRef, RegistryClient, RegistryError, is_version_tag, newer_versions
from .runner import Colors, Runner, color, printable
from .service import Service, container_service_names, get_runtime_services


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


def scan_third_party_images(project_dir: Path, mode: str) -> Dict[str, List[str]]:
    """Map each registry image the quadlets of *mode* run to the quadlets using it.

    Covers quadlets/<mode>/*.container plus the placeholder quadlet, which
    lives apart from them. ``localhost/`` images are ours and are built,
    not pulled, so they are left out.
    """
    files = sorted((project_dir / "quadlets" / mode).glob("*.container"))
    placeholder = placeholder_template(project_dir, mode)
    if placeholder.exists():
        files.append(placeholder)

    images: Dict[str, List[str]] = {}
    for qfile in files:
        name = "placeholder" if qfile == placeholder else qfile.stem
        for line in qfile.read_text().splitlines():
            if line.startswith("Image="):
                ref = line.split("=", 1)[1].strip()
                if not ref.startswith("localhost/"):
                    images.setdefault(ref, []).append(name)
    return images


@dataclass
class ThirdPartyImage:
    """Local and registry state of one pinned third-party image."""

    ref: str
    services: List[str]
    local_id: str | None = None
    local_digests: set = field(default_factory=set)
    created: str | None = None
    other_local_tags: List[str] = field(default_factory=list)
    stale_containers: List[str] = field(default_factory=list)
    remote_digest: str | None = None
    latest_same_major: str | None = None
    latest_new_major: str | None = None
    remote_error: str | None = None

    @property
    def tag(self) -> str | None:
        return ImageRef.parse(self.ref).tag

    @property
    def pinned_by_digest(self) -> bool:
        return ImageRef.parse(self.ref).digest is not None

    @property
    def digest_matches(self) -> bool | None:
        """True if the local image is what the registry serves for the tag; None if unknown."""
        if self.local_id is None or self.remote_digest is None:
            return None
        return self.remote_digest in self.local_digests


def _lookup_remote(client: RegistryClient, img: ThirdPartyImage) -> None:
    """Fill in the registry half of *img*; failures are recorded, not raised."""
    ref = ImageRef.parse(img.ref)
    try:
        tags = client.list_tags(ref)
        img.latest_same_major, img.latest_new_major = newer_versions(ref.tag, tags)
        if ref.digest is None:
            img.remote_digest = client.tag_digest(ref)
    except RegistryError as e:
        img.remote_error = str(e)


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

    def get_third_party_images(
        self, check_remote: bool = True, client: RegistryClient | None = None
    ) -> List[ThirdPartyImage]:
        """Collect local, running and (optionally) registry state of every pinned third-party image."""
        pinned = scan_third_party_images(get_config().project_dir, get_current_mode())
        images = [ThirdPartyImage(ref, services) for ref, services in pinned.items()]

        rc, stdout, _ = self.runner.run_quiet(["podman", "images", "--format", "json"])
        try:
            local = json.loads(stdout) if rc == 0 and stdout else []
        except ValueError:
            local = []

        rc, stdout, _ = self.runner.run_quiet(
            ["podman", "ps", "--no-trunc", "--format", "{{.Names}}\t{{.Image}}\t{{.ImageID}}"]
        )
        running = [line.split("\t") for line in stdout.splitlines() if line.count("\t") == 2] if rc == 0 else []

        for img in images:
            ref = ImageRef.parse(img.ref)
            name = img.ref.partition("@")[0]
            repo = name.rsplit(":", 1)[0] if ref.tag and name.endswith(f":{ref.tag}") else name

            for entry in local:
                names = entry.get("Names") or []
                digests = {d.partition("@")[2] for d in entry.get("RepoDigests") or []}
                if (ref.digest and ref.digest in digests) or (not ref.digest and img.ref in names):
                    img.local_id = entry.get("Id")
                    img.local_digests = digests
                    img.created = (entry.get("CreatedAt") or "")[:10] or None
                for n in names:
                    if n.startswith(f"{repo}:") and n != img.ref:
                        img.other_local_tags.append(n.rsplit(":", 1)[1])
            img.other_local_tags = sorted(set(img.other_local_tags))

            if img.local_id:
                for container, image, image_id in running:
                    if image == img.ref and not (
                        img.local_id.startswith(image_id) or image_id.startswith(img.local_id)
                    ):
                        img.stale_containers.append(container)

        if check_remote and images:
            client = client or RegistryClient()
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda i: _lookup_remote(client, i), images))

        return images

    def scan_base_images(self) -> Dict[str, List[Tuple[str, str | None]]]:
        """Scan all Dockerfiles and extract base images.

        Returns:
            Dict mapping "image:tag" -> [list of (Dockerfile path, stage name) pairs].
            The stage name is the ``AS <name>`` label of the FROM line, or None for
            unnamed stages.
        """
        base_dir = get_config().project_dir
        dockerfiles = []

        # Search in docker/ and external/. Containerfile* too: whisperx's only
        # base lives in external/WhisperVault/container/Containerfile.
        for pattern in [
            "docker/**/Dockerfile*",
            "external/**/Dockerfile*",
            "docker/**/Containerfile*",
            "external/**/Containerfile*",
        ]:
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

    def get_base_freshness(self, check_remote: bool = True) -> list:
        """Freshness verdict of every registry base behind a built visp image.

        Imported here rather than at module scope: base_freshness imports this
        module, so a top-level import would be circular.
        """
        from .base_freshness import get_base_freshness

        return get_base_freshness(self.runner, check_remote=check_remote)

    def display_visp_images(self, check_remote: bool = True) -> None:
        """Display VISP container images, the freshness of their bases, and their status."""
        from .base_freshness import format_base_line, format_summary, is_moved, needs_rebuild

        print(color("=== VISP Container Images ===", Colors.CYAN))
        print()

        found_images, expected_images = self.get_visp_images()
        bases = self.get_base_freshness(check_remote)
        # ./visp.py apply takes one service and refuses names without a runtime quadlet,
        # so a remediation may only name a service the runtime actually runs: a quadlet
        # service (mongo and matomo are pulled, never built), still running after .env
        # has had its say. A service disabled here has no container to restart.
        runtime = {svc.name for svc in get_runtime_services(get_config().project_dir)}
        applyable = runtime & {b.service for b in bases}
        stale_services = {b.service for b in bases if needs_rebuild(b)}

        # Print status for each expected image
        for image_name, build_name in sorted(expected_images.items()):
            if image_name in found_images:
                info = found_images[image_name]
                # A stale base is a reason to look, so the marker says so rather than smiling
                marker = color("⚠", Colors.YELLOW) if build_name in stale_services else color("✓", Colors.GREEN)
                print(f"  {marker} {color(build_name, Colors.BLUE):25} {image_name}:{info['tag']}")
                print(f"      Size: {info['size']:12}  Created: {info['created']}")
                for base in [b for b in bases if b.service == build_name]:
                    print(format_base_line(base))
                    if is_moved(base):
                        # Only a confirmed move earns a build-and-apply command line.
                        print(f"      → {base.remediation(applyable)}")
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

        for line in format_summary(bases, checked=check_remote, applyable=applyable):
            print(line)
        print()

    def display_third_party_images(self, check_remote: bool = True) -> None:
        """Display pinned third-party images: local digest vs registry, and newer versions."""
        print(color("=== Third-party Images (pulled, not built) ===", Colors.CYAN))
        print()

        images = self.get_third_party_images(check_remote)
        if not images:
            print("  No third-party images in the quadlets.")
            print()
            return

        for img in images:
            services = ", ".join(img.services)
            if img.local_id:
                print(f"  {color('✓', Colors.GREEN)} {color(services, Colors.BLUE):25} {img.ref}")
                shown = img.remote_digest if img.digest_matches else next(iter(sorted(img.local_digests)), "?")
                print(f"      Local:     {shown[:19]}   Built: {img.created or '?'}")
            else:
                print(f"  {color('✗', Colors.RED)} {color(services, Colors.BLUE):25} {img.ref} (not pulled)")

            if img.other_local_tags:
                print(f"      Also local: {', '.join(img.other_local_tags)} (not what the quadlets run)")

            if check_remote:
                if img.remote_error:
                    print(color(f"      Registry:  check failed — {img.remote_error}", Colors.YELLOW))
                elif img.pinned_by_digest:
                    print("      Registry:  pinned by digest, cannot move")
                elif img.digest_matches:
                    print(color(f"      Registry:  same digest as the registry's '{img.tag}' ✓", Colors.GREEN))
                elif img.digest_matches is False:
                    print(
                        color(
                            f"      Registry:  '{img.tag}' was rebuilt upstream ({img.remote_digest[:19]})",
                            Colors.YELLOW,
                        )
                    )
                    print(f"                 ./visp.py images update {img.services[0]}   (pick the current tag)")

                if not img.remote_error and not img.pinned_by_digest:
                    print(f"      Newer:     {self._newer_line(img)}")

            for container in img.stale_containers:
                print(
                    color(
                        f"      ⚠ container '{container}' still runs an older image of this tag — restart it",
                        Colors.YELLOW,
                    )
                )
            print()

        if check_remote:
            rebuilt = [i for i in images if i.digest_matches is False]
            outdated = [i for i in images if i.latest_same_major or i.latest_new_major]
            failed = [i for i in images if i.remote_error]
            if rebuilt:
                print(
                    color(
                        f"{len(rebuilt)} image(s) rebuilt upstream under the same tag — pull again and restart.",
                        Colors.YELLOW,
                    )
                )
            if outdated:
                print(f"{len(outdated)} image(s) have newer versions. Move one with: ./visp.py images update <service>")
            if failed:
                print(color(f"{len(failed)} registry check(s) failed.", Colors.YELLOW))
            if not (rebuilt or outdated or failed):
                print(color("All third-party images match their registry and are the newest version.", Colors.GREEN))
        else:
            print("Registry not checked (--offline).")
        print()

    @staticmethod
    def _newer_line(img: ThirdPartyImage) -> str:
        if not is_version_tag(img.tag):
            return "n/a — tag is not a version"
        if not (img.latest_same_major or img.latest_new_major):
            return color("none, this is the newest", Colors.GREEN)
        major = img.tag.lstrip("v").split(".")[0]
        same = color(img.latest_same_major, Colors.YELLOW) if img.latest_same_major else "none"
        new = color(img.latest_new_major, Colors.YELLOW) if img.latest_new_major else "none"
        return f"latest {major}.x: {same}   ·   new major: {new}"

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
        print()

        from .base_freshness import fan_out

        print(color("=== Base freshness fan-out (cached checks, no network) ===", Colors.CYAN))
        checked_bases = self.get_base_freshness(check_remote=False)
        if not any(b.check_source != "none" for b in checked_bases):
            print("  no cached checks yet — run ./visp.py images to populate")
            return
        for ref, pairs in fan_out(checked_bases).items():
            who = ", ".join(f"{service} ({role})" for service, role in pairs)
            print(f"  {printable(ref)} → {who}")
        print()
