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
    runner = type("FakeRunner", (), {})()
    monkeypatch.setattr(net_mod, "NetworkManager", FakeNM)
    monkeypatch.setattr(net_mod, "Runner", lambda: runner)

    args = types.SimpleNamespace(action="ensure")
    net_mod.cmd_network(args, runner=runner)

    out = capsys.readouterr().out
    assert "Ensuring required Podman networks exist" in out
    assert "Networks ensured" in out


def test_cmd_network_status_prints_backend(monkeypatch, capsys):
    class FakeNM2:
        def __init__(self, runner):
            pass

        def check_netavark(self):
            return (False, "cni")

    runner = type("FakeRunner", (), {})()
    monkeypatch.setattr(net_mod, "NetworkManager", FakeNM2)
    monkeypatch.setattr(net_mod, "Runner", lambda: runner)

    args = types.SimpleNamespace(action=None)
    net_mod.cmd_network(args, runner=runner)

    out = capsys.readouterr().out
    assert "Backend: cni" in out
