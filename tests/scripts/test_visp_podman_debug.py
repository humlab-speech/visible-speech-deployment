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


def _stub_runner_methods(monkeypatch, vp, rc=0):
    """Stub the Runner methods that actually exist (there is no _run)."""
    import subprocess as _sp

    calls = []

    def fake_run(self, cmd, capture=False, check=True, **kw):
        calls.append(list(cmd))
        return _sp.CompletedProcess(list(cmd), rc, "", "")

    def fake_run_quiet(self, cmd):
        calls.append(list(cmd))
        return (rc, "", "")

    def fake_journalctl(self, *a, **kw):
        # journalctl is HEALTHY here: this file tests the debug panel, not
        # log-failure accounting (rc=1 here would make cmd_logs exit 1).
        calls.append(list(a))
        return _sp.CompletedProcess(list(a), 0, "", "")

    monkeypatch.setattr(vp.Runner, "run", fake_run)
    monkeypatch.setattr(vp.Runner, "run_quiet", fake_run_quiet)
    monkeypatch.setattr(vp.Runner, "journalctl", fake_journalctl)
    return calls


def test_cmd_debug_shows_quadlet_link(tmp_path, capsys, monkeypatch):
    vp = load_visp_module()

    runner = vp.Runner()
    calls = _stub_runner_methods(monkeypatch, vp, rc=1)
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
    assert "does not exist" not in out  # branch-specific: file IS present
    # Wave-7 BROKEN: nothing pinned WHICH container debug inspects — the
    # header alone survives any target mutation. Pin the argv correlation.
    joined = [" ".join(c) for c in calls]
    assert any("session-manager.service" in c for c in joined), (
        "diagnostics never targeted session-manager specifically"
    )


def test_cmd_debug_absent_quadlet_says_so(tmp_path, capsys, monkeypatch):
    """The 'does not exist' arm of the debug panel never ran in tests."""
    vp = load_visp_module()
    _calls = _stub_runner_methods(monkeypatch, vp, rc=1)
    runner = vp.Runner()
    vp.init_config(runner=runner, systemd_dir=tmp_path / "systemd", project_dir=tmp_path)
    (tmp_path / "systemd").mkdir(parents=True)
    (tmp_path / ".visp-mode").write_text("dev")

    vp.cmd_debug(types.SimpleNamespace(service="session-manager"))
    out = capsys.readouterr().out
    assert "does not exist" in out
    assert "(rendered template)" not in out
