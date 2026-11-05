"""Scoped uninstall must not delete secrets that surviving INSTALLED units cite.

Round-2 audit (test_visp_uninstall unit) proved with real podman that a
user-authored quadlet referencing visp_mongo_uri did not protect that secret
when a repo service was uninstalled, because 'still needed' was computed from
repo templates only. These tests pin the helper AND the cmd_uninstall wiring.
"""

import importlib.util
import sys
import types
from pathlib import Path

proj = str(Path(__file__).resolve().parents[2])
if proj not in sys.path:
    sys.path.insert(0, proj)

from vispctl.secrets import filter_remove_keep  # noqa: E402


def load_visp_module():
    spec = importlib.util.spec_from_file_location("vp", str(Path.cwd() / "visp.py"))
    vp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vp)
    return vp


def test_installed_unit_protects_its_secret(tmp_path):
    sd = tmp_path / "systemd"
    sd.mkdir()
    (sd / "my-custom-app.container").write_text("[Container]\nImage=example\nSecret=visp_mongo_uri,type=mount\n")
    assert filter_remove_keep(["visp_mongo_uri", "visp_only_gone"], sd) == ["visp_only_gone"]


def test_empty_systemd_dir_protects_nothing(tmp_path):
    sd = tmp_path / "systemd"
    sd.mkdir()
    assert filter_remove_keep(["a", "b"], sd) == ["a", "b"]


def test_cmd_uninstall_wires_the_filter(tmp_path, monkeypatch, capsys):
    """The regression class this fixes hid in the CALL SITE, so pin the call site."""
    vp = load_visp_module()

    removed = []

    class FakeRunner:
        def systemctl(self, *a, **k):
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        def run(self, *a, **k):
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        def run_quiet(self, cmd):
            return (0, "", "")

    class FakeSecretManager:
        def __init__(self, runner):
            pass

        def list_secrets(self):
            return ["visp_mongo_uri", "visp_mongo_root_password", "visp_secret_app"]

        def remove_secrets(self, names):
            removed.extend(names)

    import vispctl.secrets as sec

    monkeypatch.setattr(sec, "SecretManager", FakeSecretManager)

    systemd = tmp_path / "systemd"
    systemd.mkdir()
    # Surviving user-authored unit that cites the shared mongo URI secret.
    (systemd / "custom-app.container").write_text("Secret=visp_mongo_uri,type=mount\n")

    quadlets = tmp_path / "quadlets"
    quadlets.mkdir()
    (quadlets / "mongo.container").write_text(
        "Secret=visp_mongo_uri,type=mount\nSecret=visp_mongo_root_password,type=mount\n"
    )
    (quadlets / "app.container").write_text("Secret=visp_secret_app,type=mount\n")

    cfg = types.SimpleNamespace(project_dir=tmp_path, systemd_dir=systemd, runner=FakeRunner())
    monkeypatch.setattr(vp, "get_config", lambda: cfg)
    monkeypatch.setattr(vp, "get_current_mode", lambda: "dev")
    monkeypatch.setattr(vp, "get_quadlets_dir", lambda mode: quadlets)
    monkeypatch.setattr(
        vp,
        "resolve_services",
        lambda svc, pdir, **kw: [types.SimpleNamespace(name="mongo", file=systemd / "mongo.container")],
    )
    import vispctl.quadlets as qmod

    monkeypatch.setattr(qmod, "remove_stale_quadlets", lambda *a, **k: [])

    args = types.SimpleNamespace(service="mongo", keep_running=True)
    vp.cmd_uninstall(args)

    _out = capsys.readouterr().out
    assert "visp_mongo_uri" not in removed, f"secret still cited by installed custom-app was removed: {removed}"
    assert "visp_mongo_root_password" in removed, "uninstalled service's own secret should still be removed"
