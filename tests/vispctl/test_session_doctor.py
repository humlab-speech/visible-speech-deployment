"""Tests for vispctl.session_doctor — session container health checks."""

from pathlib import Path
from unittest.mock import patch

from vispctl.session_doctor import (
    _collect_socket_dirs,
    _diagnose,
    run_session_doctor,
)

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_session(name, state="running", labels=None):
    """Create a mock session container dict."""
    default_labels = {
        "visp.hsApp": "jupyter",
        "visp.username": "testuser_at_example_dot_com",
        "visp.projectId": "proj123",
        "visp.accessCode": "abc123",
    }
    if labels:
        default_labels.update(labels)
    return {
        name: {
            "id": "abcdef123456",
            "state": state,
            "labels": default_labels,
            "created": "2026-04-15T10:00:00Z",
            "image": "localhost/visp-jupyter-session:latest",
        }
    }


def _make_proxy(session_name, state="running"):
    """Create a mock proxy container dict."""
    proxy_name = session_name + "-proxy"
    return {
        proxy_name: {
            "id": "fedcba654321",
            "state": state,
            "labels": {
                "visp.proxyFor": session_name,
                "visp.type": "session-proxy",
            },
            "claims_session": session_name,
            "created": "2026-04-15T10:00:01Z",
            "image": "localhost/visp-session-proxy:latest",
        }
    }


def _make_socket_dir(name, has_ui=True, has_proxy=True):
    """Create a mock socket directory dict."""
    return {
        name: {
            "path": Path(f"/fake/mounts/sessions/{name}"),
            "has_ui_sock": has_ui,
            "has_proxy_sock": has_proxy,
        }
    }


# ── Diagnosis tests ───────────────────────────────────────────────────────────


def test_healthy_session_with_proxy():
    """A running session with a running proxy should have no issues."""
    name = "visp-session-proj-user-A1b2"
    sessions = _make_session(name)
    proxies = _make_proxy(name)
    socket_dirs = _make_socket_dir(name)

    reports = _diagnose(sessions, proxies, socket_dirs)

    assert len(reports) == 1
    r = reports[0]
    assert r["type"] == "session"
    assert r["issues"] == []
    assert r["warnings"] == []
    assert r["proxy"] is not None


def test_exited_session_flagged():
    """An exited session container should be flagged as an issue."""
    name = "visp-session-proj-user-X1y2"
    sessions = _make_session(name, state="exited")
    proxies = _make_proxy(name)
    socket_dirs = _make_socket_dir(name)

    reports = _diagnose(sessions, proxies, socket_dirs)

    assert len(reports) == 1
    assert any("exited" in i for i in reports[0]["issues"])


def test_dead_proxy_flagged():
    """A dead proxy sidecar should be flagged as an issue on the session."""
    name = "visp-session-proj-user-D3e4"
    sessions = _make_session(name)
    proxies = _make_proxy(name, state="dead")
    socket_dirs = _make_socket_dir(name)

    reports = _diagnose(sessions, proxies, socket_dirs)

    assert len(reports) == 1
    assert any("dead" in i.lower() for i in reports[0]["issues"])


def test_orphaned_proxy_detected():
    """A proxy with no matching session should be reported as orphaned."""
    name = "visp-session-proj-user-Or1p"
    sessions = {}  # No session containers
    proxies = _make_proxy(name)
    socket_dirs = {}

    reports = _diagnose(sessions, proxies, socket_dirs)

    assert len(reports) == 1
    assert reports[0]["type"] == "orphan-proxy"
    assert any("orphan" in i.lower() for i in reports[0]["issues"])


def test_stale_socket_dir_detected():
    """A socket directory with no matching container should be flagged."""
    name = "visp-session-proj-user-St1l"
    sessions = {}
    proxies = {}
    socket_dirs = _make_socket_dir(name)

    reports = _diagnose(sessions, proxies, socket_dirs)

    assert len(reports) == 1
    assert reports[0]["type"] == "stale-socket-dir"
    assert any("stale" in i.lower() for i in reports[0]["issues"])


def test_bridge_session_no_proxy_is_ok():
    """A bridge-mode session (not UDS) without a proxy should be fine."""
    name = "visp-session-proj-user-Br1d"
    sessions = _make_session(name)
    proxies = {}
    socket_dirs = {}

    # Mock podman inspect to return bridge mode
    with patch("vispctl.session_doctor._podman_inspect") as mock_inspect:
        mock_inspect.return_value = {"HostConfig": {"NetworkMode": "bridge"}}
        reports = _diagnose(sessions, proxies, socket_dirs)

    assert len(reports) == 1
    # Should not have "no proxy sidecar" in issues
    assert not any("proxy" in i.lower() for i in reports[0]["issues"])


def test_uds_session_without_proxy_is_issue():
    """A network=none session without a proxy sidecar should be flagged."""
    name = "visp-session-proj-user-Ud1s"
    sessions = _make_session(name)
    proxies = {}
    socket_dirs = {}

    with patch("vispctl.session_doctor._podman_inspect") as mock_inspect:
        mock_inspect.return_value = {"HostConfig": {"NetworkMode": "none"}}
        reports = _diagnose(sessions, proxies, socket_dirs)

    assert len(reports) == 1
    assert any("proxy" in i.lower() for i in reports[0]["issues"])


def test_proxy_label_mismatch_warned():
    """Proxy claiming a different session should produce a warning."""
    name = "visp-session-proj-user-Mm1s"
    sessions = _make_session(name)
    proxy_name = name + "-proxy"
    proxies = {
        proxy_name: {
            "id": "fedcba654321",
            "state": "running",
            "labels": {
                "visp.proxyFor": "some-other-container",
                "visp.type": "session-proxy",
            },
            "claims_session": "some-other-container",
            "created": "2026-04-15T10:00:01Z",
            "image": "localhost/visp-session-proxy:latest",
        }
    }
    socket_dirs = _make_socket_dir(name)

    reports = _diagnose(sessions, proxies, socket_dirs)

    session_report = [r for r in reports if r["type"] == "session"][0]
    assert any("visp.proxyFor" in w for w in session_report["warnings"])


def test_empty_state_is_healthy():
    """No containers, no proxies, no dirs → no issues."""
    reports = _diagnose({}, {}, {})
    assert reports == []


def test_multiple_sessions_reported_independently():
    """Each session gets its own report."""
    s1 = _make_session("visp-session-a-user-A1")
    s2 = _make_session("visp-session-b-user-B1")
    sessions = {**s1, **s2}

    p1 = _make_proxy("visp-session-a-user-A1")
    proxies = {**p1}  # Only one proxy — second session has none

    with patch("vispctl.session_doctor._podman_inspect") as mock_inspect:
        mock_inspect.return_value = {"HostConfig": {"NetworkMode": "bridge"}}
        reports = _diagnose(sessions, proxies, {})

    assert len(reports) == 2
    session_reports = [r for r in reports if r["type"] == "session"]
    assert len(session_reports) == 2


def test_socket_dir_collection(tmp_path):
    """_collect_socket_dirs should find directories with socket files."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()

    # Create a session dir with sockets
    sd = sessions_dir / "visp-session-test-user-Ab12"
    sd.mkdir()
    (sd / "ui.sock").touch()
    (sd / "proxy.sock").touch()

    # Create one with no sockets
    empty = sessions_dir / "visp-session-stale-user-Cd34"
    empty.mkdir()

    with patch("vispctl.session_doctor.SESSIONS_DIR", sessions_dir):
        dirs = _collect_socket_dirs()

    assert len(dirs) == 2
    assert dirs["visp-session-test-user-Ab12"]["has_ui_sock"] is True
    assert dirs["visp-session-test-user-Ab12"]["has_proxy_sock"] is True
    assert dirs["visp-session-stale-user-Cd34"]["has_ui_sock"] is False
    assert dirs["visp-session-stale-user-Cd34"]["has_proxy_sock"] is False


def test_json_output(capsys):
    """--json flag should produce parseable JSON."""
    with (
        patch("vispctl.session_doctor._collect_session_containers", return_value={}),
        patch("vispctl.session_doctor._collect_proxy_containers", return_value={}),
        patch("vispctl.session_doctor._collect_socket_dirs", return_value={}),
        patch("vispctl.session_doctor._query_session_manager", return_value=None),
    ):
        result = run_session_doctor(json_output=True)

    assert result == 0
    import json

    output = json.loads(capsys.readouterr().out)
    assert "summary" in output
    assert output["summary"]["total_sessions"] == 0
    assert output["summary"]["total_issues"] == 0
    assert "session_manager" in output
    assert output["session_manager"]["reachable"] is False


# ── Session-manager tracking / adrift detection tests ──────────────────────────


def test_adrift_session_detected():
    """A running session not tracked by session-manager should be flagged as adrift."""
    name = "visp-session-proj-user-Ad1r"
    sessions = _make_session(name)
    proxies = _make_proxy(name)
    socket_dirs = _make_socket_dir(name)

    # session-manager knows about a *different* container, not this one
    sm_sessions = [{"containerId": "999999999999"}]

    reports = _diagnose(sessions, proxies, socket_dirs, sm_sessions=sm_sessions)

    assert len(reports) == 1
    assert any("adrift" in i.lower() for i in reports[0]["issues"])


def test_tracked_session_not_adrift():
    """A running session tracked by session-manager should NOT be flagged as adrift."""
    name = "visp-session-proj-user-Tr1k"
    sessions = _make_session(name)
    proxies = _make_proxy(name)
    socket_dirs = _make_socket_dir(name)

    # session-manager knows about this container (matching the 12-char id)
    sm_sessions = [{"containerId": "abcdef123456"}]

    reports = _diagnose(sessions, proxies, socket_dirs, sm_sessions=sm_sessions)

    assert len(reports) == 1
    assert not any("adrift" in i.lower() for i in reports[0]["issues"])
    assert reports[0]["issues"] == []


def test_sm_unreachable_skips_adrift_check():
    """When session-manager is unreachable (None), adrift check should be skipped."""
    name = "visp-session-proj-user-Sk1p"
    sessions = _make_session(name)
    proxies = _make_proxy(name)
    socket_dirs = _make_socket_dir(name)

    # sm_sessions=None means session-manager is unreachable
    reports = _diagnose(sessions, proxies, socket_dirs, sm_sessions=None)

    assert len(reports) == 1
    # Should have no adrift issues (check is skipped when SM unreachable)
    assert not any("adrift" in i.lower() for i in reports[0]["issues"])
    assert reports[0]["issues"] == []


def test_exited_session_not_flagged_adrift():
    """An exited session should not also be flagged as adrift (only running ones)."""
    name = "visp-session-proj-user-Ex1t"
    sessions = _make_session(name, state="exited")
    proxies = _make_proxy(name)
    socket_dirs = _make_socket_dir(name)

    # session-manager has no record (empty list) — but session is exited
    sm_sessions = []

    reports = _diagnose(sessions, proxies, socket_dirs, sm_sessions=sm_sessions)

    assert len(reports) == 1
    issues = reports[0]["issues"]
    # Should have the "exited" issue but NOT the "adrift" issue
    assert any("exited" in i for i in issues)
    assert not any("adrift" in i.lower() for i in issues)


def test_sm_empty_list_all_adrift():
    """When session-manager returns empty list, all running sessions are adrift."""
    s1 = _make_session("visp-session-a-user-A1")
    s2 = _make_session("visp-session-b-user-B1")
    sessions = {**s1, **s2}
    proxies = {**_make_proxy("visp-session-a-user-A1"), **_make_proxy("visp-session-b-user-B1")}
    socket_dirs = {**_make_socket_dir("visp-session-a-user-A1"), **_make_socket_dir("visp-session-b-user-B1")}

    # session-manager just restarted — empty list
    sm_sessions = []

    reports = _diagnose(sessions, proxies, socket_dirs, sm_sessions=sm_sessions)

    session_reports = [r for r in reports if r["type"] == "session"]
    assert len(session_reports) == 2
    for r in session_reports:
        assert any("adrift" in i.lower() for i in r["issues"])


def test_json_output_includes_sm_status(capsys):
    """JSON output should include session-manager reachability info."""
    name = "visp-session-proj-user-Js1n"
    with (
        patch("vispctl.session_doctor._collect_session_containers", return_value=_make_session(name)),
        patch("vispctl.session_doctor._collect_proxy_containers", return_value=_make_proxy(name)),
        patch("vispctl.session_doctor._collect_socket_dirs", return_value=_make_socket_dir(name)),
        patch(
            "vispctl.session_doctor._query_session_manager",
            return_value=[{"containerId": "abcdef123456"}],
        ),
    ):
        result = run_session_doctor(json_output=True)

    assert result == 0
    import json

    output = json.loads(capsys.readouterr().out)
    assert output["session_manager"]["reachable"] is True
    assert output["session_manager"]["tracked_sessions"] == 1
