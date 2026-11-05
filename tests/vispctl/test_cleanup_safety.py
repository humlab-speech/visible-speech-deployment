"""Deletion safety: anchored prefixes only, honest rm accounting.

Round-2 confirmed live: unanchored podman name regexes plus blind removed+=1
let the sweep delete foreign containers and report success when rm failed.
"""

import importlib.util
import subprocess
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


class ModeRecordingRunner:
    """Answers discovery with one container; records every argv it saw."""

    def __init__(self):
        self.cmds = []

    def run(self, cmd, capture=False, check=True):
        self.cmds.append(list(cmd))
        stdout = ""
        rc = 0
        if cmd[:2] == ["podman", "ps"]:
            stdout = "ccc9 visp-session-mode-probe\n"
        return types.SimpleNamespace(stdout=stdout, returncode=rc, stderr="")


def _discovery_call(cmds):
    return next(c for c in cmds if c[:2] == ["podman", "ps"] and "--format" in c)


@pytest.mark.parametrize(
    "mode,expect_a,expect_exited_filter",
    [
        ("running", False, False),  # live `ps` only: never -a
        ("all", True, False),  # -a, no status filter
        ("stopped", True, True),  # -a plus the explicit exited filter
    ],
)
def test_mode_selects_the_right_podman_ps(monkeypatch, mode, expect_a, expect_exited_filter):
    """Round-3 mutation: `mode == "running"` at :15 survived because nothing
    pinned which discovery command each mode issues. 'running' must never
    enumerate exited containers with -a, or a running-only sweep deletes
    stopped ones too."""
    import vispctl.cleanup_containers as cc

    fr = ModeRecordingRunner()
    monkeypatch.setattr(cc, "Runner", lambda: fr)
    cleanup_containers(mode=mode, yes=True)

    disc = _discovery_call(fr.cmds)
    assert ("-a" in disc) is expect_a, f"mode={mode} issued wrong discovery: {disc}"
    assert ("status=exited" in " ".join(disc)) is expect_exited_filter


def test_stop_and_rm_errors_do_not_raise_but_report_partial(monkeypatch):
    """:89/:96/:97 must stay check=False, and their failures must stay VISIBLE.

    Round-3 mutation: a podman error during the proxy liveness probe, stop, or
    rm was invisible to the suite. This fake honours `check=` like the real
    Runner: if cleanup ever flips to check=True the error raises instead of
    being handled, and if it swallows the failure the partial report
    disappears. The probe path needs a -proxy container (round-3 gap: the
    probe only runs for sidecars) and it must fail like a real podman error."""
    import vispctl.cleanup_containers as cc

    class ErroringRunner:
        def __init__(self):
            self.rm_fail = False

        def run(self, cmd, capture=False, check=True):
            if cmd[:2] == ["podman", "ps"] and "id=" in " ".join(cmd):
                return types.SimpleNamespace(stdout="ddd1\n", returncode=0)  # main is running -> stop path
            if cmd[:2] == ["podman", "ps"]:
                if cmd[3] == "name=^visp-session-broken-proj$":
                    # proxy sidecar's main-liveness probe errors like a real podman failure
                    if check:
                        raise subprocess.CalledProcessError(1, cmd)
                    return types.SimpleNamespace(stdout="", returncode=1, stderr="cannot connect")
                return types.SimpleNamespace(
                    stdout="ddd1 visp-session-broken-proj\nddd2 visp-session-broken-proj-proxy\n",
                    returncode=0,
                )
            rc = 1 if cmd[:2] in (["podman", "rm"], ["podman", "stop"]) else 0
            if check and rc != 0:
                raise subprocess.CalledProcessError(rc, cmd)
            return types.SimpleNamespace(stdout="", returncode=rc, stderr="in use")

    monkeypatch.setattr(cc, "Runner", lambda: ErroringRunner())
    # must NOT raise CalledProcessError despite failing probe/stop/rm with check semantics live
    res = cleanup_containers(mode="all", yes=True)
    assert res["status"] == "partial"
    assert res["removed"] == 0
    assert "visp-session-broken-proj" in res["message"]
