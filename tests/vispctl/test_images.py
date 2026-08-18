"""Tests for vispctl.images — ImageManager stale-container detection."""

from vispctl.images import ImageManager
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
