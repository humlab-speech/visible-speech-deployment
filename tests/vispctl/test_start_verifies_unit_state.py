"""'Started' requires the unit to actually be up, not just a 0-rc start request.

Round-2 (test_install unit) reproduced live: a crash-looping quadlet gets
'systemctl --user start' rc 0, prints Started, and the command exits 0.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

proj = str(Path(__file__).resolve().parents[2])
if proj not in sys.path:
    sys.path.insert(0, proj)

from vispctl.service import Service  # noqa: E402
from vispctl.service_manager import ServiceManager  # noqa: E402


class FakeRunner:
    def __init__(self, start_rc=0, becomes_active=False):
        self.start_rc = start_rc
        self.becomes_active = becomes_active
        self.calls = []

    def systemctl(self, action, unit, check=False):
        self.calls.append((action, unit))
        return types.SimpleNamespace(returncode=self.start_rc, stdout="", stderr="boom" if self.start_rc else "")

    def unit_is_active(self, name):
        return self.becomes_active

    def run_quiet(self, cmd):
        return (0, "", "")


def _svc():
    return Service("demo", "container", "demo.container")


def test_start_reports_unit_that_never_came_up():
    sm = ServiceManager(FakeRunner(start_rc=0, becomes_active=False), [_svc()])
    sm._resolve_targets = lambda names: [_svc()]
    failed = sm.start("demo")
    assert failed == ["demo"]


def test_start_accepts_unit_that_came_up():
    sm = ServiceManager(FakeRunner(start_rc=1 if False else 0, becomes_active=True), [_svc()])
    sm._resolve_targets = lambda names: [_svc()]
    assert sm.start("demo") == []


def test_start_reports_nonzero_request():
    sm = ServiceManager(FakeRunner(start_rc=1, becomes_active=False), [_svc()])
    sm._resolve_targets = lambda names: [_svc()]
    assert sm.start("demo") == ["demo"]


def _load_visp():
    spec = importlib.util.spec_from_file_location("vp", str(Path(proj) / "visp.py"))
    vp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vp)
    return vp


def test_cmd_start_exits_nonzero_on_failure(monkeypatch):
    vp = _load_visp()
    cfg = types.SimpleNamespace(runner=FakeRunner(becomes_active=False))
    monkeypatch.setattr(vp, "get_config", lambda: cfg)
    monkeypatch.setattr(vp, "_resolve_service_names", lambda a, c, **kw: ["demo"])
    monkeypatch.setattr(vp, "get_runtime_services", lambda include_disabled=False: [_svc()])
    monkeypatch.setattr(vp, "ServiceManager", lambda runner, services: _FailingSM())
    with pytest.raises(SystemExit) as exc:
        vp.cmd_start(types.SimpleNamespace(service="demo", services=None))
    assert exc.value.code == 1


class _FailingSM:
    def __init__(self, *a):
        pass

    def start(self, names):
        return ["demo"]
