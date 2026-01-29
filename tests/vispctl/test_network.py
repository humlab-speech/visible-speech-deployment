from vispctl.network import NetworkManager


class FakeRunner:
    def __init__(self, ls_output=""):
        self.ls_output = ls_output
        self.commands = []

    def run_quiet(self, cmd):
        # Simulate 'podman network ls' and 'podman info'
        if cmd[:3] == ["podman", "network", "ls"]:
            return 0, self.ls_output, ""
        if cmd[:3] == ["podman", "info", "--format"] or cmd[:2] == ["podman", "info"]:
            return 0, "netavark", ""
        return 0, "", ""

    def run(self, cmd, check=True):
        self.commands.append(cmd)

        class R:
            def __init__(self):
                self.returncode = 0

        return R()


def test_check_netavark_true():
    fr = FakeRunner()
    nm = NetworkManager(fr)
    is_net, backend = nm.check_netavark()
    assert is_net is True
    assert backend == "netavark"


def test_ensure_networks_create_missing():
    fr = FakeRunner(ls_output="")
    nm = NetworkManager(fr)
    ok = nm.ensure_networks_exist()
    assert ok is True
    # Ensure we attempted to create networks
    found = any(
        cmd and cmd[0] == "podman" and cmd[1] == "network" and "create" in cmd
        for cmd in fr.commands
    )
    assert found is True


def test_configure_netavark_requires_packages(tmp_path):
    class FakeBadPkgRunner(FakeRunner):
        def run_quiet(self, cmd):
            if cmd[:2] == ["dpkg", "-l"]:
                return 1, "", "not installed"
            return super().run_quiet(cmd)

    fr = FakeBadPkgRunner()
    nm = NetworkManager(fr, containers_conf=tmp_path / "containers.conf")
    ok = nm.configure_netavark()
    assert ok is False


def test_configure_netavark_writes_file(tmp_path):
    class FakeGoodPkgRunner(FakeRunner):
        def run_quiet(self, cmd):
            if cmd[:2] == ["dpkg", "-l"]:
                return 0, "installed", ""
            return super().run_quiet(cmd)

    conf = tmp_path / "containers.conf"
    fr = FakeGoodPkgRunner()
    nm = NetworkManager(fr, containers_conf=conf)
    ok = nm.configure_netavark()
    assert ok is True
    assert conf.exists()
    content = conf.read_text()
    assert 'network_backend = "netavark"' in content


def test_migrate_to_netavark_calls_system_reset(tmp_path):
    calls = []

    class FakeRunnerForMigrate(FakeRunner):
        def run_quiet(self, cmd):
            if cmd[:2] == ["dpkg", "-l"]:
                return 0, "installed", ""
            return super().run_quiet(cmd)

        def run(self, cmd, check=True):
            calls.append(cmd)

            class R:
                def __init__(self):
                    self.returncode = 0

            return R()

    fr = FakeRunnerForMigrate()
    nm = NetworkManager(fr, containers_conf=tmp_path / "containers.conf")
    ok = nm.migrate_to_netavark()
    assert ok is True
    # ensure podman system reset was called
    assert any(
        cmd[:3] == ["podman", "system", "reset"]
        or cmd[:3] == ["podman", "system", "reset"]
        for cmd in calls
    )
    assert any("system" in c and "reset" in c for c in calls)
