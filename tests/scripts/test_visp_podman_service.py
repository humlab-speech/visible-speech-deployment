import types
import importlib.util
from pathlib import Path


def load_visp_module():
    spec = importlib.util.spec_from_file_location(
        "vp", str(Path.cwd() / "visp-podman.py")
    )
    vp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vp)
    return vp


def test_cmd_start_delegates_to_servicemanager(monkeypatch):
    vp = load_visp_module()
    called = {}

    class FakeSM:
        def __init__(self, runner, services):
            called["init"] = True

        def start(self, names):
            called.setdefault("start", []).append(names)

        def stop(self, names):
            called.setdefault("stop", []).append(names)

    monkeypatch.setattr(vp, "ServiceManager", FakeSM)

    args = types.SimpleNamespace(service="session-manager")
    vp.cmd_start(args)
    assert called.get("start") and "session-manager" in called["start"][0]


def test_cmd_restart_all_invokes_stop_then_start(monkeypatch):
    vp = load_visp_module()
    called = {}

    class FakeSM:
        def __init__(self, runner, services):
            called["init"] = True

        def start(self, names):
            called.setdefault("start", []).append(names)

        def stop(self, names):
            called.setdefault("stop", []).append(names)

    monkeypatch.setattr(vp, "ServiceManager", FakeSM)

    args = types.SimpleNamespace(service="all")
    vp.cmd_restart(args)
    assert called.get("stop") and called["stop"][0] == "all"
    assert called.get("start") and called["start"][0] == "all"
