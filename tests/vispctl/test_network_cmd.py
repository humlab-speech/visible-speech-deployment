import importlib.util
from pathlib import Path
import types


def load_visp_module():
    import sys

    proj = str(Path.cwd())
    if proj not in sys.path:
        sys.path.insert(0, proj)

    spec = importlib.util.spec_from_file_location(
        "vp", str(Path.cwd() / "visp-podman.py")
    )
    vp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vp)
    return vp


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
    vp = load_visp_module()

    # Monkeypatch NetworkManager to our fake
    monkeypatch.setattr(vp, "NetworkManager", FakeNM)

    args = types.SimpleNamespace(action="ensure")
    vp.cmd_network(args)

    out = capsys.readouterr().out
    assert "Ensuring required Podman networks exist" in out
    assert "Networks ensured" in out


def test_cmd_network_status_prints_backend(monkeypatch, capsys):
    vp = load_visp_module()

    class FakeNM2:
        def __init__(self, runner):
            pass

        def check_netavark(self):
            return (False, "cni")

    monkeypatch.setattr(vp, "NetworkManager", FakeNM2)

    args = types.SimpleNamespace(action=None)
    vp.cmd_network(args)

    out = capsys.readouterr().out
    assert "Backend: cni" in out
