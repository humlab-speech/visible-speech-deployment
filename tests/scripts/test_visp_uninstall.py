"""Tests for cmd_uninstall in visp.py (autostart drop-in cleanup + stale unit removal)."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

import vispctl.secrets as secrets_mod
import vispctl.service_manager as service_manager_mod
from vispctl.service import Service


def load_visp_module():
    proj = str(Path.cwd())
    if proj not in sys.path:
        sys.path.insert(0, proj)
    spec = importlib.util.spec_from_file_location("vp", str(Path.cwd() / "visp.py"))
    vp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vp)
    return vp


class FakeRunner:
    def __init__(self):
        self.systemctl_calls = []

    def systemctl(self, *args, **kwargs):
        self.systemctl_calls.append(args)

        class R:
            returncode = 0
            stderr = ""
            stdout = ""

        return R()

    def run_quiet(self, cmd):
        return 0, "inactive", ""

    def unit_is_active(self, unit):
        return False


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

    runner = FakeRunner()
    vp.init_config(runner=runner, systemd_dir=systemd_dir, project_dir=tmp_path)

    svc = Service("mongo", "container", "mongo.container")
    monkeypatch.setattr(vp, "resolve_services", lambda *a, **kw: [svc])
    monkeypatch.setattr(vp, "get_current_mode", lambda: "dev")
    monkeypatch.setattr(secrets_mod.SecretManager, "list_secrets", lambda self: [])
    monkeypatch.setattr(service_manager_mod.time, "sleep", lambda s: None)

    return systemd_dir, runner


def _add_stale_octra(systemd_dir, project_dir):
    """Simulate units rendered by an older repo version (before the OCTRA→TRATT rename)."""
    (systemd_dir / "octra.container").write_text(
        "[Container]\n"
        "ContainerName=octra\n"
        "Image=localhost/visp-octra:latest\n"
        "Network=octra-net.network\n"
        f"Volume={project_dir}/mounts/octra/appconfig.json:"
        "/usr/local/apache2/htdocs/config/appconfig.json:ro,Z\n"
    )
    (systemd_dir / "octra-net.network").write_text("[Network]\nInternal=true\n")


def test_cmd_uninstall_removes_autostart_dropin(tmp_path, monkeypatch, capsys):
    vp = load_visp_module()
    systemd_dir, _ = _setup(tmp_path, monkeypatch, vp)

    args = types.SimpleNamespace(service="mongo", keep_running=True, remove_networks=False)
    vp.cmd_uninstall(args)

    assert not (systemd_dir / "mongo.container").exists()
    assert not (systemd_dir / "mongo.container.d").exists()
    out = capsys.readouterr().out
    assert "90-visp-autostart.conf: removed" in out


def test_cmd_uninstall_keeps_nonempty_dropin_dir(tmp_path, monkeypatch, capsys):
    vp = load_visp_module()
    systemd_dir, _ = _setup(tmp_path, monkeypatch, vp, extra_dropin_file=True)

    args = types.SimpleNamespace(service="mongo", keep_running=True, remove_networks=False)
    vp.cmd_uninstall(args)

    dropin_dir = systemd_dir / "mongo.container.d"
    assert not (dropin_dir / "90-visp-autostart.conf").exists()
    assert dropin_dir.exists()  # still contains the user's custom.conf
    assert (dropin_dir / "custom.conf").exists()


def test_cmd_uninstall_without_dropin(tmp_path, monkeypatch, capsys):
    vp = load_visp_module()
    systemd_dir, _ = _setup(tmp_path, monkeypatch, vp, with_dropin=False)

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

    args = types.SimpleNamespace(service="all", keep_running=True, remove_networks=False, force=True)
    vp.cmd_uninstall(args)

    assert sorted(removed) == ["visp_a", "visp_b"]
    out = capsys.readouterr().out
    assert "Kept" not in out


def test_cmd_uninstall_all_requires_confirmation(tmp_path, monkeypatch, capsys):
    """'uninstall all' without --force must abort non-interactively (EOF)."""
    vp = load_visp_module()
    systemd_dir, _ = _setup(tmp_path, monkeypatch, vp)

    def _eof(*a):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    args = types.SimpleNamespace(service="all", keep_running=True, remove_networks=False, force=False)

    with pytest.raises(SystemExit) as exc:
        vp.cmd_uninstall(args)
    assert exc.value.code == 1
    assert (systemd_dir / "mongo.container").exists()  # nothing removed
    assert "non-interactive" in capsys.readouterr().out


def test_cmd_uninstall_all_removes_stale_units(tmp_path, monkeypatch, capsys):
    """'uninstall all' removes VISP units no longer in the service registry (renamed services)."""
    vp = load_visp_module()
    systemd_dir, _ = _setup(tmp_path, monkeypatch, vp)
    _add_stale_octra(systemd_dir, tmp_path)

    args = types.SimpleNamespace(service="all", keep_running=True, remove_networks=False, force=True)
    vp.cmd_uninstall(args)

    assert not (systemd_dir / "octra.container").exists()
    assert not (systemd_dir / "octra-net.network").exists()
    out = capsys.readouterr().out
    assert "stale" in out


def test_cmd_uninstall_all_removes_stale_units_from_old_repo_path(tmp_path, monkeypatch, capsys):
    """Units rendered from an older checkout location are still VISP units.

    Regression test for the emu-webapp incident: the quadlet embedded the old
    repo path, so the path check alone missed it and the unit respawned its
    container after manual removal. The localhost/visp-* image convention is
    the path-independent marker.
    """
    vp = load_visp_module()
    systemd_dir, _ = _setup(tmp_path, monkeypatch, vp)
    (systemd_dir / "emu-webapp.container").write_text(
        "[Container]\n"
        "ContainerName=emu-webapp\n"
        "Image=localhost/visp-emu-webapp:latest\n"
        "Volume=/old/repo/location/mounts/emu-webapp/httpd.conf:/usr/local/apache2/conf/httpd.conf:ro,Z\n"
    )

    args = types.SimpleNamespace(service="all", keep_running=True, remove_networks=False, force=True)
    vp.cmd_uninstall(args)

    assert not (systemd_dir / "emu-webapp.container").exists()
    assert "stale" in capsys.readouterr().out


def test_cmd_uninstall_all_keeps_user_quadlets(tmp_path, monkeypatch, capsys):
    """User-owned quadlets in the shared systemd dir are never touched."""
    vp = load_visp_module()
    systemd_dir, _ = _setup(tmp_path, monkeypatch, vp)
    (systemd_dir / "myapp.container").write_text("[Container]\nImage=docker.io/library/nginx:latest\n")

    args = types.SimpleNamespace(service="all", keep_running=True, remove_networks=False, force=True)
    vp.cmd_uninstall(args)

    assert (systemd_dir / "myapp.container").exists()


def test_cmd_uninstall_single_service_keeps_stale_units(tmp_path, monkeypatch, capsys):
    """The stale sweep only runs for 'uninstall all', not single-service uninstalls."""
    vp = load_visp_module()
    systemd_dir, _ = _setup(tmp_path, monkeypatch, vp)
    _add_stale_octra(systemd_dir, tmp_path)

    args = types.SimpleNamespace(service="mongo", keep_running=True, remove_networks=False)
    vp.cmd_uninstall(args)

    assert (systemd_dir / "octra.container").exists()
    assert (systemd_dir / "octra-net.network").exists()


def test_cmd_uninstall_all_stops_stale_units(tmp_path, monkeypatch, capsys):
    """Stale units are stopped before their quadlet files are removed."""
    vp = load_visp_module()
    systemd_dir, runner = _setup(tmp_path, monkeypatch, vp)
    _add_stale_octra(systemd_dir, tmp_path)

    args = types.SimpleNamespace(service="all", keep_running=False, remove_networks=False, force=True)
    vp.cmd_uninstall(args)

    stops = [c for c in runner.systemctl_calls if c and c[0] == "stop"]
    assert ("stop", "octra.service") in stops
    assert ("stop", "octra-net-network.service") in stops
