import importlib.util
import types
from pathlib import Path


def load_visp_module():
    # Ensure project root is on sys.path so `vispctl` can be imported
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


def test_cmd_debug_shows_quadlet_link(tmp_path, capsys, monkeypatch):
    vp = load_visp_module()

    # Stub podman inspect to return not-found (so it prints container-not-found)
    monkeypatch.setattr(vp, "run_quiet", lambda cmd: (1, "", ""))

    # Replace SYSTEMD_QUADLETS_DIR with a temp dir and create a symlink
    sys_dir = tmp_path / "systemd"
    quad_dir = tmp_path / "quadlets" / "dev"
    quad_dir.mkdir(parents=True)
    sys_dir.mkdir(parents=True)

    svc_file = "session-manager.container"
    source = quad_dir / svc_file
    source.write_text("content")
    target = sys_dir / svc_file
    target.symlink_to(source)

    monkeypatch.setattr(vp, "SYSTEMD_QUADLETS_DIR", sys_dir)

    # Call cmd_debug
    args = types.SimpleNamespace(service="session-manager")
    vp.cmd_debug(args)

    out = capsys.readouterr().out
    assert "Quadlet Link:" in out
    # Symlink printed as 'link -> target'
    assert str(target) in out
