"""Safety guards for session-doctor cleanup: no podman evidence => no deletions,
and only directories with real socket evidence can be classed stale.

These pin behaviour that round-1 proved missing: with podman unreachable the
tool used to report '0 containers', call every directory stale and rmtree them,
including directories holding unrelated user data.
"""

import subprocess
from pathlib import Path

import pytest

import vispctl.session_doctor as sd


@pytest.fixture()
def sessions_root(tmp_path, monkeypatch):
    root = tmp_path / "mounts" / "sessions"
    root.mkdir(parents=True)
    monkeypatch.setattr(sd, "_get_sessions_dir", lambda: root)
    monkeypatch.setattr(sd, "_query_session_manager", lambda: [])
    return root


def _stale_socket_dir(root: Path, name: str = "sessionliveabc123") -> Path:
    d = root / name
    d.mkdir()
    (d / "proxy.sock").touch()
    return d


def test_unreachable_podman_cleans_nothing(sessions_root, monkeypatch, capsys):
    stale = _stale_socket_dir(sessions_root)
    data = sessions_root / "not-a-session-just-my-data"
    data.mkdir()
    (data / "important.csv").write_text("user data")

    # Fail at the subprocess boundary so the real _podman_ps_json failure
    # handling is exercised, not a mock's opinion of it.
    real_run = sd.subprocess.run

    def fake_run(cmd, *a, **k):
        if isinstance(cmd, list) and cmd[:2] == ["podman", "ps"]:
            return sd.subprocess.CompletedProcess(list(cmd), 1, "", "podman is not reachable")
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(sd.subprocess, "run", fake_run)

    rc = sd.run_session_doctor(clean=True, yes=True)

    assert rc == 1
    assert stale.exists(), "cleanup deleted directories while podman was unreachable"
    assert (data / "important.csv").exists()
    assert "not reachable" in capsys.readouterr().out


def test_stale_removal_requires_socket_evidence(sessions_root, monkeypatch):
    stale = _stale_socket_dir(sessions_root)
    data = sessions_root / "not-a-session-just-my-data"
    data.mkdir()
    (data / "important.csv").write_text("user data")
    monkeypatch.setattr(sd, "_podman_ps_json", lambda *a, **k: [])
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(list(a[0]) if a else [], 0, "", "")
    )

    sd.run_session_doctor(clean=True, yes=True)

    assert not stale.exists(), "stale socket directory with real socket evidence was not cleaned"
    assert (data / "important.csv").exists(), "cleanup touched a directory without any socket evidence"


def test_confirmed_empty_podman_still_cleans_stale_sockets(sessions_root, monkeypatch):
    stale = _stale_socket_dir(sessions_root, name="sessionother456")
    monkeypatch.setattr(sd, "_podman_ps_json", lambda *a, **k: [])
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(list(a[0]) if a else [], 0, "", "")
    )

    sd.run_session_doctor(clean=True, yes=True)

    assert not stale.exists(), "normal cleanup path regressed: podman answered, zero containers, dir still kept"
