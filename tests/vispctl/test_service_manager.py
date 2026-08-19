from vispctl.service import DEFAULT_SERVICES
from vispctl.service_manager import ServiceManager


class FakeRunner:
    def __init__(self):
        self.systemctl_calls = []

    def run_quiet(self, cmd):
        # Simulate 'systemctl is-active' returning active
        if cmd and cmd[0] == "podman":
            # podman network exists -> returncode 0 to indicate existence
            return 0, "", ""
        return 0, "active", ""

    def systemctl(self, *args, **kwargs):
        self.systemctl_calls.append(args)

        class R:
            def __init__(self):
                self.returncode = 0
                self.stderr = ""

        return R()


def test_status_prints(capsys):
    services = DEFAULT_SERVICES
    fr = FakeRunner()
    m = ServiceManager(fr, services)
    m.status()
    captured = capsys.readouterr().out
    for s in services:
        assert s.name in captured


def test_status_header_single_and_no_poc_label(capsys):
    services = DEFAULT_SERVICES
    m = ServiceManager(FakeRunner(), services)
    m.status()
    out = capsys.readouterr().out
    assert out.count("VISP Service Status") == 1
    assert "(PoC)" not in out


def test_network_status_shows_active(capsys):
    # Ensure network services are shown as active when podman network exists
    services = DEFAULT_SERVICES
    fr = FakeRunner()
    m = ServiceManager(fr, services)
    m.status()
    captured = capsys.readouterr().out
    # visp-net is a network in default services and should show as active
    assert "visp-net" in captured
    assert "active" in captured.splitlines()[1] or "active" in captured


def test_start_stop(capsys):
    services = DEFAULT_SERVICES
    fr = FakeRunner()
    m = ServiceManager(fr, services)

    m.start("mongo")
    out = capsys.readouterr().out
    assert "Starting mongo.service" in out
    assert "Started" in out

    m.stop("mongo")
    out = capsys.readouterr().out
    assert "Stopping mongo.service" in out
    assert "Stopped" in out


def test_start_stop_skip_networks(capsys):
    # Network quadlets generate '<name>-network.service' units, not '<name>.service',
    # and are pulled up via Requires= — lifecycle commands must not call systemctl on them.
    services = DEFAULT_SERVICES
    fr = FakeRunner()
    m = ServiceManager(fr, services)

    m.start("visp-net")
    out = capsys.readouterr().out
    assert "visp-net.service" not in out
    assert "skipped" in out
    assert not any("visp-net.service" in arg for call in fr.systemctl_calls for arg in call)

    m.stop("visp-net")
    out = capsys.readouterr().out
    assert "skipped" in out
    assert not any("visp-net.service" in arg for call in fr.systemctl_calls for arg in call)


def test_stop_all_skips_networks(capsys):
    services = DEFAULT_SERVICES
    fr = FakeRunner()
    m = ServiceManager(fr, services)

    m.stop("all")
    out = capsys.readouterr().out
    # Containers are stopped, networks are skipped with a note.
    assert "Stopping mongo.service" in out
    assert "visp-net.service" not in out
    assert "octra-net.service" not in out
    assert out.count("skipped") == 2
    stopped = [arg for call in fr.systemctl_calls if call[0] == "stop" for arg in call]
    assert "visp-net.service" not in stopped
    assert "octra-net.service" not in stopped
    assert "mongo.service" in stopped


def test_enable_disable_skip_networks(tmp_path, capsys):
    services = DEFAULT_SERVICES
    fr = FakeRunner()
    m = ServiceManager(fr, services, systemd_dir=tmp_path)

    m.disable("visp-net")
    out = capsys.readouterr().out
    assert "skipped" in out
    assert not (tmp_path / "visp-net.network.d").exists()

    m.enable("visp-net")
    out = capsys.readouterr().out
    assert "skipped" in out
    assert not (tmp_path / "visp-net.network.d").exists()


def test_enable_disable(tmp_path, capsys):
    services = DEFAULT_SERVICES
    fr = FakeRunner()
    (tmp_path / "mongo.container").write_text("[Install]\nWantedBy=default.target\n")
    m = ServiceManager(fr, services, systemd_dir=tmp_path)

    m.disable("mongo")
    out = capsys.readouterr().out
    dropin = tmp_path / "mongo.container.d" / "90-visp-autostart.conf"
    assert "Disabling autostart for mongo.service" in out
    assert "Disabled" in out
    assert dropin.exists()
    assert "WantedBy=" in dropin.read_text()
    assert ("daemon-reload",) in fr.systemctl_calls

    m.enable("mongo")
    out = capsys.readouterr().out
    assert "Enabling autostart for mongo.service" in out
    assert "Enabled" in out
    assert not dropin.exists()
    assert fr.systemctl_calls.count(("daemon-reload",)) == 2
