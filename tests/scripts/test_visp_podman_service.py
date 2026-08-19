import importlib.util
import sys
import types
from pathlib import Path

import pytest

proj = str(Path(__file__).resolve().parents[2])
if proj not in sys.path:
    sys.path.insert(0, proj)


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

    args = types.SimpleNamespace(services=["session-manager"])
    vp.cmd_start(args)
    assert called.get("start") and called["start"][0] == ["session-manager"]


def test_cmd_up_enables_then_starts(monkeypatch):
    vp = load_visp_module()
    called = {}

    class FakeSM:
        def __init__(self, runner, services):
            called["init"] = True

        def enable(self, names):
            called.setdefault("ops", []).append(("enable", names))

        def start(self, names):
            called.setdefault("ops", []).append(("start", names))

    monkeypatch.setattr(vp, "ServiceManager", FakeSM)

    args = types.SimpleNamespace(services=["session-manager"])
    vp.cmd_up(args)

    assert called.get("ops") == [
        ("enable", ["session-manager"]),
        ("start", ["session-manager"]),
    ]


def test_cmd_down_stops_then_disables(monkeypatch):
    vp = load_visp_module()
    called = {}

    class FakeSM:
        def __init__(self, runner, services):
            called["init"] = True

        def stop(self, names):
            called.setdefault("ops", []).append(("stop", names))

        def disable(self, names):
            called.setdefault("ops", []).append(("disable", names))

    monkeypatch.setattr(vp, "ServiceManager", FakeSM)

    args = types.SimpleNamespace(services=["session-manager"])
    vp.cmd_down(args)

    assert called.get("ops") == [
        ("stop", ["session-manager"]),
        ("disable", ["session-manager"]),
    ]


def test_cmd_down_all_skips_dev_only_services_in_prod(monkeypatch):
    vp = load_visp_module()
    called = {}

    class FakeSM:
        def __init__(self, runner, services):
            called["services"] = [service.name for service in services]

        def stop(self, names):
            called.setdefault("ops", []).append(("stop", names))

        def disable(self, names):
            called.setdefault("ops", []).append(("disable", names))

    monkeypatch.setattr(vp, "ServiceManager", FakeSM)

    import vispctl.env as env_mod
    import vispctl.quadlets as q_mod

    monkeypatch.setattr(q_mod, "get_current_mode", lambda: "prod")
    monkeypatch.setattr(env_mod, "load_env_file", lambda _: {})

    args = types.SimpleNamespace(services=["all"])
    vp.cmd_down(args)

    assert "local-idp" not in called["services"]
    assert "mongo-express" not in called["services"]
    for _, targets in called["ops"]:
        assert "local-idp" not in targets
        assert "mongo-express" not in targets
        assert "session-manager" in targets


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

    args = types.SimpleNamespace(services=["all"])
    vp.cmd_restart(args)
    assert called.get("stop")
    stop_targets = called["stop"][0]
    assert isinstance(stop_targets, list)
    assert "session-manager" in stop_targets
    assert called.get("start")
    start_targets = called["start"][0]
    assert isinstance(start_targets, list)
    assert "session-manager" in start_targets
    # Networks are passed through to ServiceManager, which skips them with a note
    # (their units come up via Requires= from the containers).
    assert "visp-net" in stop_targets
    assert "visp-net" in start_targets


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

    import vispctl.env as env_mod

    monkeypatch.setattr(env_mod, "load_env_file", lambda _: {"WHISPERX_ENABLED": "false"})

    args = types.SimpleNamespace(services=["all"])
    vp.cmd_restart(args)

    assert called.get("stop")
    stop_targets = called["stop"][0]
    assert isinstance(stop_targets, list)
    assert "session-manager" in stop_targets
    assert called.get("start")
    start_targets = called["start"][0]
    assert "session-manager" in start_targets
    assert "whisperx" not in start_targets


def test_resolve_services_reports_disabled_optional_service(monkeypatch):
    import vispctl.env as env_mod
    import vispctl.quadlets as q_mod
    from vispctl.config import get_config, init_config
    from vispctl.exceptions import ServiceError

    init_config()
    monkeypatch.setattr(env_mod, "load_env_file", lambda _: {"WHISPERX_ENABLED": "false"})
    monkeypatch.setattr(q_mod, "get_current_mode", lambda: "dev")

    from vispctl.service import resolve_services

    with pytest.raises(ServiceError, match="WHISPERX_ENABLED=true"):
        resolve_services("whisperx", get_config().project_dir)


def test_resolve_services_reports_disabled_local_idp(monkeypatch):
    import vispctl.env as env_mod
    import vispctl.quadlets as q_mod
    from vispctl.config import get_config, init_config
    from vispctl.exceptions import ServiceError

    init_config()
    monkeypatch.setattr(env_mod, "load_env_file", lambda _: {"LOCAL_IDP_ENABLED": "false"})
    monkeypatch.setattr(q_mod, "get_current_mode", lambda: "dev")

    from vispctl.service import resolve_services

    with pytest.raises(ServiceError, match="LOCAL_IDP_ENABLED=true"):
        resolve_services("local-idp", get_config().project_dir)


def test_get_runtime_services_includes_mongo_express_in_dev(monkeypatch):
    import vispctl.env as env_mod
    import vispctl.quadlets as q_mod

    load_visp_module()
    monkeypatch.setattr(q_mod, "get_current_mode", lambda: "dev")
    monkeypatch.setattr(env_mod, "load_env_file", lambda _: {})

    from vispctl.service import get_runtime_services

    service_names = [service.name for service in get_runtime_services()]
    assert "mongo-express" in service_names


def test_get_runtime_services_excludes_mongo_express_in_prod(monkeypatch):
    import vispctl.env as env_mod
    import vispctl.quadlets as q_mod

    load_visp_module()
    monkeypatch.setattr(q_mod, "get_current_mode", lambda: "prod")
    monkeypatch.setattr(env_mod, "load_env_file", lambda _: {})

    from vispctl.service import get_runtime_services

    service_names = [service.name for service in get_runtime_services()]
    assert "mongo-express" not in service_names
