"""Deletion safety: anchored prefixes only, honest rm accounting.

Round-2 confirmed live: unanchored podman name regexes plus blind removed+=1
let the sweep delete foreign containers and report success when rm failed.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

proj = str(Path(__file__).resolve().parents[2])
if proj not in sys.path:
    sys.path.insert(0, proj)

from vispctl.cleanup_containers import cleanup_containers  # noqa: E402

PS_LINES = "\n".join(
    [
        "aaa1 notvisp-session-foreign",
        "aaa2 my-visp-session-backup",
        "aaa3 demo-hsapp-session-old",
        "aaa4 visp-session-realuser-proj",
        "aaa5 session-manager",
    ]
)


class FakeRunner:
    def __init__(self, ps_rc=0, rm_rc=0, running_main=""):
        self.ps_rc = ps_rc
        self.rm_rc = rm_rc
        self.running_main = running_main
        self.rmed = []

    def run(self, cmd, capture=False, check=True):
        if cmd[:3] == ["podman", "ps", "-a"] or cmd[:2] == ["podman", "ps"]:
            if "id=" in " ".join(cmd):
                return types.SimpleNamespace(stdout="", returncode=0)
            if any(f.startswith("name=^") and f.endswith("$") for f in cmd):
                # main-session liveness probe (exact-match anchored)
                return types.SimpleNamespace(stdout=self.running_main, returncode=0)
            return types.SimpleNamespace(stdout=PS_LINES, returncode=self.ps_rc)
        if cmd[:2] == ["podman", "rm"]:
            self.rmed.append(cmd[2])
            return types.SimpleNamespace(stdout="", returncode=self.rm_rc)
        return types.SimpleNamespace(stdout="", returncode=0)


def _run_with(monkeypatch, fr, mode="all"):
    import vispctl.cleanup_containers as cc

    monkeypatch.setattr(cc, "Runner", lambda: fr)
    return cleanup_containers(mode=mode, yes=True)


def test_foreign_names_survive_the_sweep(monkeypatch):
    fr = FakeRunner()
    res = _run_with(monkeypatch, fr)
    assert fr.rmed == ["aaa4"]
    assert res["removed"] == 1


def test_unreachable_podman_removes_nothing(monkeypatch):
    fr = FakeRunner(ps_rc=1)
    res = _run_with(monkeypatch, fr)
    assert fr.rmed == []
    assert res["status"] == "error"
    assert res["removed"] == 0


def test_failed_rm_is_not_counted_as_removed(monkeypatch):
    fr = FakeRunner(rm_rc=1)
    res = _run_with(monkeypatch, fr)
    assert res["removed"] == 0
    assert res["status"] == "partial"


def _load_visp():
    spec = importlib.util.spec_from_file_location("vp", str(Path(proj) / "visp.py"))
    vp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vp)
    return vp


def test_cmd_exits_1_on_partial(monkeypatch):
    vp = _load_visp()
    monkeypatch.setattr(vp, "cleanup_containers", lambda mode, yes: {"status": "partial", "message": "x", "removed": 0})
    with pytest.raises(SystemExit) as exc:
        vp.cmd_cleanup_containers(types.SimpleNamespace(mode="all", yes=True))
    assert exc.value.code == 1


PROXY_LINES = "\n".join(
    [
        "bbb1 visp-session-alice-proj",
        "bbb2 visp-session-alice-proj-proxy",
    ]
)


class ProxyFake:
    """Session main + proxy sidecar; main container is RUNNING."""

    def __init__(self, main_running=True):
        self.main_running = main_running
        self.rmed = []

    def run(self, cmd, capture=False, check=True):
        if cmd[:2] == ["podman", "ps"]:
            if any(f.startswith("name=^") and f.endswith("$") for f in cmd):
                return types.SimpleNamespace(stdout="aaa" if self.main_running else "", returncode=0)
            if "id=" in " ".join(cmd):
                return types.SimpleNamespace(stdout="bbb1" if self.main_running else "", returncode=0)
            return types.SimpleNamespace(stdout=PROXY_LINES, returncode=0)
        if cmd[:2] == ["podman", "rm"]:
            self.rmed.append(cmd[2])
            return types.SimpleNamespace(stdout="", returncode=0)
        if cmd[:2] == ["podman", "stop"]:
            return types.SimpleNamespace(stdout="", returncode=0)
        return types.SimpleNamespace(stdout="", returncode=0)


def test_proxy_of_running_session_survives(monkeypatch):
    import vispctl.cleanup_containers as cc

    fr = ProxyFake(main_running=True)
    monkeypatch.setattr(cc, "Runner", lambda: fr)
    _res = cleanup_containers(mode="all", yes=True)
    assert "bbb2" not in fr.rmed  # proxy kept


def test_proxy_of_stopped_session_is_removed(monkeypatch):
    import vispctl.cleanup_containers as cc

    fr = ProxyFake(main_running=False)
    monkeypatch.setattr(cc, "Runner", lambda: fr)
    cleanup_containers(mode="all", yes=True)
    assert "bbb2" in fr.rmed  # orphan proxy cleaned
