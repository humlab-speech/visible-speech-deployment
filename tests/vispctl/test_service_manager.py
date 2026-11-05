import pytest

from vispctl.service import DEFAULT_SERVICES
from vispctl.service_manager import ServiceManager


@pytest.fixture(autouse=True)
def _no_wall_clock(monkeypatch):
    # stop() verifies pull-up after a 3s pause and _unit_came_up polls with
    # 0.4s spacing; faking the clock keeps the polling logic exercised without
    # costing 6s per suite run.
    monkeypatch.setattr("vispctl.service_manager.time.sleep", lambda s: None)


class FakeRunner:
    def __init__(self):
        self.systemctl_calls = []
        self.started_units = set()

    def run_quiet(self, cmd):
        # Simulate units that are not running, so start/stop take the real
        # path — except ones this fake has been told to start: those come up.
        if cmd and "is-active" in cmd:
            unit = cmd[-1].replace(".service", "")
            return 0, ("active" if unit in self.started_units else "inactive"), ""
        if cmd and cmd[0] == "podman":
            # podman network exists -> returncode 0 to indicate existence
            return 0, "", ""
        return 0, "active", ""

    def unit_is_active(self, unit):
        _, out, _ = self.run_quiet(["systemctl", "--user", "is-active", f"{unit}.service"])
        return out.strip() in ("active", "activating")

    def systemctl(self, *args, **kwargs):
        self.systemctl_calls.append(args)
        if args and args[0] == "start" and len(args) > 1:
            self.started_units.add(args[1].replace(".service", ""))

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


def test_status_distinguishes_failed_from_inactive(capsys):
    # systemctl is-active exits 3 for both, so status must read the output.
    class FailedRunner(FakeRunner):
        def run_quiet(self, cmd):
            if "is-active" in cmd:
                if cmd[-1] == "wsrng-server.service":
                    return 3, "failed", ""
                return 3, "inactive", ""
            return super().run_quiet(cmd)

    m = ServiceManager(FailedRunner(), DEFAULT_SERVICES)
    m.status()
    out = capsys.readouterr().out
    wsrng_line = next(line for line in out.splitlines() if "wsrng-server:" in line)
    assert "failed" in wsrng_line
    mongo_line = next(line for line in out.splitlines() if " mongo:" in line)
    assert "inactive" in mongo_line
    assert "1 service(s) failed: wsrng-server" in out


def test_network_status_shows_active(capsys):
    # R2 BROKEN: the old assertion scanned ALL output for "active", which is
    # a substring of the "inactive" lines every other service prints, and the
    # fake always answered exists=rc0. Pin the visp-net ROW and its negative.
    fr = FakeRunner()
    ServiceManager(fr, DEFAULT_SERVICES).status()
    lines = capsys.readouterr().out.splitlines()
    row = [ln for ln in lines if "visp-net:" in ln]
    assert row, "network row missing"
    assert "active" in row[0]

    missing = FakeRunner()
    original = missing.run_quiet

    def rc_when_absent(cmd):
        if "network" in cmd and "exists" in cmd:
            return 1, "", ""
        return original(cmd)

    missing.run_quiet = rc_when_absent
    ServiceManager(missing, DEFAULT_SERVICES).status()
    row2 = [ln for ln in capsys.readouterr().out.splitlines() if "visp-net:" in ln]
    assert "not found" in row2[0] and "active" not in row2[0]


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


def test_start_resets_failed_state_first():
    # A unit that hit StartLimitBurst refuses 'start' until reset-failed.
    fr = FakeRunner()
    ServiceManager(fr, DEFAULT_SERVICES).start("mongo")
    assert fr.systemctl_calls == [("reset-failed", "mongo.service"), ("start", "mongo.service")]


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
    assert "artic-net.service" not in out
    assert out.count("skipped") == 3
    stopped = [arg for call in fr.systemctl_calls if call[0] == "stop" for arg in call]
    assert "visp-net.service" not in stopped
    assert "tratt-net.service" not in stopped
    assert "artic-net.service" not in stopped
    assert "mongo.service" in stopped


def test_enable_disable_skip_networks(tmp_path, capsys):
    services = DEFAULT_SERVICES
    fr = FakeRunner()
    m = ServiceManager(fr, services, systemd_dir=tmp_path)

    m.disable("visp-net")
    out = capsys.readouterr().out
    assert "skipped" in out

    m.enable("visp-net")
    out = capsys.readouterr().out
    assert "skipped" in out
    # R2 TAUTOLOGY fix: the .network.d directory never existed in this
    # fixture, so its absence proved nothing. Pin behaviour instead: no
    # systemctl verb may ever name the network unit.
    assert not [c for c in fr.systemctl_calls if "visp-net" in " ".join(c)]
    assert not list(tmp_path.glob("visp-net*"))


class IsEnabledRunner:
    def __init__(self, state="generated"):
        self.state = state
        self.systemctl_calls = []

    def run_quiet(self, cmd):
        return 0, "", ""

    def systemctl(self, *args, **kwargs):
        self.systemctl_calls.append(args)
        if args and args[0] == "start" and len(args) > 1:
            self.started_units.add(args[1].replace(".service", ""))

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
    # R2: leaving the empty drop-in dir behind survives the old suite
    assert not dropin_dir.exists(), "enable must remove the emptied drop-in dir"
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
    dropin_text = dropin.read_text()
    assert "WantedBy=" in dropin_text
    # R2 F3: the old assertion also passed on a drop-in that ENABLES boot
    assert "WantedBy=default.target" not in dropin_text
    assert ("daemon-reload",) in fr.systemctl_calls

    m.enable("mongo")
    out = capsys.readouterr().out
    assert "Enabling autostart for mongo.service" in out
    assert "Enabled" in out
    assert not dropin.exists()
    assert fr.systemctl_calls.count(("daemon-reload",)) == 2


def test_stop_order_is_reverse_of_declaration_order():
    # Requires= chains mean dependents must die before their networks and
    # early-declared backends; a flipped iteration order passes every older
    # assertion, so pin the exact sequence.
    fr = FakeRunner()
    ServiceManager(fr, DEFAULT_SERVICES).stop("all")
    stopped = [u for action, u in fr.systemctl_calls if action == "stop"]
    containers = [f"{s.name}.service" for s in DEFAULT_SERVICES if s.type == "container"]
    assert stopped == list(reversed(containers))


def test_stopped_unit_pulled_back_up_is_warned(capsys):
    # FakeRunner models 'stop does not clear running state' -> the stopped
    # unit must be reported as pulled back up.
    fr = FakeRunner()
    m = ServiceManager(fr, DEFAULT_SERVICES)
    m.start("mongo")
    m.stop("mongo")
    out = capsys.readouterr().out
    assert "came back up" in out


def test_clean_stop_is_not_warned(capsys):
    class CleanStopRunner(FakeRunner):
        def systemctl(self, *args, **kwargs):
            res = super().systemctl(*args, **kwargs)
            if args and args[0] == "stop" and len(args) > 1:
                self.started_units.discard(args[1].replace(".service", ""))
            return res

    ServiceManager(CleanStopRunner(), DEFAULT_SERVICES).stop("mongo")
    out = capsys.readouterr().out
    assert "Stopped" in out and "came back up" not in out
