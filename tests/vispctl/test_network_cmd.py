import types

import vispctl.network as net_mod


class FakeNM:
    def __init__(self, runner):
        self.runner = runner
        self.ensured = False

    def ensure_networks_exist(self):
        self.ensured = True
        return True

    def check_netavark(self):
        return True, "netavark"


def test_cmd_network_ensure_invokes_manager(monkeypatch, capsys):
    # BROKEN fix: the old test printed the banner from cmd_network itself and
    # never checked that ensure_networks_exist actually ran.
    runner = type("FakeRunner", (), {})()
    made = []

    class RecordingNM(FakeNM):
        def __init__(self, runner):
            super().__init__(runner)
            made.append(self)

    monkeypatch.setattr(net_mod, "NetworkManager", RecordingNM)

    args = types.SimpleNamespace(action="ensure")
    net_mod.cmd_network(args, runner=runner)

    out = capsys.readouterr().out
    assert "Ensuring required Podman networks exist" in out
    assert "Networks ensured" in out
    assert made and made[0].ensured is True, "ensure_networks_exist was never called"


def test_cmd_network_ensure_failure_raises(monkeypatch):
    # F2: nothing executed the NetworkError arm — deleting the raise survived.
    class FailingNM(FakeNM):
        def ensure_networks_exist(self):
            self.ensured = True
            return False

    monkeypatch.setattr(net_mod, "NetworkManager", FailingNM)
    args = types.SimpleNamespace(action="ensure")
    import pytest

    with pytest.raises(Exception) as exc:
        net_mod.cmd_network(args, runner=type("FakeRunner", (), {})())
    assert "network" in type(exc.value).__name__.lower() or "network" in str(exc.value).lower()


def test_cmd_network_status_prints_backend(monkeypatch, capsys):
    class FakeNM2:
        def __init__(self, runner):
            pass

        def check_netavark(self):
            return (False, "cni")

    runner = type("FakeRunner", (), {})()
    monkeypatch.setattr(net_mod, "NetworkManager", FakeNM2)

    args = types.SimpleNamespace(action=None)
    net_mod.cmd_network(args, runner=runner)

    out = capsys.readouterr().out
    assert "Backend: cni" in out
    assert "(recommended)" not in out  # F3: netavark must not be claimed here


def test_cmd_network_status_recommends_netavark(monkeypatch, capsys):
    # F3: the (True, 'netavark') arm was never executed by any test.
    class NMNetavark:
        def __init__(self, runner):
            pass

        def check_netavark(self):
            return (True, "netavark")

    monkeypatch.setattr(net_mod, "NetworkManager", NMNetavark)
    args = types.SimpleNamespace(action=None)
    net_mod.cmd_network(args, runner=type("FakeRunner", (), {})())
    out = capsys.readouterr().out
    assert "Backend: netavark" in out
    assert "(recommended)" in out
