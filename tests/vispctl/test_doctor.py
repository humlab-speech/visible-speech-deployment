"""Tests for vispctl.doctor — project health checks."""

import os
from unittest.mock import patch

import pytest

from vispctl.doctor import _diagnose_project, parse_only_ids, run_doctor

# ── Helpers ────────────────────────────────────────────────────────────────────


def _fake_project(pid="proj123"):
    """Build a minimal MongoDB project document."""
    return {
        "id": pid,
        "name": "Test Project",
        "members": [{"username": "testuser_at_example_dot_com", "role": "admin"}],
        "sessions": [],
    }


# ── --only parsing ─────────────────────────────────────────────────────────────


def test_parse_only_ids_none():
    assert parse_only_ids(None) is None


def test_parse_only_ids_plain():
    assert parse_only_ids("a3f1,b2e4") == {"a3f1", "b2e4"}


def test_parse_only_ids_strips_whitespace():
    assert parse_only_ids(" a3f1 , b2e4 ") == {"a3f1", "b2e4"}


def test_parse_only_ids_drops_empty_parts():
    assert parse_only_ids("a3f1,,b2e4,") == {"a3f1", "b2e4"}


def test_parse_only_ids_blank_returns_empty_set():
    # A whitespace-only value selects no fixes — never "all fixes".
    assert parse_only_ids(" , ") == set()


# ── --apply without --fix ──────────────────────────────────────────────────────


def test_apply_without_fix_warns(capsys):
    with patch("vispctl.doctor.mongosh_json", return_value=[]):
        run_doctor(apply=True, fix=False)

    out = capsys.readouterr().out
    assert "--apply has no effect without --fix" in out


def test_apply_with_fix_does_not_warn(capsys):
    with patch("vispctl.doctor.mongosh_json", return_value=[]):
        run_doctor(apply=True, fix=True)

    out = capsys.readouterr().out
    assert "--apply has no effect" not in out


# ── Permission-denied project dirs ─────────────────────────────────────────────


def test_unreadable_session_dir_reported_as_issue(tmp_path):
    """A permission-denied session dir must be reported, not traceback."""
    if os.geteuid() == 0:
        pytest.skip("running as root — permission bits are ignored")

    repos = tmp_path / "repositories"
    db = repos / "proj123" / "Data" / "VISP_emuDB"
    session_dir = db / "Session_1_ses"
    session_dir.mkdir(parents=True)
    (db / "VISP_DBconfig.json").write_text("{}")
    session_dir.chmod(0o000)
    try:
        with patch("vispctl.doctor._get_repos_path", return_value=repos):
            report = _diagnose_project(_fake_project())
    finally:
        session_dir.chmod(0o755)

    assert any("Cannot fully scan project on disk" in i for i in report["issues"])
    assert any("PermissionError" in i for i in report["issues"])
    # The fix section relies on a complete scan and must be skipped
    assert report["fixes"] == []


def test_unreadable_emudb_dir_reported_as_issue(tmp_path):
    """A permission-denied emuDB dir must be reported, not traceback."""
    if os.geteuid() == 0:
        pytest.skip("running as root — permission bits are ignored")

    repos = tmp_path / "repositories"
    db = repos / "proj123" / "Data" / "VISP_emuDB"
    db.mkdir(parents=True)
    db.chmod(0o000)
    try:
        with patch("vispctl.doctor._get_repos_path", return_value=repos):
            report = _diagnose_project(_fake_project())
    finally:
        db.chmod(0o755)

    assert any("Cannot fully scan project on disk" in i for i in report["issues"])
    assert report["fixes"] == []


def test_scan_error_raised_by_helper_is_caught(tmp_path):
    """Any OSError from the scan helpers is caught and reported."""
    repos = tmp_path / "repositories"
    db = repos / "proj123" / "Data" / "VISP_emuDB"
    db.mkdir(parents=True)
    (db / "VISP_DBconfig.json").write_text("{}")

    with (
        patch("vispctl.doctor._get_repos_path", return_value=repos),
        patch("vispctl.doctor._disk_sessions", side_effect=PermissionError(13, "Permission denied")),
    ):
        report = _diagnose_project(_fake_project())

    assert any("Cannot fully scan project on disk (PermissionError)" in i for i in report["issues"])
    assert report["fixes"] == []


def test_readable_project_scans_normally(tmp_path):
    """A fully readable project produces no scan-error issues."""
    repos = tmp_path / "repositories"
    db = repos / "proj123" / "Data" / "VISP_emuDB"
    (db / "Session_1_ses" / "my_recording_bndl").mkdir(parents=True)
    (db / "VISP_DBconfig.json").write_text("{}")
    (db / "Session_1_ses" / "my_recording_bndl" / "my_recording.wav").write_bytes(b"RIFF")

    with patch("vispctl.doctor._get_repos_path", return_value=repos):
        report = _diagnose_project(_fake_project())

    assert not any("Cannot fully scan project on disk" in i for i in report["issues"])
    assert report["stats"]["audio_files"] == 1
