"""Tests for vispctl.images — stale containers, base images, third-party images."""

import json

from vispctl.images import ImageManager, ThirdPartyImage
from vispctl.registry import RegistryError
from vispctl.service import Service


class _FakeRunner:
    """Fake runner simulating podman inspect for stale-container detection."""

    def __init__(self, running_id="running-id", latest_id="latest-id"):
        self.running_id = running_id
        self.latest_id = latest_id
        self.inspected_containers = []

    def run_quiet(self, cmd):
        if cmd[:2] == ["podman", "inspect"] and "{{.ImageID}}" in cmd:
            self.inspected_containers.append(cmd[2])
            return 0, self.running_id, ""
        if cmd[:3] == ["podman", "image", "inspect"]:
            return 0, self.latest_id, ""
        return 0, "", ""


def test_get_stale_containers_uses_unprefixed_name():
    """get_stale_containers inspects '<name>', not 'systemd-<name>'."""
    runner = _FakeRunner(running_id="old-id", latest_id="new-id")
    im = ImageManager(runner)
    svc = Service("wsrng-server", "container", "wsrng-server.container")

    stale = im.get_stale_containers([svc])

    assert runner.inspected_containers == ["wsrng-server"]  # no systemd- prefix
    assert stale == [svc]  # running id != latest id → stale


def test_get_stale_containers_not_stale_when_same_image():
    """A container running the latest image is not stale."""
    runner = _FakeRunner(running_id="same-id", latest_id="same-id")
    im = ImageManager(runner)
    svc = Service("mongo", "container", "mongo.container")

    assert im.get_stale_containers([svc]) == []


def test_get_stale_containers_skips_not_running():
    """A service whose container isn't running is skipped."""

    class NotRunningRunner:
        def run_quiet(self, cmd):
            if cmd[:2] == ["podman", "inspect"] and "{{.ImageID}}" in cmd:
                return 1, "", "no such container"
            return 0, "", ""

    im = ImageManager(NotRunningRunner())
    svc = Service("mongo", "container", "mongo.container")

    assert im.get_stale_containers([svc]) == []


def test_get_container_networks_lists_running_visp_containers():
    """get_container_networks lists running VISP containers (not non-VISP ones)."""

    class FakeRunner:
        def run_quiet(self, cmd):
            if cmd[:2] == ["podman", "ps"]:
                # A VISP container, another VISP one, and a non-VISP host container.
                return 0, "mongo\nkiwix-proxy\nsession-manager\n", ""
            if cmd[:2] == ["podman", "inspect"]:
                return 0, "netid1 netid2 ", ""
            return 0, "", ""

    im = ImageManager(FakeRunner())
    nets = im.get_container_networks()

    assert "mongo" in nets
    assert "session-manager" in nets
    assert "kiwix-proxy" not in nets  # not a VISP container service
    assert nets["mongo"] == "netid1 netid2"


def test_get_container_networks_empty_when_none_running():
    """No running VISP containers → empty dict."""

    class FakeRunner:
        def run_quiet(self, cmd):
            if cmd[:2] == ["podman", "ps"]:
                return 0, "kiwix-proxy\n", ""
            return 0, "", ""

    im = ImageManager(FakeRunner())
    assert im.get_container_networks() == {}


# ── Tag pinning heuristic ──────────────────────────────────────────────────────


def test_classify_tag_pinned():
    from vispctl.images import _classify_tag

    assert _classify_tag("2.4.67") == "pinned"
    assert _classify_tag("3.23") == "pinned"
    assert _classify_tag("20.20.2-alpine3.22") == "pinned"
    assert _classify_tag("trixie-20260406") == "pinned"
    assert _classify_tag("20260406") == "pinned"


def test_classify_tag_unpinned():
    from vispctl.images import _classify_tag

    assert _classify_tag("latest") == "unpinned"
    assert _classify_tag("bookworm") == "unpinned"
    assert _classify_tag("stable") == "unpinned"
    # Bare major tags can still move (point releases)
    assert _classify_tag("3") == "unpinned"
    assert _classify_tag("24") == "unpinned"


def test_classify_tag_digest():
    from vispctl.images import _classify_tag

    assert _classify_tag("@sha256:abc123") == "digest"


# ── Base image stage labels ────────────────────────────────────────────────────


def test_scan_base_images_includes_stage_names(tmp_path, monkeypatch):
    """Multi-stage Dockerfiles report the stage name per FROM line."""
    from vispctl import images as images_mod

    dockerfile = tmp_path / "docker" / "octra" / "Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text(
        "FROM node:24.15.0 AS builder\n"
        "RUN npm ci\n"
        "FROM httpd:2.4.67\n"
        "COPY --from=builder /app /usr/share/apache2/htdocs\n"
    )

    monkeypatch.setattr(images_mod, "get_config", lambda: type("C", (), {"project_dir": tmp_path})())

    im = ImageManager(runner=None)
    base = im.scan_base_images()

    assert base["node:24.15.0"] == [("docker/octra/Dockerfile", "builder")]
    assert base["httpd:2.4.67"] == [("docker/octra/Dockerfile", None)]


# ── Third-party (pulled) images ────────────────────────────────────────────────


def _write_quadlets(root):
    prod = root / "quadlets" / "prod"
    prod.mkdir(parents=True)
    (prod / "matomo.container").write_text("[Container]\nImage=docker.io/library/matomo:5.8.0\n")
    (prod / "mongo.container").write_text("[Container]\nImage=docker.io/library/mongo:6.0.27\n")
    (prod / "apache.container").write_text("[Container]\nImage=localhost/visp-apache:latest\n")
    placeholder = root / "quadlets" / "placeholder"
    placeholder.mkdir()
    (placeholder / "prod.container").write_text("[Container]\nImage=docker.io/library/nginx:1.30.0-alpine3.23\n")


def test_scan_third_party_images_skips_localhost_and_includes_placeholder(tmp_path):
    from vispctl.images import scan_third_party_images

    _write_quadlets(tmp_path)

    assert scan_third_party_images(tmp_path, "prod") == {
        "docker.io/library/matomo:5.8.0": ["matomo"],
        "docker.io/library/mongo:6.0.27": ["mongo"],
        "docker.io/library/nginx:1.30.0-alpine3.23": ["placeholder"],
    }


class _PodmanImagesRunner:
    """Fake runner answering `podman images --format json` and `podman ps`."""

    def __init__(self, images, ps=""):
        self.images, self.ps = images, ps

    def run_quiet(self, cmd):
        if cmd[:2] == ["podman", "images"]:
            return 0, json.dumps(self.images), ""
        if cmd[:2] == ["podman", "ps"]:
            return 0, self.ps, ""
        return 1, "", ""


class _FakeClient:
    def __init__(self, tags, digests):
        self.tags, self.digests = tags, digests

    def list_tags(self, ref):
        return self.tags[ref.repository]

    def tag_digest(self, ref):
        if ref.repository not in self.digests:
            raise RegistryError("cannot reach registry-1.docker.io: timed out")
        return self.digests[ref.repository]


def test_get_third_party_images_compares_digests_and_versions(tmp_path, monkeypatch):
    from vispctl import images as images_mod

    _write_quadlets(tmp_path)
    monkeypatch.setattr(images_mod, "get_config", lambda: type("C", (), {"project_dir": tmp_path})())
    monkeypatch.setattr(images_mod, "get_current_mode", lambda: "prod")

    local = [
        {  # matomo: pulled before the registry rebuilt 5.8.0
            "Id": "aaa111",
            "Names": ["docker.io/library/matomo:5.8.0"],
            "RepoDigests": ["docker.io/library/matomo@sha256:old", "docker.io/library/matomo@sha256:arch"],
            "CreatedAt": "2026-04-22T10:00:00Z",
        },
        {"Id": "bbb222", "Names": ["docker.io/library/matomo:5.14.0"], "RepoDigests": []},
        {  # mongo: identical to the registry
            "Id": "ccc333",
            "Names": ["docker.io/library/mongo:6.0.27"],
            "RepoDigests": ["docker.io/library/mongo@sha256:same"],
            "CreatedAt": "2025-12-29T00:00:00Z",
        },
        # nginx: not pulled at all
    ]
    # The matomo container was started from an image the tag no longer points at.
    ps = "matomo\tdocker.io/library/matomo:5.8.0\tzzz999\nmongo\tdocker.io/library/mongo:6.0.27\tccc333"
    client = _FakeClient(
        tags={
            "library/matomo": ["5.8.0", "5.9.0", "5.14.0", "5.14"],
            "library/mongo": ["6.0.27", "6.0.28", "9.0.4"],
            "library/nginx": ["1.30.0-alpine3.23"],
        },
        digests={"library/matomo": "sha256:new", "library/mongo": "sha256:same"},
    )

    im = ImageManager(_PodmanImagesRunner(local, ps))
    result = {i.services[0]: i for i in im.get_third_party_images(client=client)}

    matomo = result["matomo"]
    assert matomo.local_id == "aaa111"
    assert matomo.digest_matches is False  # same tag, rebuilt upstream
    assert matomo.other_local_tags == ["5.14.0"]
    assert matomo.stale_containers == ["matomo"]
    assert (matomo.latest_same_major, matomo.latest_new_major) == ("5.14.0", None)
    assert matomo.created == "2026-04-22"

    mongo = result["mongo"]
    assert mongo.digest_matches is True
    assert mongo.stale_containers == []
    assert (mongo.latest_same_major, mongo.latest_new_major) == ("6.0.28", "9.0.4")

    nginx = result["placeholder"]
    assert nginx.local_id is None
    assert nginx.digest_matches is None
    assert nginx.remote_error == "cannot reach registry-1.docker.io: timed out"


def test_get_third_party_images_offline_skips_registry(tmp_path, monkeypatch):
    from vispctl import images as images_mod

    _write_quadlets(tmp_path)
    monkeypatch.setattr(images_mod, "get_config", lambda: type("C", (), {"project_dir": tmp_path})())
    monkeypatch.setattr(images_mod, "get_current_mode", lambda: "prod")

    class _NoClient:
        def __getattr__(self, name):
            raise AssertionError("registry must not be contacted offline")

    im = ImageManager(_PodmanImagesRunner([]))
    images = im.get_third_party_images(check_remote=False, client=_NoClient())

    assert len(images) == 3
    assert all(i.remote_digest is None and i.remote_error is None for i in images)


def test_scan_base_images_finds_containerfiles(tmp_path, monkeypatch):
    """Containerfile* counts too — whisperx's only base lives in one, and the
    Dockerfile-only glob silently reported no bases for that image."""
    from vispctl import images as images_mod

    containerfile = tmp_path / "external" / "whispervault" / "container" / "Containerfile"
    containerfile.parent.mkdir(parents=True)
    containerfile.write_text("FROM python:3.12.11-slim AS base\nRUN pip install x\n")

    monkeypatch.setattr(images_mod, "get_config", lambda: type("C", (), {"project_dir": tmp_path})())

    im = ImageManager(runner=None)
    base = im.scan_base_images()

    assert base["python:3.12.11-slim"] == [("external/whispervault/container/Containerfile", "base")]


def test_digest_match_is_exact_not_suffix():
    """A last-two-chars matcher would call 'sha256:gold' a match for 'sha256:old'."""
    img = ThirdPartyImage(
        ref="docker.io/library/app:1",
        services=["x"],
        local_id="aaa111",
        local_digests={"sha256:old"},
        remote_digest="sha256:gold",
    )
    assert img.digest_matches is False
    img.remote_digest = "sha256:old"
    assert img.digest_matches is True
