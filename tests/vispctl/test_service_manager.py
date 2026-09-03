from vispctl.service import DEFAULT_SERVICES
from vispctl.service_manager import ServiceManager


class FakeRunner:
    def __init__(self):
        self.systemctl_calls = []

    def run_quiet(self, cmd):
        # Simulate units that are not running, so start/stop take the real path
        if cmd and "is-active" in cmd:
            return 0, "inactive", ""
        if cmd and cmd[0] == "podman":
            # podman network exists -> returncode 0 to indicate existence
            return 0, "", ""
        return 0, "active", ""

    def unit_is_active(self, unit):
        _, out, _ = self.run_quiet(["systemctl", "--user", "is-active", f"{unit}.service"])
        return out.strip() in ("active", "activating")

    def systemctl(self, *args, **kwargs):
        self.systemctl_calls.append(args)

        class R:
            def __init__(self):
                self.returncode = 0
                self.stderr = ""
                self.stdout = "generated\n"

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
    assert "tratt-net.service" not in out
    assert out.count("skipped") == 2
    stopped = [arg for call in fr.systemctl_calls if call[0] == "stop" for arg in call]
    assert "visp-net.service" not in stopped
    assert "tratt-net.service" not in stopped
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


class IsEnabledRunner:
    def __init__(self, state="generated"):
        self.state = state
        self.systemctl_calls = []

    def run_quiet(self, cmd):
        return 0, "", ""

    def systemctl(self, *args, **kwargs):
        self.systemctl_calls.append(args)

        class R:
            returncode = 0
            stderr = ""
            stdout = ""

        r = R()
        if args and args[0] == "is-enabled":
            r.stdout = f"{self.state}\n"
            # Real systemctl is-enabled exits non-zero for disabled/static/masked
            # units. The code no longer depends on the exit code, but keep the
            # fake faithful so a regression back to rc-based logic is caught.
            r.returncode = 0 if self.state in ("enabled", "indirect") else 1
        return r


def test_enable_already_enabled_generated(tmp_path, capsys):
    # No drop-in and systemd reports 'generated' (quadlet units) → no action.
    (tmp_path / "mongo.container").write_text("[Install]\nWantedBy=default.target\n")
    fr = IsEnabledRunner("generated")
    m = ServiceManager(fr, DEFAULT_SERVICES, systemd_dir=tmp_path)

    m.enable("mongo")
    out = capsys.readouterr().out

    assert "Already enabled" in out
    assert ("enable", "mongo.service") not in fr.systemctl_calls


def test_enable_reenables_manually_disabled_unit(tmp_path, capsys):
    # No drop-in, but a manual 'systemctl --user disable' put the unit in a
    # disabled state → 'up' must re-enable it, not report 'Already enabled'.
    (tmp_path / "mongo.container").write_text("[Install]\nWantedBy=default.target\n")
    fr = IsEnabledRunner("disabled")
    m = ServiceManager(fr, DEFAULT_SERVICES, systemd_dir=tmp_path)

    m.enable("mongo")
    out = capsys.readouterr().out

    assert "Already enabled" not in out
    assert "Enabled" in out
    assert ("enable", "mongo.service") in fr.systemctl_calls


def test_enable_removes_dropin_and_reports_enabled(tmp_path, capsys):
    (tmp_path / "mongo.container").write_text("[Install]\nWantedBy=default.target\n")
    dropin_dir = tmp_path / "mongo.container.d"
    dropin_dir.mkdir()
    (dropin_dir / "90-visp-autostart.conf").write_text("[Install]\nWantedBy=\n")
    fr = IsEnabledRunner("generated")
    m = ServiceManager(fr, DEFAULT_SERVICES, systemd_dir=tmp_path)

    m.enable("mongo")
    out = capsys.readouterr().out

    assert not (dropin_dir / "90-visp-autostart.conf").exists()
    assert "Enabled" in out
    assert ("enable", "mongo.service") not in fr.systemctl_calls


def test_enable_not_loaded_unit_says_reload(tmp_path, capsys):
    # is-enabled prints nothing to stdout when the unit is not loaded yet
    # (installed but no daemon-reload) — say so instead of claiming 'Already enabled'.
    (tmp_path / "mongo.container").write_text("[Install]\nWantedBy=default.target\n")
    fr = IsEnabledRunner("")
    m = ServiceManager(fr, DEFAULT_SERVICES, systemd_dir=tmp_path)

    m.enable("mongo")
    out = capsys.readouterr().out

    assert "Already enabled" not in out
    assert "Not loaded yet" in out
    assert "./visp.py reload" in out
    assert ("enable", "mongo.service") not in fr.systemctl_calls


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
