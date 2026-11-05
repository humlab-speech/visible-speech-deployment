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

from vispctl.logs import view_logs  # noqa: E402
from vispctl.runner import Runner  # noqa: E402
from vispctl.service import Service  # noqa: E402


def _args(service=None, follow=False):
    return types.SimpleNamespace(
        service=service,
        follow=follow,
        lines=None,
        since=None,
        priority=None,
        journal_only=True,
        debug=False,
        no_follow=True,
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
    monkeypatch.setattr(
        vp,
        "get_config",
        lambda: types.SimpleNamespace(runner=_runner_with(1), systemd_dir=Path("/tmp"), project_dir=Path("/tmp")),
    )
    monkeypatch.setattr(vp, "view_logs", lambda *a, **k: ["mongo"])
    with pytest.raises(SystemExit) as exc:
        vp.cmd_logs(_args("mongo"))
    assert exc.value.code == 1


def _view_podman(service, *, follow=False, no_follow=True):
    """view_logs through the podman-stream shortcut (container, no debug)."""
    runner = MagicMock(spec=Runner)
    args = types.SimpleNamespace(
        service=service,
        follow=follow,
        lines=None,
        since=None,
        priority=None,
        journal_only=False,
        debug=False,
        no_follow=no_follow,
    )
    return view_logs(
        args,
        runner=runner,
        container_log_files={},
        systemd_dir=Path("/tmp"),
        get_runtime_services=lambda: [Service("mongo", "container", "mongo.container")],
        get_all_services=lambda: [],
        resolve_services=lambda s: [s],
        container_services=lambda svcs: svcs,
    )


def test_follow_mode_podman_rc_is_never_a_failure(monkeypatch):
    # H12: `visp logs mongo` follows by default; a nonzero podman exit after
    # the stream ends (e.g. 125) must NOT be counted as failure. Flipping
    # 'if not follow and rc' to 'if rc' used to keep the whole suite green
    # while turning the default logs path into exit 1.
    import vispctl.logs as log_mod

    monkeypatch.setattr(log_mod, "stream_podman_logs", lambda service, **kw: 125)
    assert _view_podman("mongo", no_follow=False) == []


def test_snapshot_mode_podman_rc_is_a_failure(monkeypatch):
    # The other arm of the same policy: --no-follow must still fail loudly.
    import vispctl.logs as log_mod

    monkeypatch.setattr(log_mod, "stream_podman_logs", lambda service, **kw: 125)
    assert _view_podman("mongo", no_follow=True) == ["mongo"]
