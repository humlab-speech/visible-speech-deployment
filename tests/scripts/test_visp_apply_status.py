"""Tests for cmd_apply and cmd_status in visp.py."""

import importlib.util
import sys
import types
from pathlib import Path

import vispctl.env as env_mod


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
    monkeypatch.setattr(env_mod, "load_env_file", lambda _: {})
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
    monkeypatch.setattr(env_mod, "load_env_file", lambda _: {"WHISPERX_ENABLED": "false"})
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
    import vispctl.images as img_mod
    import vispctl.service as svc_mod
    import vispctl.service_manager as sm_mod
    from vispctl import quadlets as q_mod
    from vispctl.build import BUILD_CONFIGS, NODE_BUILD_CONFIGS
    from vispctl.config import init_config
    from vispctl.service import Service

    drifted_svc = Service("mongo", "container", "mongo.container")

    quad_dir = tmp_path / "quadlets" / "dev"
    quad_dir.mkdir(parents=True)
    (quad_dir / "mongo.container").write_text("new content")
    monkeypatch.setattr(q_mod, "get_quadlets_dir", lambda m=None: quad_dir)
    (tmp_path / "systemd").mkdir(exist_ok=True)

    monkeypatch.setattr(q_mod, "get_current_mode", lambda: "dev")
    monkeypatch.setattr(q_mod, "get_quadlet_drift", lambda *a, **kw: ([drifted_svc], []))
    monkeypatch.setattr(q_mod, "render_quadlet_template", lambda t: t)
    monkeypatch.setattr(svc_mod, "resolve_services", lambda s, *a, **kw: [drifted_svc])
    monkeypatch.setattr(svc_mod, "get_runtime_services", lambda *a, **kw: [drifted_svc])

    restarted = {}

    class FakeSM:
        def __init__(self, runner, services):
            pass

        def stop(self, names):
            restarted["stop"] = names

        def start(self, names):
            restarted["start"] = names

    monkeypatch.setattr(sm_mod, "ServiceManager", FakeSM)
    monkeypatch.setattr(
        img_mod, "ImageManager", lambda *a, **kw: type("FakeIM", (), {"get_stale_containers": lambda self, s: []})()
    )

    runner = type(
        "FakeRunner", (), {"systemctl": lambda self, *a, **kw: types.SimpleNamespace(returncode=0, stderr="")}
    )()
    init_config(
        runner=runner,
        systemd_dir=tmp_path / "systemd",
        project_dir=tmp_path,
        build_configs=BUILD_CONFIGS,
        node_configs=NODE_BUILD_CONFIGS,
        network_services=[],
    )

    args = types.SimpleNamespace(service="all")
    q_mod.cmd_apply(
        args,
        project_dir=tmp_path,
        systemd_dir=tmp_path / "systemd",
        runner=runner,
        build_configs=BUILD_CONFIGS,
        network_services=[],
    )

    out = capsys.readouterr().out
    assert "mongo.container" in out
    assert restarted.get("start") == ["mongo"]
