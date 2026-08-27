import importlib.util
import types
from pathlib import Path


def load_visp_module():
    # Ensure project root is on sys.path so `vispctl` can be imported
    import sys

    proj = str(Path.cwd())
    if proj not in sys.path:
        sys.path.insert(0, proj)

    spec = importlib.util.spec_from_file_location("vp", str(Path.cwd() / "visp.py"))
    vp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vp)
    return vp


def test_cmd_debug_shows_quadlet_link(tmp_path, capsys, monkeypatch):
    vp = load_visp_module()

    runner = vp.Runner()
    runner._run = lambda *a, **kw: (1, "", "")
    vp.init_config(runner=runner, systemd_dir=tmp_path / "systemd", project_dir=tmp_path)

    quad_dir = tmp_path / "quadlets" / "dev"
    quad_dir.mkdir(parents=True)
    (tmp_path / "systemd").mkdir(parents=True)

    svc_file = "session-manager.container"
    source = quad_dir / svc_file
    source.write_text("content")
    target = tmp_path / "systemd" / svc_file
    target.write_text("content")

    (tmp_path / ".visp-mode").write_text("dev")

    args = types.SimpleNamespace(service="session-manager")
    vp.cmd_debug(args)

    out = capsys.readouterr().out
    assert "Quadlet File:" in out
    assert str(target) in out
