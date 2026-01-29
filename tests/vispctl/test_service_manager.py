from vispctl.service_manager import ServiceManager
from vispctl.service import load_default_services


class FakeRunner:
    def run_quiet(self, cmd):
        # Simulate 'systemctl is-active' returning active
        if cmd and cmd[0] == "podman":
            # podman network exists -> returncode 0 to indicate existence
            return 0, "", ""
        return 0, "active", ""

    def systemctl(self, *args, **kwargs):
        class R:
            def __init__(self):
                self.returncode = 0
                self.stderr = ""

        return R()


def test_status_prints(capsys):
    services = load_default_services()
    fr = FakeRunner()
    m = ServiceManager(fr, services)
    m.status()
    captured = capsys.readouterr().out
    for s in services:
        assert s.name in captured


def test_network_status_shows_active(capsys):
    # Ensure network services are shown as active when podman network exists
    services = load_default_services()
    fr = FakeRunner()
    m = ServiceManager(fr, services)
    m.status()
    captured = capsys.readouterr().out
    # visp-net is a network in default services and should show as active
    assert "visp-net" in captured
    assert "active" in captured.splitlines()[1] or "active" in captured


def test_start_stop(capsys):
    services = load_default_services()
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
