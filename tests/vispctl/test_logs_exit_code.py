"""'visp logs' must not report success when journalctl failed (TL-R2-03)."""

import importlib.util
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

proj = str(Path(__file__).resolve().parents[2])
if proj not in sys.path:
    sys.path.insert(0, proj)

from vispctl.logs import view_logs
from vispctl.runner import Runner
from vispctl.service import Service


def _args(service=None, follow=False):
    return types.SimpleNamespace(
        service=service, follow=follow, lines=None, since=None,
        priority=None, journal_only=True, debug=False, no_follow=True,
    )


def _runner_with(rc):
    r = MagicMock(spec=Runner)
    r.journalctl.return_value = subprocess.CompletedProcess(["journalctl"], rc, "", "")
    return r


def _view(runner, service=None):
    return view_logs(
        _args(service),
        runner=runner,
        container_log_files={},
        systemd_dir=Path("/tmp"),
        get_runtime_services=lambda: [Service("mongo", "container", "mongo.container")],
        get_all_services=lambda: [],
        resolve_services=lambda s: [s],
        container_services=lambda svcs: svcs,
    )


def test_failing_journalctl_for_one_service_is_reported():
    assert _view(_runner_with(1), "mongo") == ["mongo"]


def test_failing_journalctl_for_all_is_reported():
    assert _view(_runner_with(1)) == ["all"]


def test_healthy_journalctl_reports_nothing():
    assert _view(_runner_with(0), "mongo") == []


def _load_visp():
    spec = importlib.util.spec_from_file_location("vp", str(Path(proj) / "visp.py"))
    vp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vp)
    return vp


def test_cmd_logs_exits_1_on_failure(monkeypatch):
    vp = _load_visp()
    monkeypatch.setattr(vp, "get_config", lambda: types.SimpleNamespace(
        runner=_runner_with(1), systemd_dir=Path("/tmp"), project_dir=Path("/tmp")))
    monkeypatch.setattr(vp, "view_logs", lambda *a, **k: ["mongo"])
    with pytest.raises(SystemExit) as exc:
        vp.cmd_logs(_args("mongo"))
    assert exc.value.code == 1
