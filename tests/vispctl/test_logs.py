"""Tests for vispctl.logs: tail_container_logs, stream_podman_logs,
show_debug_info, and view_logs."""

from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import MagicMock, patch

from vispctl.logs import show_debug_info, stream_podman_logs, tail_container_logs, view_logs
from vispctl.runner import Runner
from vispctl.service import Service

# ── helpers ────────────────────────────────────────────────────────────────────


def _fake_runner(run_quiet_rc=0):
    """Return a Runner whose run_quiet always returns (rc, '', '')."""
    r = MagicMock(spec=Runner)
    r.run_quiet.return_value = (run_quiet_rc, "", "")
    return r


def _svc(name: str, stype: str = "container") -> Service:
    return Service(name, stype, f"{name}.container")


CONTAINER_LOG_FILES = {
    "apache": [
        ("api", "/var/log/api/webapi.log"),
        ("php-errors", "/var/log/api/php_error.log"),
    ],
}


# ── tail_container_logs ────────────────────────────────────────────────────────


def test_tail_skips_service_with_no_log_files(capsys) -> None:
    runner = _fake_runner()
    tail_container_logs("mongo", {}, runner)
    runner.run_quiet.assert_not_called()
    assert capsys.readouterr().out == ""


def test_tail_skips_when_container_not_running(capsys) -> None:
    runner = _fake_runner(run_quiet_rc=1)
    tail_container_logs("apache", CONTAINER_LOG_FILES, runner)
    out = capsys.readouterr().out
    assert "not running" in out


def test_tail_snapshot_shows_log_content(capsys) -> None:
    runner = MagicMock(spec=Runner)
    # inspect returns running (rc=0); log tail returns content
    runner.run_quiet.side_effect = [
        (0, "running", ""),  # podman inspect
        (0, "line1\nline2", ""),  # tail api log
        (0, "", ""),  # tail php-errors (empty)
    ]
    tail_container_logs("apache", CONTAINER_LOG_FILES, runner, lines=10)
    out = capsys.readouterr().out
    assert "line1" in out
    assert "line2" in out


def test_tail_snapshot_skips_empty_log(capsys) -> None:
    runner = MagicMock(spec=Runner)
    runner.run_quiet.side_effect = [
        (0, "running", ""),  # inspect
        (0, "", ""),  # api log empty
        (0, "", ""),  # php-errors empty
    ]
    tail_container_logs("apache", CONTAINER_LOG_FILES, runner)
    out = capsys.readouterr().out
    # No log-section header should appear for empty logs
    assert "api" not in out


# ── stream_podman_logs ─────────────────────────────────────────────────────────


def test_stream_podman_logs_builds_correct_command() -> None:
    with patch("subprocess.run") as mock_run:
        stream_podman_logs("mongo", follow=False, lines=50)
        cmd = mock_run.call_args[0][0]
        assert "podman" in cmd
        assert "logs" in cmd
        assert "--tail" in cmd
        assert "50" in cmd
        assert "mongo" in cmd


def test_stream_podman_logs_follow_adds_f_flag() -> None:
    with patch("subprocess.run") as mock_run:
        stream_podman_logs("mongo", follow=True)
        cmd = mock_run.call_args[0][0]
        assert "-f" in cmd


def test_stream_podman_logs_since_adds_flag() -> None:
    with patch("subprocess.run") as mock_run:
        stream_podman_logs("mongo", follow=False, since="1h")
        cmd = mock_run.call_args[0][0]
        assert "--since" in cmd
        assert "1h" in cmd


# ── show_debug_info ────────────────────────────────────────────────────────────


def test_show_debug_info_prints_quadlet_file(tmp_path: Path, capsys) -> None:
    sys_dir = tmp_path / "systemd"
    sys_dir.mkdir()

    svc = _svc("session-manager")
    target = sys_dir / svc.file
    target.write_text("unit content")

    runner = MagicMock(spec=Runner)
    runner.run_quiet.return_value = (1, "", "")  # container not found

    show_debug_info("session-manager", runner, [svc], sys_dir)
    out = capsys.readouterr().out
    assert "Quadlet File:" in out
    assert str(target) in out


def test_show_debug_info_container_not_found_message(tmp_path: Path, capsys) -> None:
    sys_dir = tmp_path / "systemd"
    sys_dir.mkdir()

    runner = MagicMock(spec=Runner)
    runner.run_quiet.return_value = (1, "", "")

    show_debug_info("apache", runner, [], sys_dir)
    out = capsys.readouterr().out
    assert "not found" in out


def test_show_debug_info_prints_systemctl_status(tmp_path: Path, capsys) -> None:
    """show_debug_info prints the captured systemctl status output (not empty)."""
    sys_dir = tmp_path / "systemd"
    sys_dir.mkdir()

    runner = MagicMock(spec=Runner)
    runner.run_quiet.return_value = (0, "", "")  # container found
    runner.systemctl.return_value = types.SimpleNamespace(
        stdout="● mongo.service - MongoDB Database\n     Active: active (running)\n   Main PID: 1234 (conmon)\n",
        stderr="",
    )

    show_debug_info("mongo", runner, [_svc("mongo")], sys_dir)
    out = capsys.readouterr().out

    assert "Service Status:" in out
    assert "Active: active (running)" in out
    assert "Main PID: 1234" in out


def test_show_debug_info_status_placeholder_when_empty(tmp_path: Path, capsys) -> None:
    """When systemctl produces no output, a placeholder is shown (not a blank section)."""
    sys_dir = tmp_path / "systemd"
    sys_dir.mkdir()

    runner = MagicMock(spec=Runner)
    runner.run_quiet.return_value = (1, "", "")
    runner.systemctl.return_value = types.SimpleNamespace(stdout="", stderr="")

    show_debug_info("mongo", runner, [_svc("mongo")], sys_dir)
    out = capsys.readouterr().out

    assert "Service Status:" in out
    assert "no status output" in out


# ── view_logs ──────────────────────────────────────────────────────────────────


def _make_view_args(**kwargs) -> types.SimpleNamespace:
    defaults = dict(
        service="mongo",
        follow=False,
        lines=None,
        since=None,
        priority=None,
        journal_only=False,
        debug=False,
        no_follow=True,
    )
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


def test_view_logs_uses_podman_logs_for_container_service() -> None:
    runner = MagicMock(spec=Runner)
    services = [_svc("mongo")]
    args = _make_view_args(service="mongo", follow=False)

    with patch("vispctl.logs.stream_podman_logs") as mock_stream:
        view_logs(
            args,
            runner=runner,
            container_log_files={},
            systemd_dir=Path("/tmp"),
            get_runtime_services=lambda: services,
            get_all_services=lambda: services,
            resolve_services=lambda s: [next(x for x in services if x.name == s)],
            container_services=lambda svcs: [s for s in svcs if s.type == "container"],
        )
    mock_stream.assert_called_once()
    call_kwargs = mock_stream.call_args
    assert call_kwargs[0][0] == "mongo"


def test_view_logs_journal_only_skips_podman() -> None:
    runner = MagicMock(spec=Runner)
    services = [_svc("mongo")]
    args = _make_view_args(service="mongo", follow=False, journal_only=True)

    with patch("vispctl.logs.stream_podman_logs") as mock_stream:
        view_logs(
            args,
            runner=runner,
            container_log_files={},
            systemd_dir=Path("/tmp"),
            get_runtime_services=lambda: services,
            get_all_services=lambda: services,
            resolve_services=lambda s: [next(x for x in services if x.name == s)],
            container_services=lambda svcs: [s for s in svcs if s.type == "container"],
        )
    mock_stream.assert_not_called()


def test_view_logs_debug_calls_show_debug_info() -> None:
    runner = MagicMock(spec=Runner)
    services = [_svc("apache")]
    args = _make_view_args(service="apache", follow=False, debug=True, journal_only=True)

    with patch("vispctl.logs.show_debug_info") as mock_debug:
        view_logs(
            args,
            runner=runner,
            container_log_files=CONTAINER_LOG_FILES,
            systemd_dir=Path("/tmp"),
            get_runtime_services=lambda: services,
            get_all_services=lambda: services,
            resolve_services=lambda s: services,
            container_services=lambda svcs: [s for s in svcs if s.type == "container"],
        )
    mock_debug.assert_called_once()
    assert mock_debug.call_args[0][0] == "apache"


def test_view_logs_all_uses_journalctl() -> None:
    runner = MagicMock(spec=Runner)
    services = [_svc("mongo"), _svc("apache")]
    args = _make_view_args(service="all", follow=False)

    view_logs(
        args,
        runner=runner,
        container_log_files={},
        systemd_dir=Path("/tmp"),
        get_runtime_services=lambda: services,
        get_all_services=lambda: services,
        resolve_services=lambda s: services,
        container_services=lambda svcs: [s for s in svcs if s.type == "container"],
    )
    runner.journalctl.assert_called_once()
    units_arg = runner.journalctl.call_args[0]
    assert "mongo.service" in units_arg or any("mongo" in a for a in units_arg)


def test_view_logs_snapshot_tail_suppressed_by_journal_only() -> None:
    """journal_only flag must prevent tail_container_logs from being called."""
    runner = MagicMock(spec=Runner)
    services = [_svc("apache")]
    args = _make_view_args(service="apache", follow=False, journal_only=True)

    with patch("vispctl.logs.tail_container_logs") as mock_tail:
        view_logs(
            args,
            runner=runner,
            container_log_files=CONTAINER_LOG_FILES,
            systemd_dir=Path("/tmp"),
            get_runtime_services=lambda: services,
            get_all_services=lambda: services,
            resolve_services=lambda s: services,
            container_services=lambda svcs: [s for s in svcs if s.type == "container"],
        )
    mock_tail.assert_not_called()


def test_view_logs_snapshot_tail_called_when_not_journal_only() -> None:
    """When journal_only is False and service has app logs, tail should be called."""
    runner = MagicMock(spec=Runner)
    # Use a non-container type so stream_podman_logs shortcut is not taken
    services = [Service("apache", "other", "apache.container")]
    args = _make_view_args(service="apache", follow=False, journal_only=False)

    with patch("vispctl.logs.tail_container_logs") as mock_tail:
        view_logs(
            args,
            runner=runner,
            container_log_files=CONTAINER_LOG_FILES,
            systemd_dir=Path("/tmp"),
            get_runtime_services=lambda: services,
            get_all_services=lambda: services,
            resolve_services=lambda s: services,
            container_services=lambda svcs: [s for s in svcs if s.type == "container"],
        )
    mock_tail.assert_called_once()
    assert mock_tail.call_args[0][0] == "apache"
