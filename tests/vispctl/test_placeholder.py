from pathlib import Path

import pytest

from vispctl import placeholder
from vispctl.exceptions import ServiceError
from vispctl.placeholder import (
    PLACEHOLDER_FILE,
    http_port,
    placeholder_template,
    placeholder_up,
    remove_placeholder,
)
from vispctl.quadlets import remove_stale_quadlets
from vispctl.service import DEFAULT_SERVICES
from vispctl.service_manager import ServiceManager

PROJECT_DIR = Path(__file__).resolve().parents[2]


class FakeRunner:
    def __init__(self, active=()):
        self.active = set(active)
        self.systemctl_calls = []
        self.run_calls = []

    def unit_is_active(self, unit):
        return unit in self.active

    def run_quiet(self, cmd):
        return 0, "inactive", ""

    def run(self, cmd, **kwargs):
        self.run_calls.append(cmd)

        class R:
            returncode = 0

        return R()

    def systemctl(self, *args, **kwargs):
        self.systemctl_calls.append(args)

        class R:
            returncode = 0
            stderr = ""
            stdout = ""

        return R()


def _render(content):
    return (
        content.replace("@@PROJECT_DIR@@", "/srv/visp")
        .replace("@@HTTP_PORT@@", "8081")
        .replace("@@BASE_DOMAIN@@", "visp.local")
    )


def _install_apache(systemd_dir, disabled):
    (systemd_dir / "apache.container").write_text("[Container]\n")
    if disabled:
        dropin = systemd_dir / "apache.container.d" / "90-visp-autostart.conf"
        dropin.parent.mkdir()
        dropin.write_text("[Install]\nWantedBy=\n")


def _directives(path, key):
    return sorted(line.split("=", 1)[1] for line in path.read_text().splitlines() if line.startswith(f"{key}="))


@pytest.mark.parametrize("mode", ["dev", "prod"])
def test_placeholder_publishes_exactly_apaches_ports(mode):
    # It stands in for apache, so it must answer on every port apache does.
    apache = PROJECT_DIR / "quadlets" / mode / "apache.container"
    assert _directives(placeholder_template(PROJECT_DIR, mode), "PublishPort") == _directives(apache, "PublishPort")


@pytest.mark.parametrize("mode", ["dev", "prod"])
def test_placeholder_template_conflicts_with_apache(mode):
    content = placeholder_template(PROJECT_DIR, mode).read_text()
    assert "Conflicts=apache.service" in content
    assert "ContainerName=visp-placeholder" in content


@pytest.mark.parametrize(
    "content, port",
    [
        ("PublishPort=8081:80\n", "8081"),
        ("PublishPort=8443:443\nPublishPort=127.0.0.1:8081:80\n", "8081"),
        ("PublishPort=8443:443\n", None),
    ],
)
def test_http_port(content, port):
    assert http_port(content) == port


def test_up_refuses_while_apache_runs(tmp_path):
    _install_apache(tmp_path, disabled=True)
    runner = FakeRunner(active={"apache"})
    with pytest.raises(ServiceError, match="apache is running"):
        placeholder_up(runner, PROJECT_DIR, tmp_path, "prod", _render)
    assert not (tmp_path / PLACEHOLDER_FILE).exists()


def test_up_refuses_when_apache_would_autostart(tmp_path):
    # A plain 'stop' leaves apache enabled; both would race for the port at boot.
    _install_apache(tmp_path, disabled=False)
    with pytest.raises(ServiceError, match="start again at boot"):
        placeholder_up(FakeRunner(), PROJECT_DIR, tmp_path, "prod", _render)
    assert not (tmp_path / PLACEHOLDER_FILE).exists()


def test_up_installs_and_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(placeholder, "probe", lambda port, timeout=3.0: 503)
    _install_apache(tmp_path, disabled=True)
    runner = FakeRunner()
    placeholder_up(runner, PROJECT_DIR, tmp_path, "prod", _render)

    installed = (tmp_path / PLACEHOLDER_FILE).read_text()
    assert "@@" not in installed
    assert "Volume=/srv/visp/mounts/placeholder/html:" in installed
    assert ("restart", "visp-placeholder.service") in runner.systemctl_calls
    assert ["podman", "pull", "--quiet", "docker.io/library/nginx:1.30.0-alpine3.23"] in runner.run_calls


def test_starting_apache_removes_placeholder(tmp_path):
    (tmp_path / PLACEHOLDER_FILE).write_text("[Container]\n")
    runner = FakeRunner()
    ServiceManager(runner, DEFAULT_SERVICES, systemd_dir=tmp_path).start("apache")
    assert not (tmp_path / PLACEHOLDER_FILE).exists()
    assert ("stop", "visp-placeholder.service") in runner.systemctl_calls


def test_starting_other_services_keeps_placeholder(tmp_path):
    (tmp_path / PLACEHOLDER_FILE).write_text("[Container]\n")
    ServiceManager(FakeRunner(), DEFAULT_SERVICES, systemd_dir=tmp_path).start("mongo")
    assert (tmp_path / PLACEHOLDER_FILE).exists()


def test_remove_placeholder_when_absent(tmp_path):
    runner = FakeRunner()
    assert remove_placeholder(runner, tmp_path) is False
    assert runner.systemctl_calls == []


def test_stale_sweep_leaves_placeholder_alone(tmp_path):
    # The rendered unit embeds the project path, which otherwise marks it as a stale VISP unit.
    project_dir = tmp_path / "visp"
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    (systemd_dir / PLACEHOLDER_FILE).write_text(f"Volume={project_dir}/mounts/placeholder/html:/x\n")
    assert remove_stale_quadlets(systemd_dir, project_dir, FakeRunner()) == []
    assert (systemd_dir / PLACEHOLDER_FILE).exists()
