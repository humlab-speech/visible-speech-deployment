"""Tests for cmd_uninstall in visp.py (autostart drop-in cleanup)."""

import importlib.util
import sys
import types
from pathlib import Path

import vispctl.secrets as secrets_mod
from vispctl.service import Service


def load_visp_module():
    proj = str(Path.cwd())
    if proj not in sys.path:
        sys.path.insert(0, proj)
    spec = importlib.util.spec_from_file_location("vp", str(Path.cwd() / "visp.py"))
    vp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vp)
    return vp


def _setup(tmp_path, monkeypatch, vp, with_dropin=True, extra_dropin_file=False):
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    (systemd_dir / "mongo.container").write_text("[Unit]\n")
    if with_dropin:
        dropin_dir = systemd_dir / "mongo.container.d"
        dropin_dir.mkdir()
        (dropin_dir / "90-visp-autostart.conf").write_text("[Install]\nWantedBy=\n")
        if extra_dropin_file:
            (dropin_dir / "custom.conf").write_text("[Service]\n")

    runner = vp.Runner()
    vp.init_config(runner=runner, systemd_dir=systemd_dir, project_dir=tmp_path)

    svc = Service("mongo", "container", "mongo.container")
    monkeypatch.setattr(vp, "resolve_services", lambda *a, **kw: [svc])
    monkeypatch.setattr(vp, "get_current_mode", lambda: "dev")
    monkeypatch.setattr(secrets_mod.SecretManager, "list_secrets", lambda self: [])

    return systemd_dir


def test_cmd_uninstall_removes_autostart_dropin(tmp_path, monkeypatch, capsys):
    vp = load_visp_module()
    systemd_dir = _setup(tmp_path, monkeypatch, vp)

    args = types.SimpleNamespace(service="mongo", keep_running=True, remove_networks=False)
    vp.cmd_uninstall(args)

    assert not (systemd_dir / "mongo.container").exists()
    assert not (systemd_dir / "mongo.container.d").exists()
    out = capsys.readouterr().out
    assert "90-visp-autostart.conf: removed" in out


def test_cmd_uninstall_keeps_nonempty_dropin_dir(tmp_path, monkeypatch, capsys):
    vp = load_visp_module()
    systemd_dir = _setup(tmp_path, monkeypatch, vp, extra_dropin_file=True)

    args = types.SimpleNamespace(service="mongo", keep_running=True, remove_networks=False)
    vp.cmd_uninstall(args)

    dropin_dir = systemd_dir / "mongo.container.d"
    assert not (dropin_dir / "90-visp-autostart.conf").exists()
    assert dropin_dir.exists()  # still contains the user's custom.conf
    assert (dropin_dir / "custom.conf").exists()


def test_cmd_uninstall_without_dropin(tmp_path, monkeypatch, capsys):
    vp = load_visp_module()
    systemd_dir = _setup(tmp_path, monkeypatch, vp, with_dropin=False)

    args = types.SimpleNamespace(service="mongo", keep_running=True, remove_networks=False)
    vp.cmd_uninstall(args)

    assert not (systemd_dir / "mongo.container").exists()
    out = capsys.readouterr().out
    assert "90-visp-autostart.conf" not in out


def test_cmd_uninstall_all_removes_every_secret(tmp_path, monkeypatch, capsys):
    """'uninstall all' must remove every existing secret (remove_all wiring)."""
    vp = load_visp_module()
    _setup(tmp_path, monkeypatch, vp)

    removed: list = []
    monkeypatch.setattr(secrets_mod.SecretManager, "list_secrets", lambda self: ["visp_a", "visp_b"])
    monkeypatch.setattr(secrets_mod.SecretManager, "remove_secrets", lambda self, names: removed.extend(names))

    args = types.SimpleNamespace(service="all", keep_running=True, remove_networks=False)
    vp.cmd_uninstall(args)

    assert sorted(removed) == ["visp_a", "visp_b"]
    out = capsys.readouterr().out
    assert "Kept" not in out
