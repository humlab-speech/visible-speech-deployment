"""Tests for cmd_apply and cmd_status in visp.py."""

import importlib.util
import sys
import types
from pathlib import Path

import vispctl.runner as r_mod


def load_visp_module():
    proj = str(Path.cwd())
    if proj not in sys.path:
        sys.path.insert(0, proj)
    spec = importlib.util.spec_from_file_location("vp", str(Path.cwd() / "visp.py"))
    vp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vp)
    return vp


# ── cmd_status ─────────────────────────────────────────────────────────────────


def test_cmd_status_outputs_service_header(tmp_path, monkeypatch, capsys):
    vp = load_visp_module()

    class FakeSM:
        def __init__(self, runner, services):
            pass

        def status(self):
            pass

    runner = vp.Runner()
    runner._run = lambda *a, **kw: None
    vp.init_config(runner=runner, systemd_dir=tmp_path / "systemd", project_dir=tmp_path)
    (tmp_path / "systemd").mkdir(exist_ok=True)

    monkeypatch.setattr(vp, "ServiceManager", FakeSM)
    monkeypatch.setattr(r_mod, "load_env_vars", lambda _: {})
    monkeypatch.setattr(vp, "get_current_mode", lambda: "dev")
    monkeypatch.setattr(vp, "render_quadlet_template", lambda t: t)

    args = types.SimpleNamespace()
    vp.cmd_status(args)

    out = capsys.readouterr().out
    assert "VISP Service Status" in out
    assert "Quadlet Links" in out


def test_cmd_status_shows_disabled_optional_service(tmp_path, monkeypatch, capsys):
    vp = load_visp_module()

    class FakeSM:
        def __init__(self, runner, services):
            pass

        def status(self):
            pass

    runner = vp.Runner()
    runner._run = lambda *a, **kw: None
    vp.init_config(runner=runner, systemd_dir=tmp_path / "systemd", project_dir=tmp_path)
    (tmp_path / "systemd").mkdir(exist_ok=True)

    monkeypatch.setattr(vp, "ServiceManager", FakeSM)
    monkeypatch.setattr(r_mod, "load_env_vars", lambda _: {"WHISPERX_ENABLED": "false"})
    monkeypatch.setattr(vp, "get_current_mode", lambda: "dev")
    monkeypatch.setattr(vp, "render_quadlet_template", lambda t: t)

    args = types.SimpleNamespace()
    vp.cmd_status(args)

    out = capsys.readouterr().out
    assert "Disabled Optional Services" in out
    assert "whisperx" in out


# ── cmd_apply ──────────────────────────────────────────────────────────────────


def test_cmd_apply_nothing_to_do(tmp_path, monkeypatch, capsys):
    vp = load_visp_module()
    monkeypatch.setattr(vp, "get_current_mode", lambda: "dev")

    from vispctl import quadlets as q_mod

    monkeypatch.setattr(q_mod, "get_quadlet_drift", lambda *a, **kw: ([], []))

    runner = vp.Runner()
    runner._run = lambda *a, **kw: None
    vp.init_config(runner=runner, systemd_dir=tmp_path / "systemd", project_dir=tmp_path)

    args = types.SimpleNamespace(service="all")
    vp.cmd_apply(args)

    out = capsys.readouterr().out
    assert "up to date" in out.lower() or "nothing to apply" in out.lower()


def test_cmd_apply_installs_drifted_and_restarts(tmp_path, monkeypatch, capsys):
    vp = load_visp_module()
    monkeypatch.setattr(vp, "get_current_mode", lambda: "dev")

    from vispctl.service import Service

    drifted_svc = Service("mongo", "container", "mongo.container")

    quad_dir = tmp_path / "quadlets" / "dev"
    quad_dir.mkdir(parents=True)
    (quad_dir / "mongo.container").write_text("new content")
    monkeypatch.setattr(vp, "get_quadlets_dir", lambda m=None: quad_dir)
    (tmp_path / "systemd").mkdir(exist_ok=True)

    from vispctl import quadlets as q_mod

    monkeypatch.setattr(q_mod, "get_quadlet_drift", lambda *a, **kw: ([drifted_svc], []))

    import vispctl.service as svc_mod

    monkeypatch.setattr(svc_mod, "resolve_services", lambda s, *a, **kw: [drifted_svc])
    monkeypatch.setattr(vp, "render_quadlet_template", lambda t: t)

    restarted = {}

    class FakeSM:
        def __init__(self, runner, services):
            pass

        def stop(self, names):
            restarted["stop"] = names

        def start(self, names):
            restarted["start"] = names

    monkeypatch.setattr(vp, "ServiceManager", FakeSM)

    runner = vp.Runner()
    runner._run = lambda *a, **kw: None
    vp.init_config(
        runner=runner,
        systemd_dir=tmp_path / "systemd",
        project_dir=tmp_path,
        build_configs=vp.BUILD_CONFIGS,
        node_configs=vp.NODE_BUILD_CONFIGS,
        all_buildable=vp.ALL_BUILDABLE,
        network_services=vp.NETWORK_SERVICES,
    )
    monkeypatch.setattr(runner, "systemctl", lambda *a, **kw: types.SimpleNamespace(returncode=0, stderr=""))

    args = types.SimpleNamespace(service="all")
    vp.cmd_apply(args)

    out = capsys.readouterr().out
    assert "mongo.container" in out
    assert restarted.get("start") == ["mongo"]
