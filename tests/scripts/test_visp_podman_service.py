import importlib.util
import types
from pathlib import Path

import pytest


def load_visp_module():
    spec = importlib.util.spec_from_file_location("vp", str(Path.cwd() / "visp.py"))
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
    assert called.get("start") and called["start"][0] == ["session-manager"]


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
    assert called.get("start")
    start_targets = called["start"][0]
    assert isinstance(start_targets, list)
    assert "session-manager" in start_targets
    assert "visp-net" not in start_targets


def test_cmd_restart_all_skips_disabled_whisperx(monkeypatch):
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
    monkeypatch.setattr(vp, "load_env_vars", lambda _: {"WHISPERX_ENABLED": "false"})

    args = types.SimpleNamespace(service="all")
    vp.cmd_restart(args)

    assert called.get("stop") and called["stop"][0] == "all"
    assert called.get("start")
    start_targets = called["start"][0]
    assert "session-manager" in start_targets
    assert "whisperx" not in start_targets


def test_resolve_services_reports_disabled_optional_service(monkeypatch, capsys):
    vp = load_visp_module()
    monkeypatch.setattr(vp, "load_env_vars", lambda _: {"WHISPERX_ENABLED": "false"})

    with pytest.raises(SystemExit):
        vp._resolve_services("whisperx")

    out = capsys.readouterr().out
    assert "disabled" in out
    assert "WHISPERX_ENABLED=true" in out


def test_resolve_services_reports_disabled_local_idp(monkeypatch, capsys):
    vp = load_visp_module()
    monkeypatch.setattr(vp, "load_env_vars", lambda _: {"LOCAL_IDP_ENABLED": "false"})

    with pytest.raises(SystemExit):
        vp._resolve_services("local-idp")

    out = capsys.readouterr().out
    assert "disabled" in out
    assert "LOCAL_IDP_ENABLED=true" in out


def test_get_runtime_services_includes_mongo_express_in_dev(monkeypatch):
    vp = load_visp_module()
    monkeypatch.setattr(vp, "get_current_mode", lambda: "dev")
    monkeypatch.setattr(vp, "load_env_vars", lambda _: {})

    service_names = [service.name for service in vp._get_runtime_services()]
    assert "mongo-express" in service_names


def test_get_runtime_services_excludes_mongo_express_in_prod(monkeypatch):
    vp = load_visp_module()
    monkeypatch.setattr(vp, "get_current_mode", lambda: "prod")
    monkeypatch.setattr(vp, "load_env_vars", lambda _: {})

    service_names = [service.name for service in vp._get_runtime_services()]
    assert "mongo-express" not in service_names
