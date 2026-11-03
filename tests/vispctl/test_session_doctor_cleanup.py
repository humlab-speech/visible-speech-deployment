"""The cleanup EXECUTOR: plan branches, podman rm, rmtree, and their counting.

Round-3 mutation sampling left the plan-and-execute path (session_doctor
542-641 and the adrift plan at 434-494) unexecuted: `test_session_doctor_safety`
pins the gate, but the code that actually deletes things — `podman rm`,
`shutil.rmtree`, the PermissionError→unshare fallback, and the returncode
gates that decide what counts as removed — had no coverage. These tests run
the executor end-to-end against faked subprocesses and real tmp directories.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

import vispctl.session_doctor as sd


def _session(name, cid, state, issues, warnings=(), proxy=None, socket_dir=None):
    return {
        "name": name,
        "type": "session",
        "id": cid,
        "state": state,
        "image": "img",
        "labels": {},
        "username": "u",
        "project_id": "p",
        "hs_app": "h",
        "issues": list(issues),
        "warnings": list(warnings),
        "proxy": proxy,
        "socket_dir": socket_dir,
    }


def _orphan_proxy(name, cid, state="exited"):
    return {
        "name": name,
        "type": "orphan-proxy",
        "id": cid,
        "state": state,
        "image": "img",
        "labels": {},
        "issues": ["proxy without a session container"],
        "warnings": [],
    }


def _stale_dir(path, issues=("stale socket directory",)):
    return {
        "name": path.name,
        "type": "stale-socket-dir",
        "issues": list(issues),
        "warnings": [],
        "socket_dir": {"path": Path(path)},
    }


class RecorderRun:
    """subprocess.run replacement: records podman calls, scripted return codes."""

    def __init__(self, rm_rc=0, unshare_rc=0):
        self.rm_rc = rm_rc
        self.unshare_rc = unshare_rc
        self.stopped = []
        self.removed = []
        self.unshared = []

    def __call__(self, cmd, *a, **k):
        if cmd[:3] == ["podman", "stop", "--time"]:
            self.stopped.append(cmd[-1])
            rc = 0
        elif cmd[:3] == ["podman", "rm", "-f"]:
            self.removed.append(cmd[-1])
            rc = self.rm_rc
        elif cmd[:3] == ["podman", "unshare", "rm"]:
            self.unshared.append(cmd[-1])
            rc = self.unshare_rc
        else:
            rc = 0
        return subprocess.CompletedProcess(list(cmd), rc, "", "")


def test_executor_removes_dead_session_with_its_dead_proxy(tmp_path):
    rec = RecorderRun()
    reports = [
        _session(
            "visp-session-alice-proj",
            "cid1",
            "exited",
            ["Container is exited — should be cleaned up"],
            proxy={"state": "exited", "id": "cid2"},
        )
    ]
    monkey = pytest.MonkeyPatch()
    monkey.setattr(sd.subprocess, "run", rec)
    try:
        summary = sd._cleanup_orphans(reports, dry_run=False, yes=True)
    finally:
        monkey.undo()

    assert summary["removed_containers"] == 2, "dead session or its dead proxy not both removed"
    assert "visp-session-alice-proj" in rec.removed and "visp-session-alice-proj-proxy" in rec.removed
    assert "visp-session-alice-proj" in rec.stopped, "containers removed without stop first"


def test_executor_removes_adrift_session_proxy_and_socket_dir(tmp_path):
    sock = tmp_path / "sessionadrift123"
    sock.mkdir()
    (sock / "ui.sock").touch()
    rec = RecorderRun()
    reports = [
        _session(
            "visp-session-adrift123",
            "cid3",
            "running",
            ["Adrift — running but session-manager has no record of it"],
            proxy={"state": "running", "id": "cid4"},
            socket_dir={"path": sock},
        )
    ]
    monkey = pytest.MonkeyPatch()
    monkey.setattr(sd.subprocess, "run", rec)
    try:
        summary = sd._cleanup_orphans(reports, dry_run=False, yes=True)
    finally:
        monkey.undo()

    assert summary["removed_containers"] == 2
    assert summary["removed_dirs"] == 1
    assert not sock.exists(), "adrift session's socket directory survived cleanup"


def test_executor_orphan_proxy_and_stale_dirs(tmp_path):
    d1 = tmp_path / "sessionone111"
    d2 = tmp_path / "sessiontwo222"
    for d in (d1, d2):
        d.mkdir()
        (d / "proxy.sock").touch()
    rec = RecorderRun()
    reports = [_orphan_proxy("hsapp-session-x-proxy", "cid5"), _stale_dir(d1), _stale_dir(d2)]
    monkey = pytest.MonkeyPatch()
    monkey.setattr(sd.subprocess, "run", rec)
    try:
        summary = sd._cleanup_orphans(reports, dry_run=False, yes=True)
    finally:
        monkey.undo()

    assert summary["removed_containers"] == 1
    assert summary["removed_dirs"] == 2
    assert rec.removed == ["hsapp-session-x-proxy"]
    assert not d1.exists() and not d2.exists()


def test_failed_rm_is_not_counted_as_removed(tmp_path):
    """The `returncode == 0` gate at the rm loop must decide the count:
    a failed `podman rm -f` may never inflate removed_containers."""
    rec = RecorderRun(rm_rc=1)
    reports = [_orphan_proxy("hsapp-session-z-proxy", "cid6")]
    monkey = pytest.MonkeyPatch()
    monkey.setattr(sd.subprocess, "run", rec)
    try:
        summary = sd._cleanup_orphans(reports, dry_run=False, yes=True)
    finally:
        monkey.undo()
    assert summary["removed_containers"] == 0


def test_permission_error_falls_back_to_unshare(tmp_path, monkeypatch, capsys):
    d = tmp_path / "sessionperm333"
    d.mkdir()
    (d / "ui.sock").touch()
    rec = RecorderRun(unshare_rc=0)
    monkeypatch.setattr(sd.subprocess, "run", rec)

    real_rmtree = shutil.rmtree

    def fake_rmtree(path, *a, **k):
        real_rmtree(path, *a, **k)  # actual deletion so the dir really goes

    # raise PermissionError to trigger the fallback, but still remove the tree
    def boom_rmtree(path, *a, **k):
        real_rmtree(path, *a, **k)
        raise PermissionError(path)

    monkeypatch.setattr(shutil, "rmtree", boom_rmtree)
    summary = sd._cleanup_orphans([_stale_dir(d)], dry_run=False, yes=True)
    monkeypatch.undo()

    assert summary["removed_dirs"] == 1, "unshare fallback succeeded but was not counted"
    assert rec.unshared == [str(d)]
    assert "via unshare" in capsys.readouterr().out


def test_failed_unshare_is_reported_not_counted(tmp_path, monkeypatch, capsys):
    d = tmp_path / "sessionfail444"
    d.mkdir()
    rec = RecorderRun(unshare_rc=1)
    monkeypatch.setattr(sd.subprocess, "run", rec)

    def keep_rmtree(path, *a, **k):
        # pretend neither path can remove it
        raise PermissionError(path)

    monkeypatch.setattr(shutil, "rmtree", keep_rmtree)
    summary = sd._cleanup_orphans([_stale_dir(d)], dry_run=False, yes=True)
    assert summary["removed_dirs"] == 0, "failed unshare counted as a removal"
    assert "Failed to remove" in capsys.readouterr().out


def test_confirmation_no_cancels_without_touching(tmp_path, monkeypatch):
    d = tmp_path / "sessioncancel555"
    d.mkdir()
    (d / "ui.sock").touch()
    rec = RecorderRun()
    monkeypatch.setattr(sd.subprocess, "run", rec)
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    summary = sd._cleanup_orphans([_stale_dir(d)], dry_run=False, yes=False)
    assert summary["status"] == "cancelled"
    assert d.exists()
    assert rec.removed == []


def test_dry_run_previews_but_deletes_nothing(tmp_path, monkeypatch):
    d = tmp_path / "sessiondry666"
    d.mkdir()
    (d / "ui.sock").touch()
    rec = RecorderRun()
    monkeypatch.setattr(sd.subprocess, "run", rec)
    summary = sd._cleanup_orphans(
        [_orphan_proxy("hsapp-session-p-proxy", "cid7"), _stale_dir(d)],
        dry_run=True,
        yes=True,
    )
    assert summary["status"] == "dry_run"
    assert summary["would_remove_containers"] == 1
    assert summary["would_remove_dirs"] == 1
    assert d.exists()
    assert rec.removed == []
